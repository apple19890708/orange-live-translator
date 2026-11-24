// YouTube 頁面：顯示翻譯字幕。
// 有日文字幕的影片 → 本機服務抓字幕整份翻譯，依播放時間顯示（路線 A）
// 直播或沒有字幕 → 顯示本機即時聽譯的結果（路線 B）
(() => {
  const S = {
    enabled: true,
    mode: "auto",        // auto / subs / live
    showSource: true,
    fontSize: 26,
    connected: false,
    videoId: null,
    cues: [],
    total: 0,
    subsState: "idle",   // idle / loading / ready / live / none / error
    subsMessage: "",
    live: new Map(),     // id → 事件
    capturing: false,    // 這個分頁正在被聽譯
  };
  let port = null;
  let box = null;

  // ---------- 連線 ----------
  function connectPort() {
    try {
      port = chrome.runtime.connect({ name: "lt" });
    } catch (_) {
      return; // 擴充功能被重新載入，舊的 content script 失效
    }
    port.onMessage.addListener(onMessage);
    port.onDisconnect.addListener(() => {
      port = null;
      setTimeout(() => { connectPort(); requestVideo(true); }, 1000);
    });
  }

  function post(msg) {
    if (port) try { port.postMessage(msg); } catch (_) {}
  }

  function onMessage(msg) {
    switch (msg.type) {
      case "conn":
        S.connected = msg.connected;
        if (msg.connected) requestVideo(true);
        break;
      case "capture":
        S.capturing = msg.on;
        if (msg.on) { S.live.clear(); reportTime(false, true); }
        break;
      case "video_cues":
        if (msg.video_id !== S.videoId) return;
        for (const c of msg.cues) S.cues.push(c);
        S.cues.sort((a, b) => a.start - b.start);
        S.total = msg.total;
        S.subsState = msg.done ? "ready" : "loading";
        break;
      case "video_error":
        if (msg.video_id !== S.videoId) return;
        S.subsState = msg.reason === "live" ? "live" : msg.reason === "no_subs" ? "none" : "error";
        S.subsMessage = msg.message || "";
        break;
      case "subtitle":
        if (msg.state === "drop" || msg.state === "retract") { S.live.delete(msg.id); break; }
        const cur = S.live.get(msg.id);
        if (!(cur && cur.state === "translated" && msg.state !== "translated")) S.live.set(msg.id, msg);
        while (S.live.size > 6) S.live.delete(S.live.keys().next().value);
        break;
    }
    render();
  }

  // ---------- 回報影片播放時間（依影片時間快取字幕：往回拉時間軸時顯示當時翻好的字幕） ----------
  let lastReport = 0;
  let hooked = null;
  function mainVideo() {
    return document.querySelector("#movie_player video") || document.querySelector("video");
  }
  function reportTime(seek = false, force = false) {
    if (!S.capturing || !S.connected || !S.videoId) return;
    const v = mainVideo();
    if (!v) return;
    const now = Date.now();
    if (!seek && !force && now - lastReport < 1000) return;
    lastReport = now;
    const player = document.querySelector("#movie_player");
    post({
      type: "media_time", video_id: S.videoId, t: v.currentTime, paused: v.paused,
      rate: v.playbackRate, ad: !!(player && player.classList.contains("ad-showing")), seek,
    });
  }
  function hookVideo() {
    const v = mainVideo();
    if (!v || v === hooked) return;
    hooked = v;
    v.addEventListener("seeking", () => { S.live.clear(); reportTime(true); render(); });
    v.addEventListener("seeked", () => reportTime(true));
    for (const ev of ["play", "pause", "ratechange"]) v.addEventListener(ev, () => reportTime(false, true));
  }

  // ---------- 影片切換 ----------
  function currentVideoId() {
    const u = new URL(location.href);
    if (u.pathname === "/watch") return u.searchParams.get("v");
    const m = u.pathname.match(/^\/(?:live|shorts)\/([A-Za-z0-9_-]{11})/);
    return m ? m[1] : null;
  }

  function requestVideo(force = false) {
    const id = currentVideoId();
    if (!force && id === S.videoId) return;
    if (id !== S.videoId) {
      S.videoId = id;
      S.cues = [];
      S.total = 0;
      S.live.clear();
      S.subsState = "idle";
      S.subsMessage = "";
    }
    if (!id || S.mode === "live" || !S.connected) return;
    if (S.subsState === "ready" || S.subsState === "live" || S.subsState === "none") return;
    S.subsState = "loading";
    post({ type: "load_video", video_id: id });
  }

  // ---------- 畫面 ----------
  function ensureBox() {
    const player = document.querySelector("#movie_player");
    if (!player) return null;
    if (box && box.parentElement === player) return box;
    box = document.createElement("div");
    box.className = "lt-box";
    box.innerHTML = '<div class="lt-lines"></div><div class="lt-status"></div>';
    player.appendChild(box);
    return box;
  }

  function findCue(t) {
    const cues = S.cues;
    let lo = 0, hi = cues.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid].start <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (ans >= 0 && t <= cues[ans].end + 0.3) return cues[ans];
    return null;
  }

  function useLive() {
    if (S.capturing || S.mode === "live") return true;
    if (S.mode === "subs") return false;
    return S.subsState === "live" || S.subsState === "none" || S.subsState === "error";
  }

  function line(src, tgt, cls = "") {
    const d = document.createElement("div");
    d.className = "lt-line " + cls;
    if (S.showSource && src) {
      const s = document.createElement("div");
      s.className = "lt-src";
      s.textContent = src;
      d.appendChild(s);
    }
    if (tgt) {
      const t = document.createElement("div");
      t.className = "lt-tgt";
      t.style.fontSize = S.fontSize + "px";
      t.textContent = tgt;
      d.appendChild(t);
    }
    return d;
  }

  let lastKey = "";
  function render() {
    const b = ensureBox();
    if (!b) return;
    // 本機程式沒開時完全不顯示（連線狀態可以在擴充功能的選單裡看到）
    b.style.display = S.enabled && S.videoId && S.connected ? "" : "none";
    if (!S.enabled || !S.connected) return;
    const linesEl = b.querySelector(".lt-lines");
    const statusEl = b.querySelector(".lt-status");
    const rows = [];
    let status = "";

    if (useLive()) {
      const items = [...S.live.values()].sort((a, b) => a.id - b.id);
      const finals = items.filter((e) => e.state !== "partial").slice(-2);
      const partial = items.filter((e) => e.state === "partial").slice(-1);
      for (const e of finals) rows.push([e.source, e.state === "translated" ? e.target : "…", ""]);
      for (const e of partial) rows.push([e.source, "", "lt-partial"]);
      // 只有正在聽譯這個分頁時才提示；沒聽譯就什麼都不顯示，不干擾看影片
      if (!items.length && S.capturing) status = "聽譯這個分頁中，等待說話…";
    } else {
      const v = document.querySelector("video");
      const cue = v ? findCue(v.currentTime) : null;
      if (cue) rows.push([cue.source, cue.target || "…", ""]);
      if (S.subsState === "loading") {
        status = S.total ? `翻譯字幕中 ${S.cues.length}/${S.total}` : "讀取字幕中…";
      }
    }

    const key = JSON.stringify([rows, status, S.showSource, S.fontSize]);
    if (key === lastKey) return;
    lastKey = key;
    linesEl.replaceChildren(...rows.map(([s, t, c]) => line(s, t, c)));
    statusEl.textContent = status;
    statusEl.style.display = status ? "" : "none";
  }

  // ---------- 設定 ----------
  async function loadSettings() {
    const st = await chrome.storage.local.get(["enabled", "mode", "showSource", "fontSize"]);
    S.enabled = st.enabled ?? true;
    S.mode = st.mode ?? "auto";
    S.showSource = st.showSource ?? true;
    S.fontSize = st.fontSize ?? 26;
  }

  chrome.storage.onChanged.addListener(async (_changes, area) => {
    if (area !== "local") return;
    const prevMode = S.mode;
    await loadSettings();
    if (prevMode !== S.mode) requestVideo(true);
    lastKey = "";
    render();
  });

  // ---------- 啟動 ----------
  (async () => {
    await loadSettings();
    connectPort();
    requestVideo();
    // YouTube 是單頁應用，換影片不會重新載入頁面
    document.addEventListener("yt-navigate-finish", () => requestVideo());
    setInterval(() => { requestVideo(); hookVideo(); reportTime(); render(); }, 250);
  })();
})();

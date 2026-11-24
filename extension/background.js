// 背景：維持與本機服務的 WebSocket，轉送訊息給各分頁的 content script。
// 由擴充功能（chrome-extension:// 來源）連線，本機服務只接受這種來源，一般網頁連不進來。

const DEFAULT_URL = "ws://127.0.0.1:8765";
let ws = null;
let connected = false;
let lastStatus = null;
const ports = new Set();
let retryMs = 1000;

async function serverUrl() {
  const { serverUrl } = await chrome.storage.local.get("serverUrl");
  return serverUrl || DEFAULT_URL;
}

function broadcast(msg) {
  for (const p of ports) {
    try { p.postMessage(msg); } catch (_) { ports.delete(p); }
  }
}

async function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(await serverUrl());
  } catch (e) {
    scheduleReconnect();
    return;
  }
  ws.onopen = async () => {
    connected = true;
    retryMs = 1000;
    // 本機服務重開後，套用上次在 popup 選的語言
    const { lang } = await chrome.storage.local.get("lang");
    if (lang) send({ type: "set_language", lang });
    broadcast({ type: "conn", connected: true });
  };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.type === "status") lastStatus = msg;
    broadcast(msg);
  };
  ws.onclose = () => {
    connected = false;
    broadcast({ type: "conn", connected: false });
    scheduleReconnect();
  };
  ws.onerror = () => {};
}

function scheduleReconnect() {
  if (ports.size === 0) return; // 沒有開 YouTube 分頁就不重連
  setTimeout(connect, retryMs);
  retryMs = Math.min(retryMs * 2, 15000);
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "lt") return;
  ports.add(port);
  port.postMessage({ type: "conn", connected });
  if (lastStatus) port.postMessage(lastStatus);
  if (port.sender && port.sender.tab && port.sender.tab.id === captureTabId) {
    port.postMessage({ type: "capture", on: true });
  }
  connect();
  port.onMessage.addListener((msg) => {
    // 影片播放時間只收正在聽譯的那個分頁（其他分頁的影片和聽到的聲音無關）
    if (msg && msg.type === "media_time" && !(port.sender && port.sender.tab && port.sender.tab.id === captureTabId)) return;
    send(msg);
  });
  port.onDisconnect.addListener(() => ports.delete(port));
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "send") {
    if (msg.msg && msg.msg.type === "set_language") chrome.storage.local.set({ lang: msg.msg.lang });
    connect();
    send(msg.msg);
    reply({ ok: true });
  } else if (msg.type === "getState") {
    reply({ connected, status: lastStatus });
  } else if (msg.type === "reconnect") {
    if (ws) try { ws.close(); } catch (_) {}
    ws = null;
    connect();
    reply({ ok: true });
  }
  return false;
});

// ---------- 分頁聽譯 ----------
let captureTabId = null;

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "擷取分頁聲音送到本機語音辨識",
  });
}

function notifyTab(tabId, on) {
  for (const p of ports) {
    if (p.sender && p.sender.tab && p.sender.tab.id === tabId) {
      try { p.postMessage({ type: "capture", on }); } catch (_) {}
    }
  }
}

async function setCaptureState(tabId) {
  const prev = captureTabId;
  captureTabId = tabId;
  await chrome.storage.session.set({ captureTabId: tabId });
  if (prev !== null && prev !== tabId) notifyTab(prev, false);
  if (tabId !== null) notifyTab(tabId, true);
  chrome.action.setBadgeText({ text: tabId !== null ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#c0392b" });
}

// streamId 必須在使用者操作時取得（popup 按鈕或快捷鍵）
async function startCapture(tabId, streamId) {
  await stopCapture();
  await ensureOffscreen();
  const tab = await chrome.tabs.get(tabId);
  const res = await chrome.runtime.sendMessage({
    target: "offscreen", type: "start", streamId,
    serverUrl: await serverUrl(), title: tab.title || "",
  });
  if (!res || !res.ok) throw new Error((res && res.error) || "無法開始擷取");
  await setCaptureState(tabId);
}

async function stopCapture() {
  if (await chrome.offscreen.hasDocument()) {
    await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" });
  }
  await setCaptureState(null);
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === captureTabId) stopCapture();
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.target === "offscreen") return false;
  if (msg.type === "startCapture") {
    startCapture(msg.tabId, msg.streamId).then(
      () => reply({ ok: true }),
      (e) => reply({ ok: false, error: String(e && e.message || e) })
    );
    return true;
  }
  if (msg.type === "stopCapture") {
    stopCapture().then(() => reply({ ok: true }));
    return true;
  }
  if (msg.type === "getCapture") {
    reply({ tabId: captureTabId });
    return false;
  }
  if (msg.type === "captureEnded") {
    setCaptureState(null);
    return false;
  }
  return false;
});

// 背景 service worker 可能被回收，重新啟動時恢復狀態
chrome.storage.session.get("captureTabId").then(({ captureTabId: id }) => {
  if (id != null) captureTabId = id;
});

chrome.commands.onCommand.addListener(async (cmd, tab) => {
  if (cmd === "toggle-overlay") {
    const { enabled = true } = await chrome.storage.local.get("enabled");
    await chrome.storage.local.set({ enabled: !enabled });
  } else if (cmd === "toggle-capture" && tab) {
    if (captureTabId === tab.id) {
      await stopCapture();
    } else {
      try {
        const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
        await startCapture(tab.id, streamId);
      } catch (e) {
        console.warn("toggle-capture failed", e);
      }
    }
  }
});

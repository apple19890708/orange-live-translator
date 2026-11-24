const $ = (id) => document.getElementById(id);

async function init() {
  const st = await chrome.storage.local.get(["enabled", "mode", "showSource", "fontSize", "serverUrl"]);
  $("enabled").checked = st.enabled ?? true;
  $("mode").value = st.mode ?? "auto";
  $("showSource").checked = st.showSource ?? true;
  $("fontSize").value = st.fontSize ?? 26;
  $("serverUrl").value = st.serverUrl ?? "ws://127.0.0.1:8765";

  $("enabled").onchange = (e) => chrome.storage.local.set({ enabled: e.target.checked });
  $("mode").onchange = (e) => chrome.storage.local.set({ mode: e.target.value });
  $("showSource").onchange = (e) => chrome.storage.local.set({ showSource: e.target.checked });
  $("fontSize").onchange = (e) => chrome.storage.local.set({ fontSize: Number(e.target.value) || 26 });
  $("serverUrl").onchange = (e) => chrome.storage.local.set({ serverUrl: e.target.value.trim() });
  $("reconnect").onclick = () => chrome.runtime.sendMessage({ type: "reconnect" }, refresh);
  $("capture").onclick = toggleCapture;
  $("lang").onchange = (e) => chrome.runtime.sendMessage({ type: "send", msg: { type: "set_language", lang: e.target.value } });
  refresh();
  setInterval(refresh, 1500);
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function toggleCapture() {
  const btn = $("capture");
  btn.disabled = true;
  try {
    const tab = await activeTab();
    const { tabId } = await chrome.runtime.sendMessage({ type: "getCapture" });
    if (tab && tabId === tab.id) {
      await chrome.runtime.sendMessage({ type: "stopCapture" });
    } else if (tab) {
      // 必須在 popup 的按鈕點擊中取得 streamId
      const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
      const res = await chrome.runtime.sendMessage({ type: "startCapture", tabId: tab.id, streamId });
      if (!res.ok) $("captureInfo").textContent = "失敗：" + res.error;
    }
  } catch (e) {
    $("captureInfo").textContent = "失敗：" + (e.message || e);
  }
  btn.disabled = false;
  refresh();
}

async function refreshCapture() {
  const tab = await activeTab();
  const { tabId } = await chrome.runtime.sendMessage({ type: "getCapture" });
  const btn = $("capture");
  const mine = tab && tabId === tab.id;
  btn.textContent = mine ? "停止聽譯" : tabId != null ? "改聽譯這個分頁" : "聽譯這個分頁";
  btn.className = "big" + (mine ? " on" : "");
  if (!$("captureInfo").textContent.startsWith("失敗")) {
    $("captureInfo").textContent = mine ? "正在聽譯這個分頁（Alt+Shift+L 停止）"
      : tabId != null ? "正在聽譯其他分頁" : "快捷鍵 Alt+Shift+L";
  }
}

function refresh() {
  refreshCapture();
  chrome.runtime.sendMessage({ type: "getState" }, (s) => {
    if (!s) return;
    $("dot").className = "dot" + (s.connected ? " on" : "");
    $("conn").textContent = s.connected ? "已連線本機服務" : "未連線（請先開啟 Orange Live Translate）";
    if (s.status && s.status.language && document.activeElement !== $("lang")) {
      $("lang").value = s.status.language;
    }
    if (s.status) {
      $("models").textContent = `辨識 ${s.status.asr_model}／翻譯 ${s.status.translate_model}` +
        (s.status.live ? "，聽譯中" : "，聽譯未啟動");
    }
  });
}

init();

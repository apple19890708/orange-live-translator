// 隱藏的 offscreen 文件：取得分頁聲音，一路照常播放給你聽，一路轉 16kHz 送到本機服務。
let stream = null;
let playCtx = null;
let capCtx = null;
let ws = null;

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.target !== "offscreen") return false;
  if (msg.type === "start") {
    start(msg.streamId, msg.serverUrl, msg.title).then(
      () => reply({ ok: true }),
      (e) => { stop(); reply({ ok: false, error: String(e && e.message || e) }); }
    );
    return true;
  }
  if (msg.type === "stop") {
    stop();
    reply({ ok: true });
  }
  return false;
});

function openSocket(url, title) {
  return new Promise((resolve, reject) => {
    const s = new WebSocket(url);
    s.binaryType = "arraybuffer";
    s.onopen = () => {
      s.send(JSON.stringify({ type: "capture", on: true, title }));
      resolve(s);
    };
    s.onerror = () => reject(new Error("連不到本機服務，請先執行 run.bat"));
    s.onclose = () => {
      if (ws === s) {
        // 本機服務關掉了：停止擷取並通知背景
        stop();
        chrome.runtime.sendMessage({ type: "captureEnded", reason: "server" });
      }
    };
  });
}

async function start(streamId, serverUrl, title) {
  stop();
  ws = await openSocket(serverUrl, title);
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });
  // 擷取分頁聲音後 Chrome 會把分頁靜音，這裡把聲音接回喇叭
  playCtx = new AudioContext();
  playCtx.createMediaStreamSource(stream).connect(playCtx.destination);

  // 16kHz 的 context：Chrome 會自動重新取樣
  capCtx = new AudioContext({ sampleRate: 16000 });
  await capCtx.audioWorklet.addModule("pcm-worklet.js");
  const node = new AudioWorkletNode(capCtx, "pcm-sender");
  node.port.onmessage = (e) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };
  capCtx.createMediaStreamSource(stream).connect(node);
  // worklet 要接到輸出才會被驅動；用音量 0 避免重複播放
  const mute = capCtx.createGain();
  mute.gain.value = 0;
  node.connect(mute).connect(capCtx.destination);

  stream.getAudioTracks()[0].addEventListener("ended", () => {
    stop();
    chrome.runtime.sendMessage({ type: "captureEnded", reason: "tab" });
  });
}

function stop() {
  const s = ws;
  ws = null;
  if (s) {
    try { if (s.readyState === WebSocket.OPEN) s.send(JSON.stringify({ type: "capture", on: false })); } catch (_) {}
    try { s.close(); } catch (_) {}
  }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  if (playCtx) { playCtx.close(); playCtx = null; }
  if (capCtx) { capCtx.close(); capCtx = null; }
}

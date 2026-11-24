// 在 16kHz 的 AudioContext 裡執行：把聲音轉成 Int16，每 100ms（1600 個取樣）送一包給主執行緒。
class PcmSender extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(1600);
    this.n = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const ch0 = input[0];
    const ch1 = input[1];
    for (let i = 0; i < ch0.length; i++) {
      let v = ch1 ? (ch0[i] + ch1[i]) / 2 : ch0[i];
      v = Math.max(-1, Math.min(1, v));
      this.buf[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.buffer, [this.buf.buffer]);
        this.buf = new Int16Array(1600);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-sender", PcmSender);

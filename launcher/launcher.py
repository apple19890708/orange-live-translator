"""OrangeLiveTranslate.exe：雙擊開啟即時翻譯；已經在執行時，雙擊就關閉。

只用標準函式庫，打包出來很小。實際程式仍然用專案裡 .venv 的 Python 執行，
所以改程式碼不用重新打包 exe。
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

APP_TITLE = "即時翻譯字幕"
PORT = 8765


def root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = root_dir()
LOG_DIR = ROOT / "logs"
PID_FILE = LOG_DIR / "app.pid"
APP_LOG = LOG_DIR / "app.log"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def toast(text: str, ms: int = 2000, error: bool = False) -> None:
    """會自動消失的訊息框（MessageBoxTimeoutW）。"""
    flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
    flags |= 0x40000  # MB_TOPMOST
    try:
        fn = user32.MessageBoxTimeoutW
        fn.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
                       wintypes.WORD, wintypes.DWORD]
        fn(None, text, APP_TITLE, flags, 0, 0 if error else ms)
    except Exception:
        user32.MessageBoxW(None, text, APP_TITLE, flags)


def process_image(pid: int) -> str:
    """回傳執行檔路徑；行程不存在則回傳空字串。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
            return ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return "?"
    finally:
        kernel32.CloseHandle(h)


def running_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    image = process_image(pid).lower()
    # 防止 PID 被別的程式重複使用而誤關
    if image and ("python" in image or image == "?"):
        return pid
    return None


def port_in_use() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def unload_models() -> int:
    """請 Ollama 把本程式用到的翻譯模型移出顯示記憶體（Ollama 預設會常駐 30 分鐘）。"""
    import json
    import urllib.request

    models_file = LOG_DIR / "models.json"
    try:
        info = json.loads(models_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    base = str(info.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    n = 0
    for model in info.get("models") or []:
        req = urllib.request.Request(
            f"{base}/api/generate",
            data=json.dumps({"model": model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
            n += 1
        except Exception:
            pass
    try:
        models_file.unlink()
    except OSError:
        pass
    return n


def request_graceful_exit(pid: int, wait: float = 8.0) -> bool:
    """先請程式自己正常關閉（寫完字幕紀錄、釋放模型），關不掉才強制結束。"""
    import time
    import urllib.request

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/shutdown", timeout=2).read()
    except Exception:
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if not process_image(pid):
            return True
        time.sleep(0.2)
    return False


def stop(pid: int) -> None:
    if not request_graceful_exit(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    unload_models()
    toast("已關閉即時翻譯，並釋放顯示記憶體")


def start() -> None:
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        toast(f"找不到 {pythonw}\n請先依 README 建立 .venv。", error=True)
        return
    if port_in_use():
        toast(f"連接埠 {PORT} 已被使用。\n可能已經用 run.bat 開著，請先關掉那個視窗。", error=True)
        return
    LOG_DIR.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    log = open(APP_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(pythonw), "-m", "live_translator"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    PID_FILE.write_text(str(proc.pid))
    toast("即時翻譯啟動中…\n字幕視窗會出現在螢幕下方，載入模型約 10 秒。\n再點一次 OrangeLiveTranslate.exe 即可關閉。", ms=3500)


def main() -> None:
    # 同一時間只處理一次點擊：連點兩下時，第二下不要把剛開的程式又關掉
    kernel32.CreateMutexW(None, False, "Local\\LiveTranslatorLauncher")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    pid = running_pid()
    if pid:
        stop(pid)
    else:
        start()


if __name__ == "__main__":
    main()

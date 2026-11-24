# Orange Live Translate

> 資料夾與程式套件仍叫 `live-translator`／`live_translator`（只改了顯示名稱）。

本機 GPU 即時日文／英文翻中字幕。語音辨識（Whisper）和翻譯（Ollama 本機模型）都在自己的顯示卡上跑，不送資料到外部、不用錢。設計見 [PLAN.md](PLAN.md)。

## 需求

- Windows 10/11、NVIDIA 顯示卡（開發環境：RTX 5070 Ti 16GB）
- Python 3.12（`py -3.12`）
- [Ollama](https://ollama.com)，並下載翻譯模型：
  - `ollama pull hf.co/SakuraLLM/Sakura-GalTransl-7B-v3.7:IQ4_XS`（日翻中專用）
  - `ollama pull hf.co/tencent/Hy-MT2-1.8B-GGUF:Q8_0`（英文、日英夾雜及其他，約 1.9GB；騰訊官方 GGUF）

## 安裝

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

第一次執行會自動下載 Whisper 模型（large-v3 約 3GB）。

## 使用

### 開啟／關閉

雙擊 **`OrangeLiveTranslate.exe`** 開啟；已經開著時再雙擊一次就關閉。（也可以從系統匣「譯」圖示選「結束」。）
執行紀錄在 `logs/app.log`。

### 聽譯網頁影片（預設）

1. 安裝 Chrome 擴充功能（只要做一次）：開 `chrome://extensions` → 打開右上角「開發人員模式」→「載入未封裝項目」→ 選本專案的 `extension` 資料夾。
2. 開啟 `OrangeLiveTranslate.exe`。
3. 在要聽譯的影片分頁，按工具列的擴充功能圖示 →「**聽譯這個分頁**」（或按 `Alt+Shift+L`）。
4. 只會聽這個分頁的聲音，遊戲、Discord 等其他聲音不會被收進來。再按一次就停止。

### 切換辨識模型（Whisper／anime-whisper）

字幕框右鍵或系統匣 →「辨識模型」，執行中就能切換（舊模型會先釋放，第一次切到 anime-whisper 約等 10 秒）。選擇會記住，下次開啟沿用。

| | Whisper large-v3（預設） | anime-whisper |
|---|---|---|
| 擅長 | 一般說話、公告、專有名詞、英文 | 動畫腔、情緒化口語、笑聲喘氣 |
| 白玖ウタノ公告段落錯誤率 | 7.2% | 12.5% |
| 注意 | | 只會日文；會把咳嗽寫成「コホン」；句尾「。」常省略 |

### 切換語言（日文／英文）

- 擴充功能選單的「聽譯語言」，或桌面字幕視窗右鍵 →「聽譯語言」。
- **自動**：每句自動判斷日文或英文。日文句子夾雜英文時，會改用通用模型翻譯（日翻中專用的 Sakura 會把英文原封不動留著）。**日文**／**英文**：鎖定只聽該語言，判斷不準或混語時用。
- 兩邊選單會同步，關掉重開也會沿用擴充功能上次的選擇。

### 字幕顯示在哪裡

- **桌面字幕視窗**：任何網站都會顯示。**滑鼠滾輪往上捲**可看舊字幕（30 秒沒動或捲回底部就回到即時）；**雙擊字幕框**開「字幕紀錄」視窗，列出全部字幕和時間，可選取複製。左鍵拖曳移動；右鍵或系統匣圖示可暫停、滑鼠穿透、顯示／隱藏原文、切換語言、結束。灰色斜體是辨識中的暫定文字，白色粗體是譯文。
- **YouTube 影片上**：擴充功能直接把字幕疊在播放器上，`Alt+T` 開關。
  - 有官方繁中字幕的影片 → 直接用。
  - 有日文／英文字幕的影片（沒按聽譯時）→ 抓字幕整份翻譯，依播放時間顯示，快取在 `cache/subs`。
  - 按了「聽譯這個分頁」→ 一律顯示即時聽譯結果。
- 只想看 YouTube 上的字幕、不要桌面視窗：`config.yaml` 裡設 `overlay.enabled: false`。

### 直播後精聽

直播結束後，用自動存下的錄音重新仔細辨識一次，產出比即時字幕更準的雙語字幕：

```powershell
.un.bat refine logs\live-20261123-180716.pcm
```

- 輸出 `*.refined.srt`（精聽雙語字幕）與 `*.refined.md`（和即時字幕逐字比對，列出兩邊聽成不同字的地方）。
- 辨識錯誤率約為即時模式的 2/3（4.5% vs 6.6%）；7 分鐘錄音約 20 秒處理完。
- 比對報告是「可能聽錯」的候選：精聽也會錯，重複出現的才適合加進 `glossary.yaml` 的修正清單。

### 指令（進階）

| 指令 | 用途 |
|---|---|
| `run.bat` | 和 exe 相同，但開在終端機、看得到訊息 |
| `run.bat --source system` | 改成聽整台電腦的聲音（不用擴充功能） |
| `run.bat --no-overlay` | 只在終端機顯示字幕 |
| `run.bat --file 音檔.mp3` | 用音檔模擬即時聽譯（測試用） |
| `run.bat subs <YouTube網址>` | 抓影片字幕整份翻譯，輸出雙語 SRT |
| `run.bat refine 錄音.pcm` | 直播後精聽（見上） |
| `run.bat check` | 檢查 GPU、Whisper、Ollama 是否正常 |
| `run.bat bench qwen3:8b qwen3:14b` | 比較翻譯模型的速度和譯文 |
| `run.bat devices` | 列出可擷取的音訊裝置 |

重新產生 exe（只有改 `launcher/launcher.py` 才需要）：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --noconsole --name OrangeLiveTranslate --icon D:/program/live-translator/launcher/icon.ico --distpath dist --workpath build --specpath build launcher/launcher.py
```

## 設定

複製 `config.example.yaml` 成 `config.yaml` 修改，只要寫想改的項目。常改的：

- `audio.source`：`tab`（預設，只聽擴充功能指定的分頁）／`system`（整台電腦）
- `asr.language`：`auto`／`ja`／`en`
- `asr.model`：`large-v3`（預設，日文最準）／`large-v3-turbo`（快約 3 倍，但日文較常聽錯）
- `asr.hotwords`：辨識提示詞（人名、活動、專有名詞），讓 Whisper 優先聽成這些詞；詞彙表的日文詞會自動加入
- 評分辨識設定（需要參考逐字稿）：`.\.venv\Scripts\python.exe scripts\eval_asr.py 音檔 參考稿.txt --set prompt_chars=60 --set prompt_chars=150`，會印出每組設定的字元錯誤率
- 每場聽譯的聲音存在 `logs/live-*.pcm`（和字幕紀錄同名），可以直接當上面工具的音檔
- 比較辨識模型：`.\.venv\Scripts\python.exe scripts\compare_asr.py 音檔 --start 0 --end 90`，結果在 `docs/asr-compare.md`
- `translate.models`：依語言指定模型，預設日文用 Sakura、英文用 `translate.model`（Hy-MT2-1.8B）
- `asr.incomplete_hold_ms`：句子像沒講完（〜て、〜けど、and…）時多等多久再切句，預設 1000
- 比較翻譯模型：`.\.venv\Scripts\python.exe scripts\compare_models.py logs\live-xxx.srt`，結果在 `docs/translation-compare.md`
- `translate.provider: openai` + `base_url` + `api_key_env`：改用 OpenRouter 等雲端 API
- `overlay.show_source`、`font_size`、`lines`：字幕外觀

詞彙表：複製 `glossary.example.yaml` 成 `glossary.yaml`，加主播名字、梗的固定譯法。

辨識結果會依「。！？」重新斷句（`asr.resegment`），長句會切在句子結束處（`asr.sentence_cut`）。

`glossary.yaml` 有兩個區塊：

- `terms`（原文: 譯文）：固定譯法，日文詞也會提示語音辨識優先聽成這個詞。
- `corrections`（聽錯的寫法: 正確原文）：辨識完立刻替換，再顯示和翻譯。例如字幕出現「ウタノライブ イン タイ」（被翻成泰國），就加 `イン タイ: イン台北`。左邊照字幕上看到的錯字寫。

即時字幕會存成 `logs/live-*.srt`。

## 結構

```
src/live_translator/
  audio/       WASAPI loopback 擷取、音檔模擬
  asr/         Silero VAD 切句、Whisper 串流辨識、幻聽過濾
  translate/   翻譯介面、Ollama／OpenAI 相容實作、OpenCC 簡轉繁、詞彙表
  subtitles/   YouTube 字幕（yt-dlp）、json3／SRT
  server/      WebSocket（只聽 127.0.0.1、只接受擴充功能來源）
  overlay/     PySide6 浮動字幕視窗
extension/     Chrome 擴充功能（MV3）
```

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest
```

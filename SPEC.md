# Orange Live Translate 規格書

> 版本：2026-11-23　｜　適用程式碼：`src/live_translator`、`extension/`、`launcher/`
> 這份文件說明「系統現在是什麼樣子」。開發經過與實測紀錄見 [PLAN.md](PLAN.md)，使用方式見 [README.md](README.md)。

---

## 1. 目的與範圍

在自己的 Windows 電腦上，把 YouTube 等網頁影片／直播的日文（與英文）即時翻成**台灣繁體中文字幕**。

- **完全本機、免費**：語音辨識和翻譯都在自己的顯示卡上跑，聲音不送出電腦。
- **兩條路線**
  - **即時聽譯**：擷取聲音 → 辨識 → 翻譯 → 字幕（直播、沒有字幕的影片）。
  - **字幕翻譯**：影片本身有字幕時，直接抓字幕檔整份翻譯（不經語音辨識）。
- **不做**：聊天室翻譯、帳號、雲端服務、商業用途。

## 2. 執行環境

| 項目 | 規格 |
|---|---|
| 作業系統 | Windows 10/11 |
| 顯示卡 | NVIDIA，開發機 RTX 5070 Ti 16GB（Blackwell） |
| Python | 3.12（專案內 `.venv`） |
| 本機 LLM 服務 | Ollama 0.40（`127.0.0.1:11434`） |
| 瀏覽器 | Chrome（擴充功能 Manifest V3） |

## 3. 系統架構

```
            ┌────────── Chrome ──────────┐
 YouTube 分頁 ─ tabCapture ─ offscreen.js ─┐    content.js（字幕疊在影片上）
            └───────────────────────────│────────────▲───────────┘
                     16kHz PCM（二進位）│            │ 字幕事件（JSON）
                                        ▼            │
  ┌──────────────── 本機服務（Python，單一程序）──────────────────────┐
  │ 聲音來源 ── VAD 切句 ── 語音辨識 ── 修正清單 ── 重新斷句 ── 翻譯 ──┐ │
  │ (分頁/系統)  (Silero)  (Whisper等)  (glossary)  (segmenter) (LLM) │ │
  │                                                                 ▼ │
  │        WebSocket 127.0.0.1:8765 ◄── 事件匯流排 ──► 浮動字幕視窗     │
  │                                          └──► SRT 紀錄／錄音 .pcm │
  └──────────────────────────────────────────────────────────────────┘
            ▲ 開／關：OrangeLiveTranslate.exe（呼叫 /shutdown 正常關閉）
```

所有模組在同一個 Python 程序內，以執行緒分工：

| 執行緒 | 工作 |
|---|---|
| 主執行緒 | Qt 事件迴圈（浮動視窗、系統匣） |
| `asr` | 讀聲音佇列 → VAD → 辨識 → 發出事件 |
| `translate` | 翻譯佇列 → LLM → 發出譯文事件 |
| `ws` | asyncio WebSocket 伺服器 |
| `subs`（執行緒池） | YouTube 字幕下載與整份翻譯，一次一部 |

## 4. 使用的技術

| 層 | 技術 | 版本／模型 | 用途 |
|---|---|---|---|
| 聲音擷取 | Chrome `tabCapture` + `offscreen` + AudioWorklet | MV3 | 只擷取指定分頁，轉 16kHz 單聲道 Int16，每 100ms 一包 |
| 聲音擷取（備選） | WASAPI loopback（PyAudioWPatch）＋ soxr 重新取樣 | 0.2.12 / 1.1 | 擷取整台電腦的聲音 |
| 人聲偵測 | Silero VAD（faster-whisper 內建 ONNX，CPU） | — | 判斷有沒有人在說話、切句 |
| 語音辨識（預設） | faster-whisper / CTranslate2 | 1.2.1 / 4.9.1，Whisper **large-v3**，int8_float16 | 日文、英文辨識 |
| 語音辨識（可切換） | transformers + PyTorch | 5.24 / 2.11+cu128，**litagin/anime-whisper** | 動畫腔日文 |
| 語音辨識（實驗） | sherpa-onnx | 2.0.3，ReazonSpeech k2-v2 | 日文專有名詞較準，但會漏句；預設不用 |
| 翻譯（日文） | Ollama | **Sakura-GalTransl-7B v3.7**（IQ4_XS，4.3GB） | 日翻中專用 |
| 翻譯（英文、日英夾雜） | Ollama | 騰訊 **Hy-MT2-1.8B**（官方 Q8_0 GGUF，顯示記憶體 2.1GB，`hymt.py` 用 raw 模式） | 翻譯專用；英文 chrF 36.7，和 qwen3:8b（36.5，5.2GB）持平但省 3GB（E66） |
| 翻譯（可切換） | Ollama | 騰訊 **Hy-MT2-7B**（Q4_K_M，4.6GB，`hymt.py`） | 翻譯專用；實測 chrF 24.0～24.4，低於 Sakura（E45），預設不用 |
| 翻譯（備選） | OpenAI 相容 API | 任意 | 可改用 OpenRouter 等雲端 |
| 簡轉繁 | OpenCC（`s2twp`）＋自訂修正 | opencc-python-reimplemented 0.1.7 | 台灣正體與用語 |
| 字幕檔 | yt-dlp（json3） | 2026.10 | 抓 YouTube 官方／自動字幕 |
| 介面 | PySide6 | 6.12 | 浮動字幕視窗、系統匣、字幕紀錄視窗 |
| 通訊 | websockets | 17.4 | 本機服務 ↔ 擴充功能 |
| 開關程式 | PyInstaller 單檔 exe（僅標準函式庫） | 6.22 | `OrangeLiveTranslate.exe` |
| 解碼音檔 | PyAV | 19 | 測試用音檔、錄音回放 |

## 5. 處理流程（即時聽譯）

### 5.1 聲音來源（`audio/`）

- `tab_source.py`：接收擴充功能送來的 PCM（預設）。
- `capture.py`：WASAPI loopback（`audio.source: system`）。
- `file_source.py`：以實際速度播放音檔或錄音 `.pcm`（測試用）。

### 5.2 切句與辨識（`asr/streamer.py`）

以 100ms 為單位處理，規則依序判斷：

| 規則 | 預設值 | 說明 |
|---|---|---|
| 開始說話 | VAD 機率 ≥ 0.5 | 往前補 0.3 秒，避免吃掉第一個字 |
| 一句結束 | 靜音 ≥ 600ms | |
| 句子沒講完再等 | 最多再等 1000ms | 結尾是「〜て」「〜けど」「〜のね」、英文 and/but… |
| 長句找句號切 | 超過 6 秒後每 1.5 秒 | 快速辨識找「。！？」且後面停頓 ≥0.12 秒，切在句尾 |
| 長句換氣切 | 超過 6 秒＋停頓 250ms | 快速辨識確認句子講完才切 |
| 強制切 | 12 秒 | 切在後半段最安靜處，剩下的接到下一句 |
| 太短忽略 | < 300ms | |
| 合併重聽 | 關閉（`merge_gap_s: 0`） | 實測整體變差，保留為選項 |

辨識設定：

- **暫定結果**：說話中每 0.8 秒、beam 1。**定稿**：beam 5，溫度 0→0.2→0.4 重試。
- **前文提示**：一句有標點的範例句＋最近定稿的 60 字（範例句讓 Whisper 輸出「、。！？」）。
- **提示詞（hotwords）**：`asr.hotwords` 與詞彙表裡的日文詞。
- **語言**：`auto` 時只在日文／英文中判斷，把握不足沿用上一句；也可手動鎖定。
- **幻聽過濾**：常見結尾字幕（ご視聴ありがとうございました、次回予告…）、純符號、重複字縮短。
- 解碼參數可調（`asr.temperatures`、`compression_ratio_threshold`、`log_prob_threshold`、`repetition_penalty`、`normalize_dbfs`），預設值是實測後最好的組合，見 `docs/jt-live-whisper-比較.md`。
- 辨識引擎統一介面：`transcribe()`、`detect()`、`set_language()`、`add_hotwords()`、`close()`；可在執行中切換（`build_engine()`）。

### 5.3 辨識後處理

1. **修正清單**（`glossary.yaml` 的 `corrections`）：固定會聽錯的寫法 → 正確原文，長的優先。
2. **重新斷句**（`segmenter.py`）：一段多句依「。！？」拆成多行；結尾沒有句號的短碎片（≤8 字）最多等 2.5 秒接到下一句。

### 5.4 翻譯（`translate/`）

| 元件 | 說明 |
|---|---|
| `router.py` | 純日文 → Sakura；含英文單字或主要是英文 → Hy-MT2-1.8B；逾時自動改用另一個模型 |
| `sakura.py` | 官方 v3 模板（歷史譯文＋術語表＋原文），溫度 0.1 |
| `base.py` | 通用提示詞（台灣字幕組語氣、口頭禪可省略、夾雜英文也要翻） |
| `worker.py` | 帶前 4 句原文／譯文當前文（不放和這句相同的原文）；積壓時同語言最多 3 句合併翻；收回的句子不翻；翻譯記憶（同一句原文沿用第一次的譯文，`translate.memory_size`）預設關閉（拉低翻譯分數，E43） |
| `quality.py` | 檢查壞掉的譯文：吐出提示詞、殘留假名、原文沒有英文卻冒出羅馬拼音 → 先不帶前文重翻，再換另一個模型；壞譯文不放進前文與記憶 |
| `postprocess.py` | 去除思考標籤與多餘前綴 → OpenCC s2twp → 修正（釋出會→發表會、隻有→只有、幹擾→干擾、臺→台）→ 詞彙表強制替換 |

- **口頭禪**（`fillers.py`）：送翻譯前去掉日文的「えー、えっと、あのー」等；翻譯後去掉獨立的「呃、嗯、那個」並把「——」改成逗號；整句都是口頭禪就不翻也不顯示。
- **詞彙表只放這句真的出現的詞**（全部放會被亂套）。
- 安全機制：輸出長度上限（`num_predict`）、請求逾時 45 秒。

### 5.4.1 依影片時間快取字幕（`replay.py`）

- Chrome 分頁聽譯時，`content.js` 每秒（以及拉時間軸、播放、暫停、變速時）送出 `media_time`（影片 ID、播放時間、是否暫停、速度、是否廣告）；`background.js` 只轉送正在聽譯的分頁。
- 伺服器把回報對上「當時已收到的聲音秒數」，把字幕的時間換成影片時間；翻好的句子依影片時間存起來。
- 往回拉到已翻過的範圍：一句有 ≥50%（`replay.min_cover`）落在已翻過範圍內就不重新翻譯、辨識中的字也不顯示；播到該時間點時送出當時的字幕（新的 id，狀態 `translated`）。
- 句子中間拉過時間軸、換影片、廣告中：時間不可靠，不存也不擋。
- 只存在記憶體，程式關閉就清空；系統聲音、音檔來源沒有影片時間，不受影響。

### 5.5 輸出

- **浮動字幕視窗**（`overlay/window.py`）：置頂半透明；滾輪往回看、雙擊開完整紀錄；右鍵選單切換語言、辨識模型、暫停、滑鼠穿透。
- **YouTube 疊字幕**（`extension/content.js`）：即時聽譯結果或整份字幕翻譯。
- **SRT 紀錄**（`logs/live-時間.srt`）：雙語，最後 3 句確定不再變才寫入。
- **錄音**（`logs/live-時間.pcm`，16kHz/16bit/單聲道）：與 SRT 同名、時間軸一致，保留最近 3 場。

## 6. 字幕翻譯路線（`subtitles/`）

1. yt-dlp 取得字幕：繁中官方字幕 > 影片原始語言官方字幕 > 原始語言自動字幕。
2. 自動字幕過碎時合併（日文 ≤40 字、英文 ≤90 字）。
3. 每 12 行一批翻譯（`編號|譯文` 格式），帶前 4 行前文；解析失敗的行逐句重翻。
4. 結果快取在 `cache/subs/<影片ID>.<模型>.json`；直播中或沒有字幕時改用即時聽譯。

## 6.5 直播後精聽（`refine.py`）

- 對錄音整段離線辨識：Whisper large-v3、VAD、30 秒視窗、沿用前文、beam 5；**不用**提示詞與範例句（離線會引發幻聽）。
- 片段結尾時間以「每字 0.2 秒＋每句 1 秒」為上限，避免一句橫跨長靜音。
- 套用修正清單 → 依句號斷句 → 去口頭禪 → 翻譯（同即時流程）。
- 輸出 `*.refined.srt` 與 `*.refined.md`：整份逐字對齊即時字幕，列出「同一位置聽成不同字」的片段（自動校準的候選素材）。
- 實測：CER 4.5%（即時 6.6%）。

## 7. 介面與協定

### 7.1 字幕事件（`events.py`）

| state | 意義 |
|---|---|
| `partial` | 辨識中（灰色），之後會被取代 |
| `final` | 定稿原文，等待翻譯 |
| `translated` | 已翻譯 |
| `drop` | 取消辨識中的字幕 |
| `retract` | 收回已顯示的句子（被合併或修正） |

欄位：`id, state, source, target, start, end, lang`。重新斷句後的 id 為「辨識器 id × 100 + 序號」。

### 7.2 WebSocket（`ws://127.0.0.1:8765`）

- 只聽 127.0.0.1；只接受 `chrome-extension://` 或沒有 Origin 的連線，一般網頁一律 403。
- 用戶端 → 伺服器：`load_video`、`translate`、`set_language`、`set_engine`、`capture`、`media_time`、二進位 PCM。
- 伺服器 → 用戶端：`status`、`subtitle`、`video_cues`、`video_error`、`translate_result`。
- `GET /shutdown`：只接受沒有 Origin 的本機程式，讓程式正常結束。

### 7.3 Chrome 擴充功能（`extension/`）

| 檔案 | 工作 |
|---|---|
| `background.js` | 與本機服務的 WebSocket、分頁擷取控制、快捷鍵 |
| `offscreen.js` + `pcm-worklet.js` | 取得分頁聲音，照常播放，同時轉 16kHz 送出 |
| `content.js` | 在 YouTube 播放器上畫字幕；聽譯中回報影片播放時間（`media_time`），拉時間軸時清掉舊字幕 |
| `popup.*` | 聽譯按鈕、語言、模式、字級 |

快捷鍵：`Alt+Shift+L` 聽譯目前分頁、`Alt+T` 開關字幕。

### 7.4 開關程式（`launcher/launcher.py` → `OrangeLiveTranslate.exe`）

- 沒在執行 → 用 `.venv\Scripts\pythonw.exe` 啟動，PID 寫入 `logs/app.pid`。
- 執行中 → 呼叫 `/shutdown` 正常關閉，8 秒內沒結束才強制結束，再請 Ollama 釋放模型。
- 同時只處理一次點擊（具名 mutex）。

## 8. 設定檔

| 檔案 | 用途 | 版控 |
|---|---|---|
| `config.example.yaml` | 所有設定與預設值 | 是 |
| `config.yaml` | 使用者覆蓋（只寫要改的） | 否 |
| `glossary.example.yaml` | 共用詞彙表範本（`terms`、`corrections`） | 是 |
| `glossary.yaml` | 使用者詞彙表（含常看主播用語） | 否 |
| `user_state.json` | 選單上的選擇（辨識模型） | 否 |

## 9. 資源用量

| 組合 | 顯示記憶體 |
|---|---|
| Whisper large-v3（int8_float16） | 2.15GB（實測） |
| anime-whisper（fp16） | 約 2GB |
| Sakura 7B IQ4_XS（num_ctx 4096） | 4.04GB（實測） |
| Hy-MT2-1.8B（英文時才載入） | 約 2.1GB（qwen3:8b 為 5.2GB） |
| 只有日文 | 約 6.2GB |
| 日英都用時合計 | 約 8.3GB（原本 qwen3:8b 時約 11.4GB） |

- 開遊戲、Chrome 時其他程式約佔 6GB，超過 16GB 時 Ollama 會輪流載入模型（切換語言的第一句慢幾秒）。
- 關閉程式時會請 Ollama 立刻釋放翻譯模型（否則 Ollama 預設常駐 30 分鐘）。

## 10. 品質指標（實測）

參考稿：白玖ウタノ歌枠公告段落 7 分 40 秒（`tests/data/utano_announce_ref.txt`），工具 `scripts/eval_asr.py`。

| 項目 | 數值 |
|---|---|
| 辨識字元錯誤率（large-v3、目前設定） | 6.6%（詳細紀錄見 `tuning/experiments.md`） |
| 回歸案例 | 7/7 通過（`tuning/run_cases.py`） |
| 定稿延遲（說完到出原文） | 中位數 0.3 秒、90% 0.4 秒（快速送入）；即時播放 90% 約 3～4.5 秒 |
| 翻譯一句 | Sakura 約 0.1～0.2 秒、Hy-MT2-1.8B 約 0.07 秒 |
| anime-whisper 錯誤率 | 12.5% |

## 11. 已知限制

- 少見詞仍會聽錯（例：齟齬→そこ），只能靠詞彙表的 `corrections`／`terms` 補。
- 一直講不停的長段落，字幕最慢可能 4～6 秒才出現。
- 字幕疊在影片上只支援 YouTube；其他網站看桌面字幕視窗。
- anime-whisper、ReazonSpeech 只支援日文。
- Haas-ja 等 Linux 限定模型未支援。

## 12. 程式碼地圖

```
src/live_translator/
  __main__.py      命令列入口（live / subs / check / bench / devices）
  app.py           組裝整條流程、啟動與關閉、切換語言／模型
  config.py        設定、詞彙表、修正清單載入
  events.py        字幕事件與事件匯流排
  segmenter.py     依句號重新斷句
  replay.py        依影片時間快取字幕（往回拉時間軸時字幕一致）
  srt_log.py       SRT 紀錄
  user_state.py    記住選單選擇
  gpu.py           CUDA DLL 路徑
  tools.py         check / bench / subs 子命令
  audio/           capture.py、tab_source.py、file_source.py
  asr/             streamer.py（切句＋Whisper）、vad.py、filters.py、anime.py、reazon.py
  translate/       base.py、router.py、sakura.py、hymt.py、ollama.py、openai_compat.py、worker.py、quality.py、postprocess.py、fillers.py
  subtitles/       youtube.py、formats.py
  server/ws.py     WebSocket 與 /shutdown
  overlay/window.py 浮動字幕視窗、字幕紀錄視窗、系統匣
extension/         Chrome 擴充功能
launcher/          OrangeLiveTranslate.exe 原始碼與圖示
scripts/           eval_asr.py（評分）、eval_correction.py、compare_asr.py、compare_models.py
tuning/            品質調校：實驗紀錄、問題紀錄、回歸案例、基準參考稿、報告
tests/             單元測試與參考逐字稿
```

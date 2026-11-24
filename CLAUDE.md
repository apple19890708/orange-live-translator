# Orange Live Translate 開發規則

> 顯示名稱是 Orange Live Translate；資料夾 `live-translator`、Python 套件 `live_translator` 維持原名。

本專案的規格見 [SPEC.md](SPEC.md)，開發紀錄見 [PLAN.md](PLAN.md)。**辨識與翻譯品質的調校一律在 [tuning/](tuning/README.md) 進行並記錄。**修改程式前先讀這些文件。

## 1. 目標與原則

1. **本機、免費優先。** 語音辨識與翻譯預設都在本機顯示卡上跑；雲端 API 只能是可選項，不能成為預設。
2. **輸出一律台灣繁體中文。** 所有譯文都經過 `translate/postprocess.py`（OpenCC `s2twp` ＋ 修正表）；發現新的轉換錯誤就加進修正表並寫測試。
3. **顯示記憶體預算 16GB，其中約 6GB 留給使用者的其他程式。** 預設組合不得超過約 10GB；更大的模型只能做成選項。
4. **使用者隱私。** 聲音不離開本機；WebSocket 只聽 127.0.0.1，只接受擴充功能或沒有 Origin 的連線；`/shutdown` 只接受沒有 Origin 的請求。

## 2. 改辨識或翻譯行為之前：先查紀錄、再量測

0. **先查 `tuning/experiments.md` 與 `tuning/issues.md`**：試過沒效的做法不要再試；同樣的問題先看之前的原因與處理。
1. **任何會影響辨識結果的改動**（模型、提示詞、切句參數、VAD、過濾規則），都要用 `scripts/eval_asr.py` 對參考稿 `tests/data/utano_announce_ref.txt` 量「錯誤率（CER）＋定稿延遲」，**改動前後都要量**。
2. 同一設定跑兩次結果相同，所以差異是真的；但即時播放的延遲要另外用 `--file` 以實際速度量（`eval_asr.py` 是快速送入，量不到即時的延遲）。
3. **沒有變好就不採用**，即使理論上應該更好。曾經被數據推翻的做法：前文 150 字、把前一句的聲音一起給 Whisper、短句合併重聽、beam 8、float16、kotoba-whisper。
4. CER 會把寫法差異（假名／漢字、語助詞）也算成錯。數字變差時，先比對實際差在哪裡，再判斷是不是真的變差，並在回報中說明。
5. **每次改辨識或翻譯（含後處理、詞彙表、提示詞）都要跑 `tuning/run_cases.py`**；有「退步」就不能採用。改到翻譯，**或任何會改變「送去翻譯的文字」的功能（斷句、合併、過濾、修正清單）**，另外跑 `tuning/eval_translation.py`（和人工中文字幕比較的 chrF），分數下降就不採用。曾經因為只看可讀性就採用斷句功能，結果拖累翻譯（E38）。翻譯基準有約 ±0.2 的跑次變動，差距小於 0.5 時要跑兩次以上。
6. 結果寫進 `tuning/experiments.md`（做法、數字、決定）；`PLAN.md` 只記重大里程碑。
7. 使用者回報聽錯、翻錯時：用 `logs/live-*.pcm`（和 `.srt` 同名、時間軸一致）重現那一段 → **剪成 `tuning/cases/audio/` 的案例並加進 `cases.yaml`** → 修 → 跑案例驗證 → 更新 `tuning/issues.md`。
8. 評分基準統一用 `tuning/benchmark/`；換了基準或模型，要先重量基準並在紀錄中註記。辨識改動要跑 `tuning/eval_asr_multi.py`（多段，含閒聊），不能只看單一段公告（E52→E60 的教訓）。
9. 從基準影片本身找出的修正（例：主播修正清單），在同一批基準上的分數會偏樂觀，紀錄時要註明。

## 3. 使用者的程式正在執行時

1. **不要在使用者使用中重啟或關閉程式。** 動手前先檢查：`logs/app.pid` 的行程是否存在、`127.0.0.1:8765` 是否有人在聽、`logs/*.pcm` 是否還在變大。正在用就只改程式碼，請使用者方便時自己重開。
2. 自己測試時用 `tuning/test_config.yaml`（連接埠 8799、不開視窗、不錄音），不要碰使用者的 8765。
3. 需要重開時，用 `OrangeLiveTranslate.exe` 一次一個動作，等前一個完成再做下一個（exe 是開關切換，連點會互相抵銷）。
4. 測試留下的 `user_state.json` 等使用者狀態要還原。

## 4. 寫程式

1. 文件、註解、使用者看到的訊息一律繁體中文（台灣用語）。
2. 新功能要有測試（`tests/test_basics.py`）；改到 `asr/streamer.py` 主迴圈時，`test_streaming_loop_emits_finals` 必須通過。
3. 改完至少跑 `python -m pytest -q`；改到啟動流程時，再用 `--no-overlay --file` 實際跑一次確認沒有例外。
4. 部分原始檔是 CRLF 換行。用腳本批次改檔時，字串裡不要寫 `\\n` 這類跳脫（曾經變成真的換行而產生語法錯誤）；改完用 `ast.parse` 檢查語法。
5. 辨識引擎都要實作同一組介面：`transcribe()`、`detect()`、`set_language()`、`add_hotwords()`、`close()`，並在 `asr/streamer.py` 的 `build_engine()`、`ENGINES` 註冊。
6. 翻譯呼叫一定要有輸出長度上限與逾時，不能讓一個請求卡住整條字幕。
7. 新的事件狀態要同時處理：浮動視窗、擴充功能 `content.js`、`srt_log.py`、翻譯 `worker.py`。
8. 設定的新欄位加在 `config.example.yaml` 並附中文說明；使用者的 `config.yaml`、`glossary.yaml` 不要覆蓋。

## 5. 安裝與下載

1. 下載模型或套件前，說明名稱、來源與大小；只從官方或模型作者的來源下載。
2. 不修改系統設定（例如安裝 WSL2、驅動）；需要時請使用者自己做。
3. `OrangeLiveTranslate.exe` 只在 `launcher/launcher.py` 變動時重新打包。
4. **暫存下載不長期保留**：測試用的影片、音訊放在 `cache/`，用完（例如字幕已校對存成參考稿、片段已存進 `tuning/`）就刪除；要長期保留的只放 `tuning/benchmark/`、`tuning/cases/`。實驗後確定不採用的模型也要刪除（Hugging Face 快取、Ollama）。刪除前先列出項目與大小並取得使用者同意。
5. 開發者測試設定用 `tuning/test_config.yaml`（連接埠 8799）。

## 6. 版控

1. 只有使用者要求時才 commit。
2. 不提交使用者的私人檔案：`config.yaml`、`glossary.yaml`、`user_state.json`、`logs/`、`cache/`、`OrangeLiveTranslate.exe`。

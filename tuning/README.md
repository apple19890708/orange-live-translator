# tuning：即時翻譯品質調校

這個資料夾集中管理「辨識」與「翻譯」品質的調校，目的：

- **不重複做工**：試過的做法和數字都記在 `experiments.md`，沒效的不再試。
- **不做無效優化**：每個改動都要有數字（CER、延遲）或回歸案例證明變好。
- **修好的不再壞掉**：使用者回報過的問題都存成回歸案例，每次修改都重新檢查。

## 內容

| 檔案 | 用途 |
|---|---|
| `experiments.md` | 實驗紀錄：做法、結果、採用與否。**試新點子前先查** |
| `issues.md` | 使用者回報的問題：原因、處理、狀態、對應案例 |
| `cases/cases.yaml` | 回歸案例定義：哪段錄音、必須／不能出現的字 |
| `cases/audio/` | 案例錄音（16kHz 單聲道 .pcm，從 `logs/` 剪下保存；logs 只留最近 3 場） |
| `benchmark/` | 辨識基準（日文逐字稿，量 CER）；`benchmark/mae/` 翻譯基準（人工中文字幕，量 chrF） |
| `eval_translation.py` | 翻譯基準評分，報告依時間對照人工字幕與我們的譯文 |
| `eval_asr_multi.py` | 多段辨識基準（公告＋4 部日文標準答案），同時量即時與精聽 |
| `eval_refine.py` | 精聽的辨識＋翻譯評分 |
| `tools/` | 建立翻譯基準：`extract_burned_subs.py`（偵測燒在畫面上的字幕＋OCR 草稿＋校對拼圖）、`save_reference.py`（存成參考稿） |
| `CALIBRATION_PLAN.md` | 持續校準計畫書 |
| `run_cases.py` | 執行全部回歸案例，產生報告 |
| `reports/` | 每次執行的報告（每句原文、譯文、檢查結果），可以對照前後差異 |

## 調校流程

1. **查紀錄**：`experiments.md` 有沒有試過類似做法？`issues.md` 有沒有同樣的問題？
2. **重現**：使用者回報的問題，從 `logs/live-*.pcm`（和 `.srt` 同名、時間軸一致）剪出那一段，存進 `cases/audio/`，在 `cases.yaml` 加一筆案例，先確認它會失敗。
3. **修改**。
4. **量測**（兩個都要跑）：
   ```powershell
   .\.venv\Scripts\python.exe scripts\eval_asr.py tuning\benchmark\utano_announce.pcm tuning\benchmark\utano_announce_ref.txt
   .\.venv\Scripts\python.exe tuning\run_cases.py --note "這次改了什麼"
   ```
   - CER 變差或延遲變長 → 比對實際差異再判斷（CER 會把寫法差異也算錯）。
   - 回歸案例有「退步」→ 不能採用，除非案例本身的期待需要修正（要寫明理由）。
5. **記錄**：`experiments.md` 加一列（含數字與決定）；`issues.md` 更新狀態；報告留在 `reports/`。

## 剪錄音片段

```powershell
.\.venv\Scripts\python.exe -c "import numpy as np; a=np.fromfile(r'logs\live-XXXX.pcm', dtype='<i2'); a[16000*開始秒:16000*結束秒].tofile(r'tuning\cases\audio\cNN_名稱.pcm')"
```

## 注意

- 錄音來自直播，只供本機測試，不放進版控（見 `.gitignore`）。
- 換了參考稿或模型後，`experiments.md` 的舊數字不能直接比較，要先重量一次基準並註記。

# 影片擷取工作室

Windows 與 macOS 本機影片標記、裁切及 JPG / PPTX 匯出工具。以 Python 3.14.5、PySide6、PyAV 建立，不修改或重新編碼原始影片。所有分析與輸出均在本機進行。

## 安裝發布版

從 GitHub Releases 下載符合平台的檔案：

- Windows 10/11 x64：下載 `Video-Frame-Cutter-vX.Y.Z-windows-x64.zip`，解壓後執行 `VideoFrameCutter.exe`。這是 portable 版本，不需要安裝 Python，也不會建立開始功能表捷徑。
- macOS 14+ Apple Silicon：下載 `Video-Frame-Cutter-vX.Y.Z-macos-arm64.dmg`，開啟後將 `Video Frame Cutter.app` 拖入 Applications。

每個 Release 都附有 `SHA256SUMS.txt`，可在執行前核對下載檔案。macOS 測試版目前只有 ad-hoc 簽章，尚未經 Apple Developer ID 簽章或公證；第一次啟動若被 Gatekeeper 阻擋，請確認下載來源及 SHA-256 後，在「系統設定 → 隱私權與安全性」選擇仍要打開。不要使用來源不明的重新封裝版本。

## 啟動

在專案根目錄的 PowerShell 執行，無須先啟用虛擬環境：

```powershell
.\.venv\Scripts\python.exe -m video_frame_cutter video.mp4
```

不帶影片參數可先開啟空白視窗，再從工具列選擇影片：

```powershell
.\.venv\Scripts\python.exe -m video_frame_cutter
```

開啟影片時會先完整建立影格索引。狀態列顯示進度，可取消；完成後才能播放及編輯。第一次開啟長影片需要等待，不是程式停止回應。

VS Code 安裝 Python 與 Python Debugger 擴充套件後，選擇 **Video Frame Cutter** 偵錯設定並按 F5；設定會明確使用本專案的 `.venv`。驗收設定會另外啟動測試子程序，主要用途是觀察日誌，不是逐步偵錯子程序。

## 重建開發環境

需求：Windows x64 或 macOS 14+ Apple Silicon、標準 CPython 3.14.5、可連線至 PyPI。建立環境前先確認實際版本，不要在既有開發環境上重建。

```powershell
py -3.14 --version
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
```

若 `py -3.14` 不是 3.14.5，請使用已安裝的 3.14.5 執行檔完整路徑建立環境。不要為了解決套件問題直接改用另一個 Python 版本。

macOS 請以對應的 `python3.14` 建立 `.venv`，並將上述命令中的 `.venv\Scripts\python.exe` 改為 `.venv/bin/python`。

[requirements-lock.txt](requirements-lock.txt) 是 Python 3.14.5 執行與測試環境的精確版本快照，包含傳遞依賴，但不是含雜湊的供應鏈鎖檔。GitHub Release workflow 會在 Windows x64 與 macOS arm64 原生 runner 重新驗證這組版本。專案本身另以 editable 模式安裝；發布建置工具由 [requirements-release.txt](requirements-release.txt) 固定。PyAV wheel 內含解碼函式庫，不需另外安裝命令列 FFmpeg。生成 PPTX 不需要先安裝 PowerPoint。

## 操作流程

介面跟隨系統的淺色／深色外觀；在系統設定切換主題時，視窗會即時更新，不需重新啟動。

1. **開啟影片**：完成索引後可播放／暫停、調整音量或靜音。暫停與逐幀預覽採用精確影格時間戳，避免可變影格率影片用平均 FPS 推算而偏移。
2. **分析畫面**：按上方工具列的「分析畫面變化」，設定參數後開始分析。每個相鄰影格都會比對，只縮小分析用影像，不跳幀。完成後確認是否套用候選；手動標記及人工修改過的自動標記會保留。
3. **調整標記**：點擊時間軸定位，新增標記；可拖曳標記、輸入時間後移動，或在右側多選刪除。同一影格不允許重複標記。清單勾選框決定是否納入匯出；右側欄完整用於顯示與操作標記。
4. **設定裁切**：雙擊標記，拖拉框線或八個把手，也可輸入左／上／右／下百分比。右側預览顯示最後拉伸效果；多選標記時可勾選批次套用，按「套用」確認，按「取消」保留原設定。
5. **儲存專案**：保存影片參照、標記、裁切及分析／輸出設定。影片本身不內嵌；搬移影片後可重新指定同一支影片，內容不同會拒絕載入舊影格參照。
6. **匯出**：按上方工具列的「匯出」，在對話框選擇 JPG 或 PPTX、全批共用的寬高、JPEG 品質與 PPTX 時間戳。可選擇只匯出清單中目前選取的標記；仍以已勾選納入匯出的項目為準，依影片時間排序。

標記與裁切變更支援復原／重做。重新分析套用結果亦可復原；原始影片不會被剪接或覆寫。

## 分析參數

| 參數 | 預設 | 行為 |
| --- | --- | --- |
| 變化門檻 | 0.08 | 越低越敏感，也可能增加雜訊候選 |
| 最小變化面積 | 0.8% | 抑制游標、小區域雜訊；過高會漏掉小幅文字更新 |
| 穩定時間 | 0.35 秒 | 畫面維持穩定多久後，回標該穩定窗口的第一幀 |
| 重複畫面忽略時間 | 關閉 | 忽略指定秒數內曾截取的相似畫面；0 表示關閉 |
| 最小間隔 | 0.5 秒 | 限制穩定標記的間距 |
| 分析寬度 | 480 px | 越大越能保留細節，但耗時較長 |

若講者在短時間內 A→B→A 來回切換，可將「重複畫面忽略時間」設為 **3 秒**。分析會保留第一次 A 與 B，略過返回的 A；每次重複出現都會重新計時，超過設定時間未出現後才可再次標記。相似判定沿用「變化門檻」與「最小變化面積」，而「最小間隔」仍須小於或等於需要保留的短畫面停留時間，例如每頁停留 1 秒時不可設得高於 1 秒。

若持續動態超過 **3 秒或設定的穩定時間（取較大者）**，每段變化產生一個「待檢查」標記，預設不匯出。稍後若畫面穩定，仍會產生穩定標記；若恰好同一幀則改成穩定標記，不重複增加。影片結尾仍未穩定也會留下待檢查候選。這是視覺差異偵測，不是 OCR 或語意理解；攝影機畫面、捲動和動畫仍可能需要人工修正。

## 輸出規則

- 裁切後直接拉伸成指定尺寸，預設 1920 × 1080、JPEG 品質 95；不同比例可能變形，這是目前選定的輸出模式。
- JPG 在指定目錄中建立新的 `frames_...` 子目錄，不覆寫既有截圖；檔名包含序號與時間。
- PPTX 一個標記一頁，嵌入與 JPG 相同的 JPEG 圖片資料；時間戳是額外的投影片頁尾文字，不會燒進 JPG。
- PPTX 的圖片像素尺寸與投影片實體尺寸不同。一般比例滿版呈現；極端比例會縮放圖片的實體尺寸，必要時在投影片留白，以符合 PowerPoint 尺寸限制。圖片不會被再次裁切或改變長寬比。
- 覆寫既有 PPTX 前會詢問。匯出先寫入暫存位置，成功後才提交；取消或失敗會清理本次暫存資料。
- 待檢查標記需人工確認後勾選才匯出。即使移動標記消除待檢查狀態，仍請確認匯出勾選狀態。

## 快捷鍵

| 按鍵 | 操作 |
| --- | --- |
| Space | 播放／暫停 |
| Left / Right | 上一幀／下一幀 |
| M / Delete | 新增／刪除選取標記 |
| Enter | 編輯目前標記裁切 |
| Ctrl + Left / Right | 上一個／下一個標記 |
| Ctrl + Z / Ctrl + Y | 復原／重做 |
| Ctrl + O / Ctrl + Shift + O | 開啟影片／專案 |
| Ctrl + S | 儲存專案 |
| Ctrl + 滾輪（時間軸） | 時間軸縮放 |
| 滾輪（時間軸） | 時間軸平移 |

## 測試與原片驗收

一般測試會跳過耗時原片驗收：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

原片驗收需桌面環境，會開啟測試視窗。不要同時啟動多份，也不要操作該測試視窗：

```powershell
.\.venv\Scripts\python.exe -u scripts/verify_real_video.py --timeout 1800
```

預設完整逐幀分析寬度為 **480**，與介面一致；`--analysis-width 160` 可做較快的低解析度檢查，但不是預設品質驗收。`--timeout` 是整個驗收的秒數上限，預設 3600 秒；以上命令設為 1800 秒。Ctrl+C 可中止，執行器會停止測試子程序。

每次執行建立獨立的 `outputs/verification/<日期時間_識別碼>/`：

- `run.log`：階段、每 5 秒 GUI 心跳、百分比、耗時與 pytest 輸出。沒有子程序輸出時，外層每 10 秒回報 WATCHDOG；這表示執行器仍活著，不代表 GUI 或分析一定有進展。
- `result.json`：目前狀態、PID、最近輸出、Python 路徑、分析寬度、總耗時及最終退出碼。只有測試退出成功且 JUnit 確認唯一測試通過才記為 `passed`；失敗、跳過、逾時或取消不會冒充成功。
- `pytest.xml`：pytest 的 JUnit 結果；強制終止時可能來不及產生。
- 截圖與分析後抽樣匯出的 JPG / PPTX：僅供該次人工核對，不代表所有自動標記都已人工確認。

測試會驗證索引、影音後端、音量／靜音、手動標記、裁切資料、專案保存讀回、JPG／PPTX 一致性、完整逐幀分析與分析結果抽樣匯出，並確認原片 SHA-256 未改變。若系統斷電或整個 VS Code 被強制關閉，`running` 紀錄可能殘留，須配合 PID／日誌時間確認，不能視為成功。

**仍需人工驗收**：實際聆聽長時間音畫同步、PowerPoint／LibreOffice 開啟簡報確認渲染、不同 Windows DPI 下的完整操作，以及原片自動標記是否符合個人需求。自動測試通過不等於這些人工項目已完成。

## 分析效能與一致性驗證

2026-09-09 本機 Windows／Python 3.14.5／Intel i5-13500（14 核心、20 邏輯處理器），使用 1280×720、30 fps、60,741 幀的原片，160 px 由初始提交 `e0dc422` 載入的舊版完整基準為 **856.74 秒**，目前版本為 **22.3–40.0 秒**（同一程式兩次量測，較慢一次緊接在 15 分鐘舊版基準之後、機器已降頻）；GUI 三次完整分析為 **24.19、23.90、23.75 秒**（各次上限 120 秒）。逐幀分析像素、SSIM 曲線與標記語意（PTS、change_seconds、score、review、included）與舊版逐位元一致，不放寬任何浮點容忍度；與既有 `video.vfc.json` 的 30 個標記比對，僅最後一個標記的匯出勾選不同，那是先前在介面手動取消勾選的結果。計時包含驗證用雜湊，不包含索引或匯出；不是所有硬體／影片的速度保證。

2026-09-08 第一輪的 160 px（236.19 秒）與 480 px（614.51 秒）結果、以及當時監督工具的 Windows 進度檔讀寫競態修正，保留於 [效能優化計畫](docs/performance-plan.md)。480 px 本輪未重新量測完整原片；其前處理走同一條路徑，但每幀 SSIM 成本較高，不宣稱時間。

本輪的加速全部來自數學上完全等價的改寫，演算法與參數語意不變：

- 偵測器的差分圖改用 uint8 的 `max(a,b) - min(a,b)` 取三通道最大值，並以整數計數除以像素數取得變化面積；與原本 int16 版本逐位元相同，速度約 8 倍。相鄰幀完全相同時直接回傳 0。
- 轉圖改用每個 worker 執行緒快取的 swscale 轉換器輸出 `rgb24`，Pillow 直接依平面 stride 讀入後做原本的 BILINEAR 縮放；與 `to_ndarray("rgb24")` 再縮放的結果逐像素相同（含 162、900、1366 等非 16 倍數寬度的雜訊測試）。曾試過 `rgb0` 零拷貝路徑，但 swscale 在非 16 倍數寬度會產生不同像素，已改回。旋轉或非方形像素仍使用既有顯示轉換。
- 解碼後的影格若與前一幀的原始緩衝區逐位元相同（此原片有 93.8% 的影格如此），直接沿用前一幀的分析像素，不重複轉圖；緩衝區不同才轉圖，因此只會多做而不會少做。
- 相鄰幀差分（含 SSIM）在 worker 中以同一個函式先算好，再依原始 PTS 順序交給同一個偵測器。每個 worker 只等待前一幀的像素就緒，不等待前一幀的 SSIM，因此動態畫面的 SSIM 仍可平行（640×360 雜訊片 480 px 由單 worker 32 ms/幀降到 8 workers 11.6 ms/幀）。穩定基準與錨點比較仍在偵測器內逐幀執行，錨點差分圖只計算一次。

分析預設使用 8 個前處理 worker、預取 buffer 12 與自動解碼執行緒數（`decoder_threads=0`），三個值定義於 `analysis.py` 的模組常數，基準與監督腳本都從 `analysis_defaults()` 取得，不再各自硬編。流程以固定長度佇列保存尚未取回的工作，最多 buffer 個影格在途，每個尚未開始的工作另保留前一幀解碼影格，因此同時存活的解碼影格約為 buffer + 1（1280×720 約 18 MiB；4K 原片約 160 MiB，接近驗收的 256 MiB 上限），不會把整支影片載入記憶體；取消會停止提交、取消尚未開始的工作並等待執行中的工作結束，關閉執行緒後才釋放 decoder。讀取很慢的磁碟／網路檔案仍可能延遲取消。動態畫面很多的影片每幀都要算 SSIM，即使平行也不會有本原片的加速幅度。

短測可比較 worker 和解碼執行緒配置，不代表完整原片達標：

```powershell
.\.venv\Scripts\python.exe -u scripts/benchmark_analysis.py --sample-frames 600 --workers 2 3 4 --decoder-threads 1 2 4
```

完整 160 px 舊新版比對及 GUI 三次效能驗收：

```powershell
.\.venv\Scripts\python.exe -u scripts/benchmark_analysis.py --full --width 160 --workers 8 --decoder-threads 0 --limit-seconds 120 --output outputs/benchmarks/full160-v2/result.json
.\.venv\Scripts\python.exe -u scripts/verify_real_video.py --analysis-width 160 --analysis-repeats 3 --analysis-limit 120 --baseline-report outputs/benchmarks/full160-v2/result.json --timeout 1800
```

請依序執行，不要同時量測。基準工具需要 Git 與初始提交 `e0dc422`，會從該提交載入獨立舊版程式；沒有安裝在 PATH 的 PortableGit 也可使用目前的本機安裝位置。完整舊版分析本身仍然耗時，不受新版 120 秒門檻限制。

- 基準報告記錄 Python／套件版本、影片 SHA-256、程式修改雜湊、總時間、前處理 worker 轉圖時間、worker 相鄰幀差分時間、偵測器判定時間與 Windows 工作集；平行階段時間不能直接相加當成總時間，且相同影格沿用像素時不計轉圖時間。
- 逐幀輸入簽章包含 PTS、尺寸和全部 RGB 像素；曲線以原始雙精度值計算簽章；標記比較全部語意欄位，僅排除隨機 `uid`。不是只比標記數量，也沒有放寬浮點容忍度。
- GUI 的 `analysis.json`／`result.json` 記錄每次分析秒數、50 ms 心跳最大間隔、取樣記憶體增量、完整結果與取消耗時。`--analysis-limit 120` 要求每次分析低於 120 秒，GUI 心跳間隔低於 1 秒、取消低於 2 秒及工作集取樣增量不超過 256 MiB。
- 分析計時从索引就緒、啟動工作開始，到 GUI 收到结果並完成工作清理為止，包括診斷用像素雜湊；不包含索引、人工確認、後續縮圖和匯出。`--timeout` 則仍限制整個驗收程序，兩者分開判定。
- 每次重新開啟解碼器，不使用分析快取；後續執行可能受 OS 檔案快取影響，沒有宣稱清除系統快取。Windows 工作集採樣不是每個瞬間的分配上限，程序歷史峰值另外記錄。

480 px 改用具程序監督的單一命令，依序執行舊新版全片比對、一次 GUI 驗收與一般回歸，任一步失敗即停止：

```powershell
.\.venv\Scripts\python.exe -u scripts/verify_performance.py --width 480 --timeout 5400 --benchmark-timeout 3600 --gui-timeout 1800
```

每次建立全新的 `outputs/performance/<日期時間_識別碼>/`，不覆蓋先前中斷紀錄。`--timeout` 為整條流程上限，`--benchmark-timeout` 包含舊版與新版兩次分析；GUI 本身上限為 `--gui-timeout`，外層另保留最多 30 秒清理時間，仍受整體上限約束。480 px 不套用 120 秒門檻，其加速幅度需另外實測。

- `result.json`：整體階段、監督 PID／啟動身分、各階段退出碼與最終狀態。
- `benchmark/status.json`、`gui/status.json`、`regression/status.json`：每 5 秒監督心跳、子程序 PID、耗時、日誌大小及距離上次輸出的秒數；即使子程序沒有輸出仍會更新。監督心跳不是分析正在前進的證據。
- 各階段 `run.log`：直接保存完整 stdout／stderr，不依赖終端捲動區。突然退出會記錄非零退出碼，退出 0 但沒有有效成功報告也不能算通過。
- `progress.json`：分析階段、已處理幀、百分比與階段耗時，分析前進時約每 5 秒更新；`benchmark.json` 保留完整比對结果，`gui-results/` 保留 GUI 驗收報告與匯出，`regression.xml` 保留最後一般回歸結果。
- Windows 的程式內報告讀取使用可共享刪除的快照，寫入使用 `ReplaceFileW`，避免監督讀取阻擋進度檔替換；若索引或掃描程式短暫佔用進度檔（共享／鎖定違規或錯誤 1175），寫入會在一秒內重試，其他錯誤仍立即失敗。這只處理配套讀寫程序的並行，不會隱藏真正的檔案權限錯誤。
- 逾時或 Ctrl+C 會停止本次子程序；Windows 同時終止其子程序樹，避免 GUI 驗收的 pytest 留在背景。沒有程序進度恢復功能，中斷後不能從最後記錄幀接續分析。

查詢時將下面路徑換成該次輸出的實際目錄；這個命令不會啟動或停止測試：

```powershell
.\.venv\Scripts\python.exe scripts/verify_performance.py --status outputs/performance/<日期時間_識別碼>
```

Windows 查詢會比對監督 PID 與建立時間，避免 PID 重用誤判；監督程序不存在時顯示 `interrupted`，心跳超過 15 秒顯示 `unresponsive`。查詢不修改原始證據。若監督程序也遭強制關閉或系統斷電，就無法當場寫入最終退出狀態，不能只看檔案殘留的 `running` 判斷仍在運作。開始新一輪前還須確認沒有殘留分析子程序。詳細紀錄見 [效能優化計畫](docs/performance-plan.md)。

## 限制與故障排查

- 不支援影片剪接、多軌編輯、OCR、批次多影片或 Windows 安裝程式；Windows 發布版為 portable ZIP。
- 檔案須有可解碼影片與遞增的影格呈現時間戳；格式副檔名不保證 codec 可解碼。無音軌影片仍可截圖，原片驗收素材則預期含音軌。
- 索引、縮圖與分析可能需要時間；全片索引完成前不能編輯。畫面分數與影格索引會隨片長使用記憶體，但不保存整支影片的全解析度影像。
- 如果播放失敗，查看視窗狀態列；若精確取幀／匯出失敗，保留錯誤訊息與驗收日誌。不要用重新執行多份驗收來判斷前一份是否還活著。
- 如果找不到套件，使用上方 `.venv` 的明確路徑啟動，不要只執行 PATH 中不確定版本的 `python`。
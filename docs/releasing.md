# 發布桌面應用程式

正式版由 `vX.Y.Z` tag 觸發 GitHub Actions，建立 Windows x64 portable ZIP 與 macOS arm64 DMG。版本的唯一來源是 `pyproject.toml`；tag 去除 `v` 後必須與其完全相同。

## 發布前

1. 更新 `pyproject.toml` 的 `project.version`，使用不含 `v` 的 `X.Y.Z` 格式。
2. 在 Windows 開發環境執行：

   ```powershell
   .\.venv\Scripts\python.exe -m pip check
   .\.venv\Scripts\python.exe -m ruff check .
   .\.venv\Scripts\python.exe -m pytest -q
   ```

3. 可先建立本機 portable ZIP：

   ```powershell
   .\.venv\Scripts\python.exe scripts\build_release.py --expected-version X.Y.Z
   ```

4. 提交版本修改後建立並推送 tag：

   ```powershell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

## 自動發布

`release.yml` 在原生 `windows-latest` 與 `macos-14` runner 上安裝 Python 3.14 及 binary wheels，執行依賴檢查、Ruff、一般測試、PyInstaller 建置與 frozen smoke test。macOS 另檢查 Mach-O arm64 架構、ad-hoc code signature 及 DMG 完整性。

兩平台都通過後，publish job 才建立 GitHub Release，內容包含：

- `Video-Frame-Cutter-vX.Y.Z-windows-x64.zip`
- `Video-Frame-Cutter-vX.Y.Z-macos-arm64.dmg`
- `SHA256SUMS.txt`

下載 Release 後應重新計算 SHA-256，確認與 `SHA256SUMS.txt` 完全一致，並在實機驗證影片索引、播放、分析、JPG 及 PPTX 匯出。耗時原片與效能驗收不在 tag workflow 內，需依 README 的順序另行執行。

## 失敗處理

若 workflow 在發布前失敗，修正後可刪除遠端與本機 tag，再於同一提交歷史建立正確 tag。若 GitHub Release 已公開，不應重用同一版本覆蓋產物；請遞增 patch 版本並建立新 tag，以維持 checksum 與下載內容可追溯。

## macOS 簽章限制

目前沒有 Apple Developer ID，PyInstaller 只對 Apple Silicon bundle 做 ad-hoc signing，沒有 notarization。下載版會觸發 Gatekeeper，README 必須保留手動開啟與 checksum 提示。

取得 Apple Developer Program 後，應在建立 DMG 前加入 Developer ID Application hardened-runtime 簽章，再以 `notarytool submit --wait` 公證 DMG 並使用 `stapler` 附加票證。憑證、密碼、Apple ID 與 team ID 必須存放在 GitHub Actions secrets，不可提交到 repository。
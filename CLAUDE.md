# CLAUDE.md — line-file-bot

> 專案資訊見 README.md、system_map.md
> 共用指引與環境規則見 ../shared/CLAUDE.md、../shared/LOG_GUIDE.md

## 環境需求
- Python: 見 .python-version
- pip 套件：flask, line-bot-sdk, gunicorn（見 requirements.txt）
- Lock files: requirements.txt

## 部署
- push 到 GitHub → Render 自動部署
- .env 含 LINE Token，不進 git

## 開 Session 流程
每次新對話開始，在回應用戶之前，先執行：
1. `git fetch && git log origin/master --oneline -10`（掌握跨機器最近變更）
2. 讀 `system_map.md`（如果存在，掌握系統現狀）
3. 讀 `log_chronological.md` 最後 10 筆（如果存在，掌握決策脈絡）
4. 讀 `ROADMAP.md`（如果存在，掌握開發方向）
5. 如果 ROADMAP.md 存在，讀完後問用戶：「目前 ROADMAP 的優先順序要不要調整？」
以上檔案不存在就跳過，不要報錯。
每個步驟都要向用戶顯示目前正在做什麼（例如「正在讀取 system_map.md...」）。

## Scratch 規則
- 所有 session 中產生的臨時內容存入 `scripts/scratch/`，不允許只存在對話中
- 適用：程式腳本、網路搜尋結果、計算過程、草稿文字
- 命名：`YYYYMMDD_HHMM_機台_核心動作_狀態.副檔名`（機台：NB/DT，狀態：ok/failed/wip，程式用 .py、文字用 .md）
- 不進 git（靠 Dropbox 同步）
- 跨專案：腳本放被改的專案，log 兩邊記
- 詳細規範見 `../shared/ROADMAP.md`「scratch 規範草案」

## 收工：LCP

見 ../shared/CLAUDE.md（含 ROADMAP 同步規則）

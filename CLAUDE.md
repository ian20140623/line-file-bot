# CLAUDE.md — line-file-bot

> 專案資訊見 README.md、system_map.md
> 共用指引與環境規則見 ../shared/CLAUDE.md、../shared/LOG_GUIDE.md ^ck-d33d9c-0


## 核心原則
SRP / Information Hiding / OCP / No Silent Workaround(遇阻停下報告不繞路) / Explicit Intent(做之前先宣告 scope)。詳見 [shared/ARCHITECTURE_PRINCIPLES.md](../shared/ARCHITECTURE_PRINCIPLES.md)。

## 環境需求
- Python: 見 .python-version
- pip 套件：flask, line-bot-sdk, gunicorn（見 requirements.txt）
- Lock files: requirements.txt ^ck-d33d9c-1

## 部署
- push 到 GitHub → Render 自動部署
- .env 含 LINE Token，不進 git ^ck-d33d9c-2

## 開 Session 流程
每次新對話開始，在回應用戶之前，先執行：
1. `git fetch && git log origin/master --oneline -10`（掌握跨機器最近變更）
2. 讀 `system_map.md`（如果存在，掌握系統現狀）
3. 讀 `log_chronological.md` 最後 10 筆（如果存在，掌握決策脈絡）
4. 讀 `ROADMAP.md`（如果存在，掌握開發方向）
5. 如果 ROADMAP.md 存在，讀完後問用戶：「目前 ROADMAP 的優先順序要不要調整？」
以上檔案不存在就跳過，不要報錯。
每個步驟都要向用戶顯示目前正在做什麼（例如「正在讀取 system_map.md...」）。 ^ck-d33d9c-3

## Scratch 規則
見 ../shared/CLAUDE.md「環境注意」 ^ck-d33d9c-4

## 收工：LCP

見 ../shared/CLAUDE.md（含 ROADMAP 同步規則） ^ck-d33d9c-5

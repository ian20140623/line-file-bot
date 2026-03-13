# LINE File Bot 開發記錄 — 功能說明
*last updated: 2026-03-13*

> **給未來 AI 的說明**
> 共用指引見 [`../shared/LOG_GUIDE.md`](../shared/LOG_GUIDE.md)
>
> **本專案補充：**
> - 遠端：origin/main

---

## 系統目標
- 自動接收 LINE 群組或私訊中分享的文件檔案，下載並儲存到本地，回傳確認訊息。
- 接收圖片訊息，Claude Sonnet OCR 提取文字，支援審稿/提取分流。
- 文字對話：Claude Haiku 帶記憶對話（最近 10 輪，30 分鐘 TTL）。

---

## 技術架構
*last updated: 2026-03-13*

| 元件 | 角色 |
|------|------|
| Python 3.12.6 | 執行環境 |
| Flask 3.1.0 | Web 框架（HTTP 端點） |
| Gunicorn 23.0.0 | Production WSGI Server |
| line-bot-sdk 3.22.0 | LINE Messaging API SDK |
| anthropic (Python SDK) | Claude Sonnet 4（OCR/審稿）、Opus 4.6（對話） |
| Docker + Docker Compose | 容器化部署（本機桌機） |
| ngrok 3.37.2 | HTTPS 隧道，暴露本機 port 給 LINE Webhook |

---

## 主要流程
*last updated: 2026-02-24*

### 1. Webhook 接收 (`/callback`)
- LINE Platform 發送 POST 到 `/callback`
- 驗證 `X-Line-Signature` 簽名
- 解析事件，分派到對應 handler

### 2. 檔案處理 (`handle_file_message`)
- 收到檔案訊息時觸發
- 檢查副檔名是否在支援列表中
- 呼叫 `save_file()` 下載並儲存
- 回傳確認訊息（含檔案路徑）

### 3. 檔案儲存 (`save_file`)
- 用 `MessagingApiBlob.get_message_content()` 從 LINE 伺服器下載
- 檔名清理特殊字元 + 加時間戳（`name_YYYYmmdd_HHMMSS.ext`）
- 儲存到 `DOWNLOAD_DIR`（預設 `./downloaded_files`）

### 4. 圖片分流 (`handle_image_message` → `handle_postback`)
- 收到圖片 → 下載 → Claude Sonnet OCR 提取文字
- 回覆文字預覽（前 200 字）+ Quick Reply 按鈕
- 用戶選擇：
  - **文字提取**：回傳 OCR 全文
  - **報告審稿**：Claude Sonnet 校對（錯字、標點、異常空白）
- In-memory session 暫存 OCR 結果（TTL 10 分鐘）

### 5. 文字對話 (`handle_text_message`)
- 收到文字訊息 → 帶歷史記錄送 Claude Opus 4.6
- 每用戶最近 10 輪對話記憶，30 分鐘 TTL
- Chat history 與 image session 分開存放

### 6. 健康檢查 (`/`)
- GET 請求回傳 "OK"
- 供 uptime monitoring 服務定期 ping，維持 Render 免費方案不休眠

---

## 支援檔案類型
*last updated: 2026-02-24*

| 類別 | 副檔名 |
|------|--------|
| 文件 | `.pdf`, `.doc`, `.docx`, `.txt`, `.csv`, `.rtf` |
| 試算表 | `.xls`, `.xlsx`, `.ods` |
| 簡報 | `.ppt`, `.pptx`, `.odp` |
| 其他 | `.odt` |
| 壓縮檔 | `.zip`, `.rar`, `.7z` |

---

## 關鍵設計決策
*last updated: 2026-02-24*

### SDK 版本：line-bot-sdk 3.22.0
- **為什麼不用 3.14.0**：Python 3.12 的 PEP 585 generics 導致語法錯誤
- **現行做法**：升級到 3.22.0，支援 Python 3.10–3.14

### 檔名處理：時間戳 + 字元清理
- **為什麼加時間戳**：同名檔案不會互相覆蓋
- **為什麼清理字元**：LINE 傳來的檔名可能含特殊字元，避免檔案系統問題

### 部署平台：本機 Docker + ngrok（原 Render）
- **為什麼搬離 Render**：免費方案 15 分鐘休眠 + 喚醒 30-50 秒，與 LINE reply token 30 秒過期衝突
- **為什麼用 Docker**：可移植性（未來搬 Mac）、環境隔離、一鍵啟動
- **為什麼用 ngrok**：提供 HTTPS 公開網址給 LINE Webhook
- **限制**：ngrok 免費版每次重啟換網址，需手動更新 LINE Webhook URL

---

## 環境變數
*last updated: 2026-03-12*

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | Bot 認證 Token |
| `LINE_CHANNEL_SECRET` | Webhook 簽名驗證 |
| `DOWNLOAD_DIR` | 檔案儲存目錄（預設 `./downloaded_files`） |
| `ANTHROPIC_API_KEY` | Anthropic API Key（Claude OCR/審稿/對話） |
| `PORT` | 伺服器埠號（預設 5000） |

---

## 已知限制
*last updated: 2026-03-13*

| 限制 | 說明 | 緩解方式 |
|------|------|----------|
| ngrok 網址不固定 | 免費版每次重啟換網址 | 手動更新 LINE Webhook URL；未來考慮 Cloudflare Tunnel |
| LINE 檔案過期 | LINE 伺服器上的檔案有時效限制 | Webhook 即時下載，不做延遲處理 |
| 僅支援文件+圖片+文字 | 影片、音訊不處理 | 設計選擇：文件下載 + 圖片 AI 分析 + 文字對話 |

---

## 專案結構
*last updated: 2026-03-13*

```
line-file-bot/
├── app.py                 # Flask 主程式（Webhook、檔案處理、AI 對話）
├── requirements.txt       # Python 依賴（Flask、line-bot-sdk、Gunicorn、anthropic）
├── Dockerfile             # Docker 映像定義（Python 3.12-slim + Gunicorn）
├── docker-compose.yml     # Docker Compose 設定（port、env、volume）
├── .dockerignore          # Docker build 排除清單
├── .env.example           # 環境變數範本
├── .env                   # 環境變數（不進 git）
├── README.md              # 設定與部署說明（中文）
├── .python-version        # Python 版本（3.12.6）
├── ROADMAP.md             # 開發路線圖
├── log_chronological.md   # 開發記錄 — 流水帳
└── system_map.md          # 現況快照 — 功能說明
```

---

## 開發環境
*last updated: 2026-03-13*

- **專案位置**：`C:\Users\User\OneDrive\ClaudeProjects\line-file-bot\`
- **Git remote**：origin/main（GitHub）
- **部署**：本機 Docker（DESKTOP-82QANNF）+ ngrok HTTPS 隧道
- **.gitignore**：排除 `.env`、`.claude/`、`__pycache__/`、`downloaded_files/`

---

## 待辦事項
*last updated: 2026-03-13*

- [x] 基本檔案下載功能
- [x] Python 3.12 相容性修復（line-bot-sdk 升級）
- [x] Health check endpoint
- [x] 圖片接收 + 多模態分析
- [x] 圖片分流處理（OCR + Quick Reply 選單）
- [x] OpenAI → Anthropic 遷移
- [x] 文字對話功能（Claude Opus 4.6 + 記憶）
- [x] 本機部署（Docker + ngrok）
- [ ] Obsidian vault 查詢（搜尋 + Claude 分析）
- [ ] PDF 生成 + LINE 傳檔
- [ ] 加入錯誤通知機制

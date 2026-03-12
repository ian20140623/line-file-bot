# LINE File Bot 開發記錄 — 功能說明
*last updated: 2026-02-24*

> **給未來 AI 的說明**
> 共用指引見 [`../shared/LOG_GUIDE.md`](../shared/LOG_GUIDE.md)
>
> **本專案補充：**
> - 遠端：origin/main

---

## 系統目標
- 自動接收 LINE 群組或私訊中分享的文件檔案，下載並儲存到本地，回傳確認訊息。
- 接收圖片訊息，送 GPT-4o 多模態模型分析，將理解結果回傳 LINE 對話。

---

## 技術架構
*last updated: 2026-02-24*

| 元件 | 角色 |
|------|------|
| Python 3.12.6 | 執行環境 |
| Flask 3.1.0 | Web 框架（HTTP 端點） |
| Gunicorn 23.0.0 | Production WSGI Server |
| line-bot-sdk 3.22.0 | LINE Messaging API SDK |
| openai (Python SDK) | GPT-4o 多模態圖片分析 |
| Render | 部署平台（免費方案） |

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

### 4. 圖片處理 (`handle_image_message`)
- 收到圖片訊息時觸發
- 用 `MessagingApiBlob.get_message_content()` 下載圖片
- 圖片 base64 編碼後送 GPT-4o (`analyze_image_with_gpt4o`)
- Prompt：繁體中文描述圖片內容 + 擷取文字
- 回傳分析結果到 LINE 對話

### 5. 健康檢查 (`/`)
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

### 部署平台：Render
- **為什麼選 Render**：免費方案、自動 HTTPS（LINE Webhook 要求）、GitHub 自動部署
- **限制**：15 分鐘無流量休眠、暫存性儲存

---

## 環境變數
*last updated: 2026-02-24*

| 變數 | 說明 |
|------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | Bot 認證 Token |
| `LINE_CHANNEL_SECRET` | Webhook 簽名驗證 |
| `DOWNLOAD_DIR` | 檔案儲存目錄（預設 `./downloaded_files`） |
| `OPENAI_API_KEY` | OpenAI API Key（GPT-4o 圖片分析） |
| `PORT` | 伺服器埠號（預設 5000） |

---

## 已知限制
*last updated: 2026-02-24*

| 限制 | 說明 | 緩解方式 |
|------|------|----------|
| Render 免費方案休眠 | 15 分鐘無流量自動休眠，喚醒需約 30 秒 | Health check endpoint + 外部 uptime monitoring |
| 暫存性儲存 | Render 重啟後檔案消失 | 未來可改接雲端儲存（Google Drive、S3） |
| LINE 檔案過期 | LINE 伺服器上的檔案有時效限制 | Webhook 即時下載，不做延遲處理 |
| 僅支援文件+圖片 | 影片、音訊不處理 | 設計選擇：文件下載 + 圖片 AI 分析 |

---

## 專案結構
*last updated: 2026-02-24*

```
line-file-bot/
├── app.py                 # Flask 主程式（Webhook、檔案處理）
├── requirements.txt       # Python 依賴（Flask、line-bot-sdk、Gunicorn）
├── README.md              # 設定與部署說明（中文）
├── .env.example           # 環境變數範本
├── .python-version        # Python 版本（3.12.6）
├── log_chronological.md   # 開發記錄 — 流水帳
└── system_map.md          # 現況快照 — 功能說明
```

---

## 開發環境
*last updated: 2026-02-24*

- **專案位置**：`C:\Users\Ian\OneDrive\ClaudeProjects\line-file-bot\`
- **Git remote**：origin/main（GitHub）
- **.gitignore**：應排除 `.env`（含 API Token）、`.claude/`

---

## 待辦事項
*last updated: 2026-02-24*

- [x] 基本檔案下載功能
- [x] Python 3.12 相容性修復（line-bot-sdk 升級）
- [x] Health check endpoint
- [x] 圖片接收 + GPT-4o 多模態分析
- [ ] 圖片/PDF 分流處理（Quick Reply 選單）
- [ ] 接入持久化儲存（Google Drive / S3）
- [ ] 加入錯誤通知機制

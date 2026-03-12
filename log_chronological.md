# LINE File Bot 開發記錄 — 流水帳
*始於 2026-02-14*

> **給未來 AI 的說明**
> 共用指引見 [`../shared/LOG_GUIDE.md`](../shared/LOG_GUIDE.md)
>
> **本專案補充：**
> - 遠端：origin/main

---

## 2026-02-14：專案建立

### 初始建置
- 建立 `app.py` — Flask 主程式，處理 LINE Webhook、檔案下載
- 建立 `requirements.txt` — Flask、line-bot-sdk 3.14.0、Gunicorn
- 建立 `.python-version` — 指定 Python 3.12.6

### 功能
- LINE Webhook 接收檔案訊息，自動下載到本地
- 支援 16 種檔案類型（PDF、Word、Excel、PPT、壓縮檔等）
- 檔名加時間戳避免覆蓋，特殊字元清理

### 文件
- 新增 `README.md`：完整的 LINE Developer 設定、Render 部署、本地測試說明
- 新增 `.env.example`：環境變數範本

---

## 2026-02-15：Python 3.12 相容性修復

### 問題
- line-bot-sdk 3.14.0 在 Python 3.12 上有語法問題（PEP 585 generics）

### 修復
- Python 版本確認為 3.12.6
- line-bot-sdk 從 3.14.0 升級到 3.22.0（支援 Python 3.10–3.14）

---

## 2026-02-21：健康檢查端點

### 新增
- 在 `/` 路徑加入 GET health check，回傳 "OK"
- 目的：配合 Render 免費方案的 uptime monitoring，避免服務休眠

---

## 2026-03-12：圖片接收 + GPT-4o 多模態分析 [NB]

### 新增
- `handle_image_message`：接收 LINE 圖片訊息，下載圖片 bytes
- `analyze_image_with_gpt4o`：圖片 base64 編碼後送 GPT-4o，回傳繁體中文描述+文字擷取
- 新增環境變數 `OPENAI_API_KEY`
- `requirements.txt` 加入 `openai>=1.0.0`

### 架構決策
- 圖片不落地儲存，直接記憶體中 base64 編碼送 API — 避免暫存檔管理
- GPT-4o 而非 Claude — 用戶指定
- Prompt 固定為繁體中文描述+文字擷取，Phase B 再加分流選單

### 建立 ROADMAP.md
- Phase A（本次）：圖片 + AI 基礎
- Phase B：分流處理（圖片/PDF/行程）
- Phase C：互動強化（Quick Reply / Flex Message）
- Phase D：基礎設施（搬遷部署、持久化儲存）

---

## 2026-03-12：B1 圖片分流 — OCR + Quick Reply + Postback [NB]

### 新增
- `ocr_image()`：GPT-4o OCR 專用函數，提取圖片文字
- `proofread_text()`：GPT-4o 校對函數（錯字、標點、空白）
- `handle_postback()`：PostbackEvent handler，處理 Quick Reply 選擇
- In-memory session store（`_sessions` dict + TTL 10 分鐘 + threading lock）
- `reply_message()` 共用 helper，減少重複程式碼

### 變更
- `handle_image_message` 改為分流模式：OCR → 預覽 → Quick Reply
- 移除 `analyze_image_with_gpt4o()`（被 `ocr_image` 取代）

### 架構決策
- In-memory dict 暫存 OCR 結果 — Render 單 worker 夠用，重啟歸零可接受
- PostbackAction 而非 MessageAction — postback data 不會顯示在對話中，較乾淨
- OCR prompt 改為純文字提取（不做圖片描述），審稿/提取在 postback 階段才分流

---

## 待解決

- **Render 免費方案限制** — 15 分鐘無流量會休眠，喚醒需約 30 秒
- **暫存性儲存** — Render 重啟後下載的檔案會消失
- **LINE 檔案過期** — LINE 伺服器上的檔案有時效限制，需即時下載

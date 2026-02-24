# LINE File Bot 開發記錄 — 流水帳
*始於 2026-02-14*

> **給未來 AI 的說明**
> 請先閱讀共用指引：[`../AI_LOG_INSTRUCTIONS.md`](../AI_LOG_INSTRUCTIONS.md)
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

## 待解決

- **Render 免費方案限制** — 15 分鐘無流量會休眠，喚醒需約 30 秒
- **暫存性儲存** — Render 重啟後下載的檔案會消失
- **LINE 檔案過期** — LINE 伺服器上的檔案有時效限制，需即時下載

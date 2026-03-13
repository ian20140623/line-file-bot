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

## 2026-03-12：OpenAI → Anthropic 遷移 [NB]

### 變更
- `requirements.txt`：`openai>=1.0.0` → `anthropic>=0.40.0`
- `app.py`：OpenAI SDK → Anthropic SDK
- `ocr_image()`、`proofread_text()` 改用 Claude Sonnet 4
- 環境變數：`OPENAI_API_KEY` → `ANTHROPIC_API_KEY`

### 原因
- 用戶設定 OpenAI API Key 時遇到困難，改用 Anthropic

---

## 2026-03-12：文字對話功能 [NB]

### 新增
- `TextMessageContent` handler：接收文字訊息，送 Claude 對話
- `chat_with_claude()`：帶記憶的對話函數
- Chat history store（`_chat_history` dict）：每用戶最近 10 輪，30 分鐘 TTL
- 對話模型：Claude Opus 4.6
- System prompt：繁體中文助手，簡潔扼要

### 架構決策
- 對話用 Opus 4.6（最強），OCR/審稿用 Sonnet 4（準確+快）
- Chat history 與 image session 分開存放，互不干擾

---

## 2026-03-13（週四）

### 22:34 [DESKTOP] D1 完成：Render → 本機部署（Docker + ngrok）

#### 背景
- Render 免費方案 15 分鐘無流量休眠，喚醒需 30-50 秒
- LINE reply token 30 秒過期，與 Render 喚醒時間衝突 → bot 經常無回應
- Render 現在要求綁信用卡，即使免費方案

#### 本機部署
- 建立 `Dockerfile`：Python 3.12-slim + Gunicorn，單 worker
- 建立 `docker-compose.yml`：port 5000、env_file、downloaded_files volume mount
- 建立 `.dockerignore`：排除 .git、.env、.claude、markdown 等
- ngrok 3.37.2：建立 HTTPS 隧道，LINE Webhook URL 指向本機
- 測試通過：文字對話、圖片 OCR 皆正常

#### 其他變更
- `.env.example` 加入 `ANTHROPIC_API_KEY`
- `.gitignore` 加入 `.env`、`__pycache__/`、`*.pyc`、`downloaded_files/`

#### 架構討論（未實作，記錄想法）
- **Obsidian vault 查詢**：bot 跑在本機可直接讀取 vault，用 Claude 分析報告內容
  - 意圖分流：Claude 判斷「查報告」vs「一般聊天」
  - 搜尋策略：glob + grep 找相關 markdown，塞進 Claude context 分析
  - 權限控制：只寫讀/寫函數，不給刪除，路徑鎖定 vault 目錄
- **PDF 生成 + LINE 傳檔**：分析結果可生成 PDF 回傳 LINE
- **bot 本質**：類似 OpenClaw 的本機 AI agent，但用 LINE 當介面、權限完全由程式碼控制

#### 注意
- ngrok 免費版每次重啟換網址，需重新更新 LINE Webhook URL
- 未來可考慮 Cloudflare Tunnel（免費固定域名）或 ngrok 付費方案

### 23:32 [DESKTOP] B3 行程解析 + 圖片智慧分流 + 文字行程偵測

#### B3 行程/會議解析（完成）
- 新增 `parse_schedule()`：Claude Sonnet 從文字提取行程 JSON
- 新增 `generate_ics()`：結構化 JSON → .ics 行事曆檔
  - 含 VTIMEZONE（Asia/Taipei）、METHOD:PUBLISH、建立時間戳記在 DESCRIPTION
- 新增 `format_schedule_text()`：行程 JSON → LINE 可讀摘要
- 新增 `/ics/<filename>` route：強制下載 .ics（避免 iOS 訂閱問題）
- 新增 `/cal/<file_id>` route：HTML 下載頁，LINE in-app browser 不會攔截
- Postback handler 加入 "schedule" action
- `docker-compose.yml` 加入 `generated_ics` volume mount + `TZ=Asia/Taipei`
- `.env` 加入 `BASE_URL`（ngrok URL）、`HOST_ICS_DIR`（本機路徑）

#### 圖片智慧分流
- `ocr_image()` → `ocr_and_classify()`：OCR + AI 自動判斷內容類型（schedule/wine_label/report/general）
- Quick Reply 改為動態：判定特定類型 → 2 按鈕（判定選項 + 其他）；general → 4 按鈕全展開
- 新增 `_all_action_items()` helper
- 新增 "show_all" postback：展開所有選項
- 新增 "wine" postback：酒標辨識 placeholder

#### 文字訊息自動行程偵測
- 新增 `classify_text()`：Claude Sonnet 判斷文字是否為行程（schedule/chat）
- `handle_text_message` 改為：先分類 → schedule 自動解析+產 .ics → 否則走正常聊天
- trade-off：每則文字多一次 API call（~0.5s），未來可改關鍵字預篩

#### ROADMAP 整理
- A2 改為 Claude API（原寫 GPT-4o）
- B1/B3/C1 已完成項目打勾
- 酒標辨識歸入 B1 子項
- 新增 Phase E（Obsidian 操作）、Phase F（語音處理）
- 散落的想法整理進正式結構

---

## 2026-03-14（週五）

### 00:18 [DESKTOP] 事實查核功能 + Rate Limit 對策 + ROADMAP 新增 Phase F

#### 「報告審稿」→「事實查核」全面重寫
- 舊功能只做校對錯字，改為上網求證事實
- 全部 UI label 改名：「報告審稿」→「事實查核」
- 新增 `identify_claims_for_check()`：AI 自動選取 10 個數字聲明 + 5 個重要事實
- 新增 `verify_claims_with_search()`：用 Anthropic `web_search_20250305` 工具上網搜尋求證
- 新增 `_run_fact_check()`：背景執行完整流程（因 LINE reply token 30 秒過期）
- 錯誤率 >20% 自動觸發深度查核（更多 web search）
- 使用 `push_message()` 非同步推送結果（不受 reply token 限制）
- 原 `proofread_text()` 保留但不再被呼叫

#### 長文字自動偵測
- `handle_text_message` 新增：>200 字元文字 → Quick Reply 問「事實查核 or 聊聊內容」
- 新增 `text_chat` postback handler

#### Rate Limit 對策
- 新增 `claude_api_call()` retry wrapper：所有 Claude API call 統一走此函式
- 遇到 429 → exponential backoff（30s → 60s → 120s，最多 3 次）
- 各 handler 加入 `anthropic.RateLimitError` 捕捉，回覆友善提示
- 背景：Anthropic Sonnet 30K input tokens/min，事實查核容易撞限

#### ROADMAP 更新
- 事實查核標記完成（B1 子項）
- 新增 Phase F：多模型 Fallback（Gemini 接入、multi-provider、Anthropic 升級評估）
- 語音處理順延為 Phase G
- 決策考量：Gemini 免費 250K TPM vs Anthropic 30K TPM，事實查核適合優先切 Gemini

---

## 待解決

- **ngrok 網址不固定** — 免費版每次重啟換網址，需手動更新 LINE Webhook URL
- **LINE 檔案過期** — LINE 伺服器上的檔案有時效限制，需即時下載

# LINE File Bot 開發記錄 — 流水帳
*始於 2026-02-14*

> **給未來 AI 的說明**
> 共用指引見 [`../shared/LOG_GUIDE.md`](../shared/LOG_GUIDE.md)
>
> **本專案補充：**
> - 遠端：origin/main ^ck-eafae9-0

---

## 2026-02-14：專案建立
^ck-f90754-2 ^ck-f7eb4f-2

### 初始建置
- 建立 `app.py` — Flask 主程式，處理 LINE Webhook、檔案下載
- 建立 `requirements.txt` — Flask、line-bot-sdk 3.14.0、Gunicorn
- 建立 `.python-version` — 指定 Python 3.12.6 ^ck-24f341-3

### 功能
- LINE Webhook 接收檔案訊息，自動下載到本地
- 支援 16 種檔案類型（PDF、Word、Excel、PPT、壓縮檔等）
- 檔名加時間戳避免覆蓋，特殊字元清理 ^ck-480e44-4

### 文件
- 新增 `README.md`：完整的 LINE Developer 設定、Render 部署、本地測試說明
- 新增 `.env.example`：環境變數範本 ^ck-73edd8-5

---

## 2026-02-15：Python 3.12 相容性修復
^ck-f9fb16-7 ^ck-fdaf49-7

### 問題
- line-bot-sdk 3.14.0 在 Python 3.12 上有語法問題（PEP 585 generics） ^ck-f20b5b-8

### 修復
- Python 版本確認為 3.12.6
- line-bot-sdk 從 3.14.0 升級到 3.22.0（支援 Python 3.10–3.14） ^ck-404924-9

---

## 2026-02-21：健康檢查端點
^ck-6fb124-11 ^ck-6194b6-11

### 新增
- 在 `/` 路徑加入 GET health check，回傳 "OK"
- 目的：配合 Render 免費方案的 uptime monitoring，避免服務休眠 ^ck-958491-12

---

## 2026-03-12：圖片接收 + GPT-4o 多模態分析 [NB]
^ck-af797a-14 ^ck-041d88-14

### 新增
- `handle_image_message`：接收 LINE 圖片訊息，下載圖片 bytes
- `analyze_image_with_gpt4o`：圖片 base64 編碼後送 GPT-4o，回傳繁體中文描述+文字擷取
- 新增環境變數 `OPENAI_API_KEY`
- `requirements.txt` 加入 `openai>=1.0.0` ^ck-ee9a4f-15

### 架構決策
- 圖片不落地儲存，直接記憶體中 base64 編碼送 API — 避免暫存檔管理
- GPT-4o 而非 Claude — 用戶指定
- Prompt 固定為繁體中文描述+文字擷取，Phase B 再加分流選單 ^ck-88c368-16

### 建立 ROADMAP.md
- Phase A（本次）：圖片 + AI 基礎
- Phase B：分流處理（圖片/PDF/行程）
- Phase C：互動強化（Quick Reply / Flex Message）
- Phase D：基礎設施（搬遷部署、持久化儲存） ^ck-310771-17

---

## 2026-03-12：B1 圖片分流 — OCR + Quick Reply + Postback [NB]
^ck-0a8050-19 ^ck-4e5205-19

### 新增
- `ocr_image()`：GPT-4o OCR 專用函數，提取圖片文字
- `proofread_text()`：GPT-4o 校對函數（錯字、標點、空白）
- `handle_postback()`：PostbackEvent handler，處理 Quick Reply 選擇
- In-memory session store（`_sessions` dict + TTL 10 分鐘 + threading lock）
- `reply_message()` 共用 helper，減少重複程式碼 ^ck-02fc17-20

### 變更
- `handle_image_message` 改為分流模式：OCR → 預覽 → Quick Reply
- 移除 `analyze_image_with_gpt4o()`（被 `ocr_image` 取代） ^ck-69c7bd-21

### 架構決策
- In-memory dict 暫存 OCR 結果 — Render 單 worker 夠用，重啟歸零可接受
- PostbackAction 而非 MessageAction — postback data 不會顯示在對話中，較乾淨
- OCR prompt 改為純文字提取（不做圖片描述），審稿/提取在 postback 階段才分流 ^ck-20e386-22

---

## 2026-03-12：OpenAI → Anthropic 遷移 [NB]
^ck-818f65-24 ^ck-c5df49-24

### 變更
- `requirements.txt`：`openai>=1.0.0` → `anthropic>=0.40.0`
- `app.py`：OpenAI SDK → Anthropic SDK
- `ocr_image()`、`proofread_text()` 改用 Claude Sonnet 4
- 環境變數：`OPENAI_API_KEY` → `ANTHROPIC_API_KEY` ^ck-a63e57-25

### 原因
- 用戶設定 OpenAI API Key 時遇到困難，改用 Anthropic ^ck-34726b-26

---

## 2026-03-12：文字對話功能 [NB]
^ck-db275e-28 ^ck-c0e1e3-28

### 新增
- `TextMessageContent` handler：接收文字訊息，送 Claude 對話
- `chat_with_claude()`：帶記憶的對話函數
- Chat history store（`_chat_history` dict）：每用戶最近 10 輪，30 分鐘 TTL
- 對話模型：Claude Opus 4.6
- System prompt：繁體中文助手，簡潔扼要 ^ck-be7298-29

### 架構決策
- 對話用 Opus 4.6（最強），OCR/審稿用 Sonnet 4（準確+快）
- Chat history 與 image session 分開存放，互不干擾 ^ck-3963dc-30

---

## 2026-03-13（週四）
^ck-fe2f83-32 ^ck-9da99b-32

### 22:34 [DESKTOP] D1 完成：Render → 本機部署（Docker + ngrok）
^ck-8caac7-33 ^ck-5d72d2-33

#### 背景
- Render 免費方案 15 分鐘無流量休眠，喚醒需 30-50 秒
- LINE reply token 30 秒過期，與 Render 喚醒時間衝突 → bot 經常無回應
- Render 現在要求綁信用卡，即使免費方案 ^ck-242e55-34

#### 本機部署
- 建立 `Dockerfile`：Python 3.12-slim + Gunicorn，單 worker
- 建立 `docker-compose.yml`：port 5000、env_file、downloaded_files volume mount
- 建立 `.dockerignore`：排除 .git、.env、.claude、markdown 等
- ngrok 3.37.2：建立 HTTPS 隧道，LINE Webhook URL 指向本機
- 測試通過：文字對話、圖片 OCR 皆正常 ^ck-4fd088-35

#### 其他變更
- `.env.example` 加入 `ANTHROPIC_API_KEY`
- `.gitignore` 加入 `.env`、`__pycache__/`、`*.pyc`、`downloaded_files/` ^ck-bd0bdc-36

#### 架構討論（未實作，記錄想法）
- **Obsidian vault 查詢**：bot 跑在本機可直接讀取 vault，用 Claude 分析報告內容
  - 意圖分流：Claude 判斷「查報告」vs「一般聊天」
  - 搜尋策略：glob + grep 找相關 markdown，塞進 Claude context 分析
  - 權限控制：只寫讀/寫函數，不給刪除，路徑鎖定 vault 目錄
- **PDF 生成 + LINE 傳檔**：分析結果可生成 PDF 回傳 LINE
- **bot 本質**：類似 OpenClaw 的本機 AI agent，但用 LINE 當介面、權限完全由程式碼控制 ^ck-91fdae-37

#### 注意
- ngrok 免費版每次重啟換網址，需重新更新 LINE Webhook URL
- 未來可考慮 Cloudflare Tunnel（免費固定域名）或 ngrok 付費方案 ^ck-31a0d7-38

### 23:32 [DESKTOP] B3 行程解析 + 圖片智慧分流 + 文字行程偵測
^ck-01c6cf-39 ^ck-be256c-39

#### B3 行程/會議解析（完成）
- 新增 `parse_schedule()`：Claude Sonnet 從文字提取行程 JSON
- 新增 `generate_ics()`：結構化 JSON → .ics 行事曆檔
  - 含 VTIMEZONE（Asia/Taipei）、METHOD:PUBLISH、建立時間戳記在 DESCRIPTION
- 新增 `format_schedule_text()`：行程 JSON → LINE 可讀摘要
- 新增 `/ics/<filename>` route：強制下載 .ics（避免 iOS 訂閱問題）
- 新增 `/cal/<file_id>` route：HTML 下載頁，LINE in-app browser 不會攔截
- Postback handler 加入 "schedule" action
- `docker-compose.yml` 加入 `generated_ics` volume mount + `TZ=Asia/Taipei`
- `.env` 加入 `BASE_URL`（ngrok URL）、`HOST_ICS_DIR`（本機路徑） ^ck-e6f2d2-40

#### 圖片智慧分流
- `ocr_image()` → `ocr_and_classify()`：OCR + AI 自動判斷內容類型（schedule/wine_label/report/general）
- Quick Reply 改為動態：判定特定類型 → 2 按鈕（判定選項 + 其他）；general → 4 按鈕全展開
- 新增 `_all_action_items()` helper
- 新增 "show_all" postback：展開所有選項
- 新增 "wine" postback：酒標辨識 placeholder ^ck-0c9a0c-41

#### 文字訊息自動行程偵測
- 新增 `classify_text()`：Claude Sonnet 判斷文字是否為行程（schedule/chat）
- `handle_text_message` 改為：先分類 → schedule 自動解析+產 .ics → 否則走正常聊天
- trade-off：每則文字多一次 API call（~0.5s），未來可改關鍵字預篩 ^ck-b06a0c-42

#### ROADMAP 整理
- A2 改為 Claude API（原寫 GPT-4o）
- B1/B3/C1 已完成項目打勾
- 酒標辨識歸入 B1 子項
- 新增 Phase E（Obsidian 操作）、Phase F（語音處理）
- 散落的想法整理進正式結構 ^ck-330494-43

---

## 2026-03-14（週五）
^ck-d83a25-45 ^ck-9ded8b-45

### 00:18 [DESKTOP] 事實查核功能 + Rate Limit 對策 + ROADMAP 新增 Phase F
^ck-35e046-46 ^ck-1c9c49-46

#### 「報告審稿」→「事實查核」全面重寫
- 舊功能只做校對錯字，改為上網求證事實
- 全部 UI label 改名：「報告審稿」→「事實查核」
- 新增 `identify_claims_for_check()`：AI 自動選取 10 個數字聲明 + 5 個重要事實
- 新增 `verify_claims_with_search()`：用 Anthropic `web_search_20250305` 工具上網搜尋求證
- 新增 `_run_fact_check()`：背景執行完整流程（因 LINE reply token 30 秒過期）
- 錯誤率 >20% 自動觸發深度查核（更多 web search）
- 使用 `push_message()` 非同步推送結果（不受 reply token 限制）
- 原 `proofread_text()` 保留但不再被呼叫 ^ck-fc94e3-47

#### 長文字自動偵測
- `handle_text_message` 新增：>200 字元文字 → Quick Reply 問「事實查核 or 聊聊內容」
- 新增 `text_chat` postback handler ^ck-6b58f5-48

#### Rate Limit 對策
- 新增 `claude_api_call()` retry wrapper：所有 Claude API call 統一走此函式
- 遇到 429 → exponential backoff（30s → 60s → 120s，最多 3 次）
- 各 handler 加入 `anthropic.RateLimitError` 捕捉，回覆友善提示
- 背景：Anthropic Sonnet 30K input tokens/min，事實查核容易撞限 ^ck-5554ec-49

#### ROADMAP 更新
- 事實查核標記完成（B1 子項）
- 新增 Phase F：多模型 Fallback（Gemini 接入、multi-provider、Anthropic 升級評估）
- 語音處理順延為 Phase G
- 決策考量：Gemini 免費 250K TPM vs Anthropic 30K TPM，事實查核適合優先切 Gemini ^ck-66fe2c-50

### 07:03 [DESKTOP] ROADMAP 整理：Obsidian 連結 + PDF 分流 + broker-reports Phase B
^ck-22d3c7-51 ^ck-7f72d7-51

#### line-file-bot ROADMAP
- 新增 E5：Obsidian 筆記連結（純文字摘要 + `obsidian://` URI + HTML 中繼頁）
- B2 PDF 分流加入前置說明：需先改 broker-reports 的 PDF 留存流程
- 「報告審核」→「事實查核」同步（B2 非券商報告選項） ^ck-cea8af-52

#### broker-reports ROADMAP（跨專案）
- 新增獨立 Phase B：PDF 本機留存（架構變更）
  - 現狀 PDF 處理完不留本機 → 改為留存在同步資料夾
  - 驅動來源：line-file-bot B2 PDF 分流需要本機有 PDF
- 原 B~J 全部順延為 C~K
- 決策：PDF 留存是架構性變更，獨立為高優先 Phase 而非塞進 Highlight 整合 ^ck-8f7e75-53

#### line-file-bot ROADMAP（續）
- 新增 B4：券商 Memo 處理（文字版）— 個股研究/產業/總經，Quick Reply 四選項（存檔/查核/兩者/自由輸入）
- 新增 Phase B：功能導覽（← 本輪）— 用戶輸入「說明」→ 列出已完成功能
- 原 B~G 全部順延為 C~H（子項編號同步更新） ^ck-5d9e73-54

### 09:11 [DESKTOP] ROADMAP 整理：停機恢復 + 待解決清理

- 新增 E3：停機恢復（Docker restart always + health check + 重啟通知 + D2 告警）
- 移除「LINE 檔案過期」待解決項目 — Webhook 本來就即時下載，非實際問題
- 停機恢復排序：放在 Phase E 基礎設施，優先度低於現有 E1/E2 ^ck-649cd6-55

---

## 2026-03-17（週二）
^ck-1e0ee0-57 ^ck-b016fa-57

### 19:54 [DESKTOP] Scratch 規則擴充

- CLAUDE.md scratch 規則更新：適用範圍從「程式腳本」擴大為「所有 session 臨時內容」（搜尋結果、計算過程、草稿文字）
- 副檔名規則：程式用 .py、文字用 .md ^ck-e86b55-58

---

## 2026-06-13（週六）

### 18:47 [Mac mini] 建 cross_todo `## line-file-bot` section + 發現 `/ics` path traversal 漏洞

- **起因**：open-session 時回報「cross_todo 無 line-file-bot 項目」，Sir 質疑——複查發現本專案長期無專屬 section，但散在 command-center / life-os / infra / shared 共 7 處被點名（多數 6/13 ADR-013 才拍板要動到本 repo code）。
- **decided**：在 `~/Projects/hub/cross_todo.md` 建 `## line-file-bot` section，owned 工項列全文、他 section owned 的只留 `^ck` 指標（不複製內文、避免 stale-list drift）。
- **安全發現（本 repo 自有 code）**：查證 ADR-013 點名的「`/ics`+`/cal` path traversal」屬實 — `serve_ics()`（`app.py:722-734`）把 URL 來的 `filename` 直接 `os.path.join(ICS_DIR, filename)` 後 `open()` 回傳，**無 `..`/絕對路徑收斂**，`os.path.isfile` 只擋不存在、擋不了逃逸 → 任意檔讀 primitive（可撈 `.env`，且 `/ics` 不在 webhook 後、不需驗章）。`/cal/<file_id>`（`:737-743`）因強制接 `.ics` 後綴讀取面較窄。修法：`os.path.realpath` 收斂進 `ICS_DIR` 才放行。已落 cross_todo `^ck-260613-linefilebot-path-traversal`（owned、可即刻 ship、與部署位置無關）。
- **對齊缺口**：本 repo ROADMAP（停在 Phase B/C，`last updated 2026-03-14`）沒記到 ADR-013 要把本 repo 成果層抽成共用 `line_adapter`（家在 life-os），已記入 cross_todo 待動工前補。
- 本 session 未改 line-file-bot code。 ^ck-260613-linefilebot-crosstodo

---

## 待解決

- **ngrok 網址不固定** — 免費版每次重啟換網址，需手動更新 LINE Webhook URL ^ck-b9bb7f-60

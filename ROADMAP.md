# ROADMAP — line-file-bot

*last updated: 2026-03-14*

## ✅ Phase A：圖片 + AI 基礎（最小起步）
- [x] A1. 加 ImageMessage handler — 接收圖片
- [x] A2. 接 Claude API — 圖片丟多模態模型（Sonnet OCR + Opus 對話）

## Phase B：功能導覽 ← 本輪
> 功能越來越多，用戶記不住 → 用簡單指令叫出目前已完成的功能清單
- [ ] B1. 「說明」或「功能」指令 → 回傳已完成功能一覽
  - 依輸入類型分類（圖片可以...、文字可以...、檔案可以...）
  - 動態產生：根據程式碼中實際啟用的 handler 自動列出
  - 簡潔易懂，非技術用語

## Phase C：分流處理
- [ ] C1. 圖片智慧分流
  - [x] 收到圖片 → OCR + AI 自動判斷內容類型（行程/酒標/報告/一般）
  - [x] 根據判定類型顯示精簡 Quick Reply（判定選項 + 其他）
  - [x] 文字提取基本功能（全文回傳）
  - [x] 報告審稿基本功能（校對錯字、標點、空白）
  - [ ] 文字提取進階：
    - 問「全部提取」or「選擇段落」
    - OCR 優先辨識 highlight / 標記的文字
    - 段落選擇：顯示段落預覽，用戶點選要哪些
  - [ ] 文字提取進階：來源辨識 + 存檔
    - 判斷內容類型（網頁截圖 / 電子書 / 券商報告 / 其他）
    - 網頁 → 找出原始網址
    - 報告 → 辨識名稱、券商、作者等 metadata
    - 同一批（30 分鐘內，或用戶指定）合併
    - 連同 highlight 文字 + metadata 存成 .md
  - [x] 事實查核（原「報告審稿進階」）
    - 自動挑選 10 個數字 + 5 個重要事實，用 web_search 工具上網求證
    - 錯誤率 >20% 自動深度查核
    - 背景執行 + push_message 非同步回傳結果
    - 長文字（>200 字元）自動偵測，Quick Reply 問「事實查核 or 聊聊內容」
  - [ ] 酒標辨識（框架已接好，待實作）
    - 辨識酒名 / 葡萄品種 / 產地
    - 回傳發音（酒名、葡萄、地名）
    - 在 Obsidian 建立 .md（分類：啤酒/白酒/紅酒/威士忌/清酒/其他）
    以 類型-酒名-年份-評論日期 的格式建立 (酒名請AI自行判斷完整名稱為何)
      方便同一酒多次品嘗的比較
    - 問用戶喝的感覺，記錄到 .md
    - 加入外界評論及連結
    品牌/地名/商標名/人名發音
      加入md
      有可能是精品/科技產品
- [ ] C2. PDF 智慧分流
  > ⚠️ 前置：需先改 [broker-reports](../broker-reports/) 流程（PDF 留存方式從 Gmail → 本機同步資料夾 + 畫記/書籤同步進 .md）→ 見該專案 ROADMAP
  - [ ] 收到 PDF（通常一批）→ 自動判斷是否為券商報告
  - [ ] **是券商報告** → 同一批一起問用戶：
    1. 快速摘要
    2. 詳細摘要
    3. 閱讀內文的文字擷取版（去除 ESG）
    4. 存檔至本機同步資料夾 + 去重 + 走 broker-reports 流程提取 MD
  - [ ] **非券商報告** → 問用戶：「1. 事實查核  2. 文字提取」
  - [ ] 去重機制：content hash 比對已處理檔案
- [x] C3. 行程/會議解析
  - [x] 偵測行程/會議相關內容（文字、圖片皆可）
  - [x] 文字訊息自動偵測行程（AI 分類 → 直接解析）
  - [x] 解析關鍵資訊：時間、人物、地點、議題
  - [x] 產出 .ics 行事曆檔（含 VTIMEZONE、建立時間戳記）
  - [x] HTML 下載頁避免 iOS 訂閱問題
  - [ ] 券商資訊後綴：行程標題自動加上「-元大」「-永豐」等
  - [ ] 人名對照表：券商 ↔ 營業員對照（JSON）
- [ ] C4. 券商 Memo 處理（文字版）
  - 內容類型：個股研究（~90%）、產業分析、總經評論
  - 收到長文字 → 自動偵測為券商 memo → Quick Reply 問用戶：
    1. 存檔進 Obsidian
    2. 事實查核
    3. 存檔 + 事實查核
    4. 自由輸入（用戶指定處理方式）
  - 依賴：F1（Obsidian 讀寫）、事實查核（已完成）

### Cross-cutting：多檔案自動關聯（C1/C2/C3 共用）
- [ ] 時間窗口內（2 小時）多檔案自動判斷是否同一場活動
- [ ] 手動關聯：超出時間窗口的檔案可手動指定
- [ ] 合併處理：交叉整合產出完整紀錄

## Phase D：互動強化
- [x] D1. LINE Quick Reply 選單（已用於圖片分流 + 行程解析）
- [ ] D1b. Flex Message 進階選單
- [ ] D2. 錯誤通知機制

## ✅ Phase E：基礎設施
- [x] E1. 從 Render 搬到本機/桌機（Docker + ngrok） ✅ 2026-03-13
- [ ] E2. 持久化儲存（Google Drive / S3）
- [ ] E3. 停機恢復
  > LINE Webhook 無法回溯拉取錯過的訊息，但可降低影響
  - Docker restart policy `always` + health check 自動重啟（已有基礎）
  - 停機偵測 + 重啟後主動通知用戶「我剛重啟，如有遺漏請重送」
  - 搭配 D2 錯誤通知：掛掉時即時告警

## Phase F：Obsidian 資料庫操作
> Vault 路徑：`C:\Users\User\Documents\Obsidian Vault`
- [ ] F1. 基本讀寫（讀、寫、輸出，不刪）
- [ ] F2. 主題整理（如「整理台積電個股報告」「整理美伊戰爭」）
- [ ] F3. 處理狀態通知（收到訊息回覆「處理中」，太久回報進度）
- [ ] F4. 結果輸出：LINE 傳文字版 + Obsidian 傳完整 .md
- [ ] F5. Obsidian 筆記連結
  - 寫入 .md 後回傳純文字摘要（簡版）+ 可點擊連結
  - `/note/<file_id>` HTML 中繼頁（複用 `/cal/` 模式，避免 LINE in-app browser 攔截 `obsidian://`）
  - Obsidian URI：`obsidian://open?vault=...&file=...`，手機+電腦都能直接開啟

## Phase G：多模型 Fallback（Rate Limit 對策）
> 背景：Anthropic Sonnet 目前 30K input tokens/min，事實查核容易撞限
- [ ] G1. Gemini API 接入（免費 250K TPM，事實查核優先切換）
- [ ] G2. Multi-provider fallback：Anthropic 429 → 自動切 Gemini
- [ ] G3. 評估 Anthropic 升級方案（Build tier $40-100/mo → 80K TPM）

## Phase H：語音處理
- [ ] H1. Whisper API 語音轉文字
- [ ] H2. 轉錄結果回傳 LINE

---

> 後端處理能力由 [doc-ingestion](../doc-ingestion/ROADMAP.md) 提供（PDF/圖片/文字 → 結構化 Markdown）
> 券商報告專用流程由 [broker-reports](../broker-reports/) 提供

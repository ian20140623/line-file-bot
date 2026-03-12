# ROADMAP — line-file-bot

*last updated: 2026-03-12*

## ✅ Phase A：圖片 + AI 基礎（最小起步）
- [x] A1. 加 ImageMessage handler — 接收圖片
- [x] A2. 接 GPT-4o — 圖片丟多模態模型，理解+回覆

## Phase B：分流處理
- [ ] B1. 圖片分流 — AI 判斷內容類型 → 問用戶「報告審稿」or「文字提取」（Quick Reply）
- [ ] B2. PDF 分流 — 判斷是否券商報告 → 接 doc-ingestion / broker-reports pipeline
- [ ] B3. 行程解析 — 偵測行程/會議內容 → 產出 .ics

## Phase C：互動強化
- [ ] C1. LINE Quick Reply / Flex Message 選單
- [ ] C2. 錯誤通知機制

## Phase D：基礎設施
- [ ] D1. 從 Render 搬到本機/桌機（ngrok / Cloudflare Tunnel 解決 webhook）
- [ ] D2. 持久化儲存（Google Drive / S3）

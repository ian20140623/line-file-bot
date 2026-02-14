# LINE Bot 自動下載文件檔案

當朋友在 LINE 群組或一對一聊天中傳送文件（PDF、Word、Excel 等），這個 Bot 會自動下載並儲存。

---

## 一、申請 LINE Developers 帳號與建立 Bot

### Step 1：註冊 LINE Developers

1. 前往 https://developers.line.biz/
2. 用你的 **LINE 帳號**登入（就是你平常用的那個 LINE）
3. 如果是第一次使用，會要求你建立一個「開發者帳號」，填入名稱和 email 即可

### Step 2：建立 Provider

1. 登入後，點擊「**Create a new provider**」
2. 輸入 Provider 名稱（例如：`我的Bot工具`），這只是分類用的

### Step 3：建立 Messaging API Channel

1. 在 Provider 下方，點擊「**Create a new channel**」
2. 選擇「**Messaging API**」
3. 填寫以下資訊：
   - **Channel name**：Bot 的名稱（例如：`檔案下載助手`）
   - **Channel description**：簡單描述（例如：`自動下載文件`）
   - **Category / Subcategory**：隨便選
   - **Email**：你的 email
4. 勾選同意條款，點擊「**Create**」

### Step 4：取得 Channel Secret 和 Access Token

1. 進入你剛建立的 Channel
2. 在「**Basic settings**」分頁，找到 **Channel secret** → 複製下來
3. 切換到「**Messaging API**」分頁：
   - 往下找到 **Channel access token**
   - 點擊「**Issue**」產生 token → 複製下來

### Step 5：關閉自動回覆

1. 在「**Messaging API**」分頁
2. 找到「**Auto-reply messages**」→ 點擊進入 LINE Official Account Manager
3. 將「**自動回應訊息**」設為「**停用**」
4. 將「**Webhook**」設為「**啟用**」

---

## 二、部署方式（推薦：Render 免費方案）

### 為什麼推薦 Render？

- **免費方案**可用（有限制但夠用）
- 不需要信用卡即可開始
- 部署流程非常簡單
- 自動提供 HTTPS 網址（LINE Webhook 必須要 HTTPS）

### 部署步驟

#### 1. 準備 GitHub Repo

將這個專案資料夾上傳到 GitHub（建立一個新的 repository）：

```bash
cd line-file-bot
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/你的帳號/line-file-bot.git
git push -u origin main
```

#### 2. 在 Render 上部署

1. 前往 https://render.com/ 並註冊帳號（可用 GitHub 登入）
2. 點擊「**New +**」→「**Web Service**」
3. 連結你的 GitHub repo
4. 設定：
   - **Name**：`line-file-bot`
   - **Runtime**：`Python 3`
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`gunicorn app:app`
5. 加入環境變數（**Environment Variables**）：
   - `LINE_CHANNEL_ACCESS_TOKEN` = 你的 token
   - `LINE_CHANNEL_SECRET` = 你的 secret
   - `DOWNLOAD_DIR` = `/tmp/downloaded_files`
6. 點擊「**Create Web Service**」

#### 3. 設定 LINE Webhook URL

1. 部署完成後，Render 會給你一個網址，例如：
   `https://line-file-bot-xxxx.onrender.com`
2. 回到 LINE Developers Console
3. 進入你的 Channel →「**Messaging API**」分頁
4. 在「**Webhook URL**」填入：
   `https://line-file-bot-xxxx.onrender.com/callback`
5. 點擊「**Update**」
6. 點擊「**Verify**」確認連線成功

#### 4. 加 Bot 為好友

1. 在「**Messaging API**」分頁找到 **QR code**
2. 用 LINE 掃描加入好友
3. 把 Bot 邀請到你想要監控的群組

---

## 三、使用方式

1. 把 Bot 加為好友或邀請到群組
2. 當有人傳送文件檔案（PDF、Word、Excel 等），Bot 會自動下載
3. Bot 會回覆一則訊息告訴你下載結果

### 支援的檔案類型

| 類型 | 副檔名 |
|------|--------|
| PDF | .pdf |
| Word | .doc, .docx |
| Excel | .xls, .xlsx |
| PowerPoint | .ppt, .pptx |
| 純文字 | .txt, .csv, .rtf |
| OpenDocument | .odt, .ods, .odp |
| 壓縮檔 | .zip, .rar, .7z |

---

## 四、注意事項

- **Render 免費方案限制**：服務閒置 15 分鐘後會休眠，下次收到訊息時需要約 30 秒喚醒。如果在意即時性，可升級付費方案（$7/月）。
- **檔案儲存**：Render 免費方案的磁碟是暫時性的（重啟後會清除）。若需長期保存，建議改用雲端儲存（如 Google Drive API 或 AWS S3）。
- **LINE 檔案過期**：LINE 伺服器上的檔案有保留期限，所以 Bot 需要盡快下載。
- **群組使用**：Bot 被加入群組後，才能收到群組內的檔案訊息。

---

## 五、本機測試（選用）

如果想先在本機測試：

```bash
# 安裝套件
pip install -r requirements.txt

# 設定環境變數
export LINE_CHANNEL_ACCESS_TOKEN="你的token"
export LINE_CHANNEL_SECRET="你的secret"

# 啟動
python app.py

# 用 ngrok 建立公開網址（另開一個終端）
ngrok http 5000
```

然後把 ngrok 給你的 HTTPS 網址 + `/callback` 填入 LINE Webhook URL。

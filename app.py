"""
LINE Bot - 自動下載文件檔案 + 圖片 AI 理解
- 文件：自動下載儲存
- 圖片：送 GPT-4o 多模態理解，回傳分析結果
"""

import os
import re
import base64
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent,
    FileMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
)
from linebot.v3.messaging.api import MessagingApiBlob
from openai import OpenAI

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloaded_files")

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp",
    ".zip", ".rar", ".7z",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)


def get_safe_filename(filename):
    filename = os.path.basename(filename)
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    return filename


def save_file(message_id, filename):
    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        content = blob_api.get_message_content(message_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = get_safe_filename(filename)
    name, ext = os.path.splitext(safe_name)
    final_name = f"{name}_{timestamp}{ext}"
    filepath = os.path.join(DOWNLOAD_DIR, final_name)
    with open(filepath, "wb") as f:
        f.write(content)
    logger.info(f"File saved: {filepath} ({len(content)} bytes)")
    return filepath


def is_document(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in DOCUMENT_EXTENSIONS


@app.route("/")
def health_check():
    return "OK"


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    logger.info("Received webhook request")
    try:
        handler.handle(body, signature)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=FileMessageContent)
def handle_file_message(event):
    message = event.message
    filename = message.file_name
    file_size = message.file_size
    logger.info(f"Received file: {filename} ({file_size} bytes)")
    if is_document(filename):
        try:
            filepath = save_file(message.id, filename)
            reply_text = f"Downloaded: {filename}\nPath: {filepath}"
        except Exception as e:
            logger.error(f"Download failed: {e}")
            reply_text = f"Download failed: {filename}\nReason: {str(e)}"
    else:
        ext = os.path.splitext(filename)[1]
        reply_text = f"Received {filename}, but {ext} is not in the auto-download list."
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


def analyze_image_with_gpt4o(image_bytes):
    if not openai_client:
        return "⚠️ OPENAI_API_KEY 未設定，無法分析圖片。"
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "請用繁體中文描述這張圖片的內容。如果圖片中有文字，請一併擷取出來。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}",
                        },
                    },
                ],
            }
        ],
        max_tokens=1000,
    )
    return response.choices[0].message.content


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    message_id = event.message.id
    logger.info(f"Received image: message_id={message_id}")
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_bytes = blob_api.get_message_content(message_id)
        logger.info(f"Image downloaded: {len(image_bytes)} bytes")
        reply_text = analyze_image_with_gpt4o(image_bytes)
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        reply_text = f"圖片分析失敗：{str(e)}"
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )


@handler.add(MessageEvent)
def handle_other_messages(event):
    pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"LINE Bot starting on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)

"""
LINE Bot - 自動下載文件檔案 + 圖片 AI 分流處理 + 文字對話
- 文件：自動下載儲存
- 圖片：OCR 預覽 → Quick Reply 選模式（報告審稿 / 文字提取）
- 文字：Claude 對話（帶記憶，最近 10 輪，30 分鐘 TTL）
"""

import os
import re
import json
import base64
import logging
import time
import threading
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
    QuickReply,
    QuickReplyItem,
    PostbackAction,
)
from linebot.v3.webhooks import (
    MessageEvent,
    PostbackEvent,
    FileMessageContent,
    ImageMessageContent,
    TextMessageContent,
    VideoMessageContent,
    AudioMessageContent,
)
from linebot.v3.messaging.api import MessagingApiBlob
import anthropic

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloaded_files")

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp",
    ".zip", ".rar", ".7z",
}

SESSION_TTL = 600  # 10 分鐘
CHAT_TTL = 1800  # 30 分鐘
CHAT_MAX_TURNS = 10  # 最多記住 10 輪對話

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

# --- In-memory session store ---
# key: user_id, value: {"ocr_text": str, "image_b64": str, "timestamp": float}
_sessions = {}
_sessions_lock = threading.Lock()


def session_set(user_id, ocr_text, image_b64):
    with _sessions_lock:
        _sessions[user_id] = {
            "ocr_text": ocr_text,
            "image_b64": image_b64,
            "timestamp": time.time(),
        }
    _session_cleanup()


def session_get(user_id):
    with _sessions_lock:
        data = _sessions.get(user_id)
        if data and (time.time() - data["timestamp"]) < SESSION_TTL:
            return data
        _sessions.pop(user_id, None)
        return None


def _session_cleanup():
    now = time.time()
    with _sessions_lock:
        expired = [k for k, v in _sessions.items() if now - v["timestamp"] >= SESSION_TTL]
        for k in expired:
            del _sessions[k]


# --- Chat history store ---
# key: user_id, value: {"messages": [{"role": str, "content": str}, ...], "timestamp": float}
_chat_history = {}
_chat_lock = threading.Lock()


def chat_history_append(user_id, role, content):
    now = time.time()
    with _chat_lock:
        if user_id not in _chat_history or (now - _chat_history[user_id]["timestamp"]) >= CHAT_TTL:
            _chat_history[user_id] = {"messages": [], "timestamp": now}
        history = _chat_history[user_id]
        history["messages"].append({"role": role, "content": content})
        # 保留最近 N 輪（每輪 = user + assistant = 2 筆）
        if len(history["messages"]) > CHAT_MAX_TURNS * 2:
            history["messages"] = history["messages"][-(CHAT_MAX_TURNS * 2):]
        history["timestamp"] = now


def chat_history_get(user_id):
    with _chat_lock:
        data = _chat_history.get(user_id)
        if data and (time.time() - data["timestamp"]) < CHAT_TTL:
            return data["messages"]
        _chat_history.pop(user_id, None)
        return []


# --- Utility functions ---


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


def reply_message(event, messages):
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages,
            )
        )


# --- Claude API functions ---


def ocr_image(image_b64):
    """OCR：提取圖片中的文字，回傳純文字結果。"""
    if not claude_client:
        return None, "ANTHROPIC_API_KEY 未設定，無法分析圖片。"
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "請提取這張圖片中的所有文字。"
                            "如果圖片沒有文字，請簡短描述圖片內容（一句話）。"
                            "只回傳提取結果，不要加任何說明或前綴。"
                        ),
                    },
                ],
            }
        ],
    )
    return response.content[0].text, None


def chat_with_claude(user_id, user_message):
    """文字對話：帶記憶的 Claude 對話。"""
    if not claude_client:
        return "ANTHROPIC_API_KEY 未設定。"
    chat_history_append(user_id, "user", user_message)
    messages = chat_history_get(user_id)
    response = claude_client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=1000,
        system="你是一個友善的繁體中文助手。回覆請簡潔扼要。",
        messages=messages,
    )
    reply = response.content[0].text
    chat_history_append(user_id, "assistant", reply)
    return reply


def proofread_text(text):
    """報告審稿：校對錯字、標點、空白。"""
    if not claude_client:
        return "ANTHROPIC_API_KEY 未設定。"
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=(
            "你是繁體中文校對專家。請針對以下文字進行：\n"
            "1. 錯字修正\n"
            "2. 標點符號修正\n"
            "3. 異常空白標記\n"
            "用「原文 → 修正」格式列出所有問題。"
            "如果沒有問題，回覆「未發現錯誤」。"
        ),
        messages=[
            {"role": "user", "content": text},
        ],
    )
    return response.content[0].text


# --- Route handlers ---


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


# --- Message handlers ---


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
    reply_message(event, [TextMessage(text=reply_text)])


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    message_id = event.message.id
    user_id = event.source.user_id
    logger.info(f"Received image: message_id={message_id}, user={user_id}")

    try:
        # 下載圖片
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_bytes = blob_api.get_message_content(message_id)
        logger.info(f"Image downloaded: {len(image_bytes)} bytes")

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # OCR
        ocr_text, error = ocr_image(image_b64)
        if error:
            reply_message(event, [TextMessage(text=f"⚠️ {error}")])
            return

        # 暫存 session
        session_set(user_id, ocr_text, image_b64)

        # 預覽文字（前 200 字）
        preview = ocr_text[:200]
        if len(ocr_text) > 200:
            preview += "..."

        # 回覆預覽 + Quick Reply
        reply_message(event, [
            TextMessage(
                text=f"📄 文字預覽：\n\n{preview}\n\n請選擇處理方式：",
                quick_reply=QuickReply(
                    items=[
                        QuickReplyItem(
                            action=PostbackAction(
                                label="📝 報告審稿",
                                data=json.dumps({"action": "proofread"}),
                                display_text="報告審稿",
                            )
                        ),
                        QuickReplyItem(
                            action=PostbackAction(
                                label="📋 文字提取",
                                data=json.dumps({"action": "extract"}),
                                display_text="文字提取",
                            )
                        ),
                    ]
                ),
            )
        ])

    except Exception as e:
        logger.error(f"Image processing failed: {e}")
        reply_message(event, [TextMessage(text=f"圖片處理失敗：{str(e)}")])


# --- Postback handler ---


@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    try:
        data = json.loads(event.postback.data)
    except (json.JSONDecodeError, AttributeError):
        reply_message(event, [TextMessage(text="操作無效，請重新傳送圖片。")])
        return

    action = data.get("action")
    session = session_get(user_id)

    if not session:
        reply_message(event, [TextMessage(text="⏰ 操作已過期，請重新傳送圖片。")])
        return

    ocr_text = session["ocr_text"]

    if action == "extract":
        # 文字提取：直接回傳全文
        # LINE 訊息上限 5000 字元
        if len(ocr_text) <= 5000:
            reply_message(event, [TextMessage(text=ocr_text)])
        else:
            # 分段回覆
            chunks = [ocr_text[i:i+5000] for i in range(0, len(ocr_text), 5000)]
            reply_message(event, [TextMessage(text=chunk) for chunk in chunks[:5]])

    elif action == "proofread":
        try:
            result = proofread_text(ocr_text)
            reply_message(event, [TextMessage(text=f"📝 審稿結果：\n\n{result}")])
        except Exception as e:
            logger.error(f"Proofread failed: {e}")
            reply_message(event, [TextMessage(text=f"審稿失敗：{str(e)}")])

    else:
        reply_message(event, [TextMessage(text="未知操作，請重新傳送圖片。")])


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    logger.info(f"Received text: user={user_id}, text={user_text[:50]}")
    try:
        reply = chat_with_claude(user_id, user_text)
        if len(reply) > 5000:
            reply = reply[:4997] + "..."
        reply_message(event, [TextMessage(text=reply)])
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        reply_message(event, [TextMessage(text=f"回覆失敗：{str(e)}")])


@handler.add(MessageEvent)
def handle_other_messages(event):
    pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"LINE Bot starting on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)

"""
LINE Bot - 自動下載文件檔案 + 圖片 AI 分流處理 + 文字對話 + 行程解析
- 文件：自動下載儲存
- 圖片：OCR 預覽 → Quick Reply 選模式（報告審稿 / 文字提取 / 行程解析）
- 文字：Claude 對話（帶記憶，最近 10 輪，30 分鐘 TTL）
- 行程：從文字或圖片解析會議資訊，產出 .ics 行事曆檔
"""

import os
import re
import json
import base64
import logging
import time
import uuid
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, request, abort, send_from_directory
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
ICS_DIR = os.environ.get("ICS_DIR", "./generated_ics")
BASE_URL = os.environ.get("BASE_URL", "")  # ngrok URL, e.g. https://xxx.ngrok-free.dev

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
Path(ICS_DIR).mkdir(parents=True, exist_ok=True)

# --- In-memory session store ---
# key: user_id, value: {"ocr_text": str, "image_b64": str, "content_type": str, "timestamp": float}
_sessions = {}
_sessions_lock = threading.Lock()


def session_set(user_id, ocr_text, image_b64, content_type="general"):
    with _sessions_lock:
        _sessions[user_id] = {
            "ocr_text": ocr_text,
            "image_b64": image_b64,
            "content_type": content_type,
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


def ocr_and_classify(image_b64):
    """OCR + 內容分類：提取文字並判斷圖片類型。回傳 (ocr_text, content_type, error)。
    content_type: "schedule" / "wine_label" / "report" / "general"
    """
    if not claude_client:
        return None, None, "ANTHROPIC_API_KEY 未設定，無法分析圖片。"
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
                            "請完成兩件事：\n"
                            "1. 提取圖片中的所有文字。如果圖片沒有文字，簡短描述圖片內容。\n"
                            "2. 判斷圖片內容類型，從以下選一個：\n"
                            "   - schedule：行程、會議邀請、行事曆截圖\n"
                            "   - wine_label：酒標、酒瓶、酒款資訊\n"
                            "   - report：券商報告、研究報告、新聞稿\n"
                            "   - general：其他（一般截圖、聊天記錄、筆記等）\n\n"
                            "回傳格式（嚴格遵守）：\n"
                            "第一行：類型（只寫 schedule / wine_label / report / general）\n"
                            "第二行起：提取的文字內容\n"
                            "不要加任何說明或前綴。"
                        ),
                    },
                ],
            }
        ],
    )
    raw = response.content[0].text
    lines = raw.strip().split("\n", 1)
    content_type = lines[0].strip().lower()
    if content_type not in ("schedule", "wine_label", "report", "general"):
        content_type = "general"
    ocr_text = lines[1].strip() if len(lines) > 1 else ""
    logger.info(f"Image classified as: {content_type}")
    return ocr_text, content_type, None


def classify_text(text):
    """判斷文字是否包含行程/會議資訊。回傳 content_type: "schedule" / "chat"。"""
    if not claude_client:
        return "chat"
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=10,
        messages=[
            {
                "role": "user",
                "content": (
                    "判斷以下文字是否包含具體的行程、會議、約會資訊"
                    "（有明確的日期/時間/事件）。\n"
                    "只回答一個字：schedule 或 chat\n\n"
                    f"{text[:500]}"
                ),
            }
        ],
    )
    result = response.content[0].text.strip().lower()
    logger.info(f"Text classified as: {result}")
    return "schedule" if "schedule" in result else "chat"


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


# --- Schedule parsing functions ---


def parse_schedule(text):
    """從文字中解析行程/會議資訊，回傳結構化 JSON。"""
    if not claude_client:
        return None, "ANTHROPIC_API_KEY 未設定。"
    response = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=(
            "你是行程解析專家。從用戶提供的文字中提取會議/行程資訊。\n"
            "回傳 JSON 格式，包含以下欄位：\n"
            '{"events": [{"title": "會議標題", "date": "YYYY-MM-DD", '
            '"start_time": "HH:MM", "end_time": "HH:MM", '
            '"location": "地點（如果有）", "people": ["人名1", "人名2"], '
            '"notes": "備註（如果有）"}]}\n'
            "規則：\n"
            "- 如果沒有明確結束時間，預設為開始後 1 小時\n"
            "- 如果只有日期沒有時間，start_time 設為 \"09:00\"，end_time 設為 \"10:00\"\n"
            "- 如果有多個行程，全部列出\n"
            "- 年份如果沒有明確指定，預設為今年（2026）\n"
            "- 如果文字中沒有任何行程/會議資訊，回傳 {\"events\": []}\n"
            "只回傳 JSON，不要加任何說明。"
        ),
        messages=[{"role": "user", "content": text}],
    )
    raw = response.content[0].text
    logger.info(f"Schedule parse raw response: {raw[:500]}")
    # Claude 有時會用 markdown code block 包裝 JSON
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        result = json.loads(cleaned)
        return result, None
    except json.JSONDecodeError as e:
        logger.error(f"Schedule JSON parse failed: {e}, raw: {raw[:500]}")
        return None, "解析失敗，無法辨識行程資訊。"


def generate_ics(events):
    """從解析結果產生 .ics 檔案，回傳檔名。"""
    created_at = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LINE File Bot//Schedule//TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Taipei",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for evt in events:
        uid = str(uuid.uuid4())
        dt_start = datetime.strptime(
            f"{evt['date']} {evt['start_time']}", "%Y-%m-%d %H:%M"
        )
        dt_end = datetime.strptime(
            f"{evt['date']} {evt['end_time']}", "%Y-%m-%d %H:%M"
        )
        now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

        # 組合 DESCRIPTION：參與者 + 備註 + 建立時間
        desc_parts = []
        if evt.get("people"):
            desc_parts.append(f"參與者：{', '.join(evt['people'])}")
        if evt.get("notes"):
            desc_parts.append(f"備註：{evt['notes']}")
        desc_parts.append(f"由 LINE Bot 建立於 {created_at}")
        description = "\\n".join(desc_parts)

        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{now}")
        lines.append(f"DTSTART;TZID=Asia/Taipei:{dt_start.strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"DTEND;TZID=Asia/Taipei:{dt_end.strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"SUMMARY:{evt['title']}")
        if evt.get("location"):
            lines.append(f"LOCATION:{evt['location']}")
        lines.append(f"DESCRIPTION:{description}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    filename = f"schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ics"
    filepath = os.path.join(ICS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines))
    logger.info(f"ICS file generated: {filepath}")
    return filename


def format_schedule_text(events):
    """將解析結果格式化為 LINE 訊息文字。"""
    if not events:
        return "未偵測到行程/會議資訊。"
    parts = ["📅 行程解析結果：\n"]
    for i, evt in enumerate(events, 1):
        parts.append(f"【{i}】{evt['title']}")
        parts.append(f"  📆 {evt['date']} {evt['start_time']}～{evt['end_time']}")
        if evt.get("location"):
            parts.append(f"  📍 {evt['location']}")
        if evt.get("people"):
            parts.append(f"  👥 {', '.join(evt['people'])}")
        if evt.get("notes"):
            parts.append(f"  📝 {evt['notes']}")
        parts.append("")
    return "\n".join(parts)


def _all_action_items():
    """所有可用的圖片處理選項。"""
    return [
        QuickReplyItem(
            action=PostbackAction(
                label="📅 行程解析", data=json.dumps({"action": "schedule"}),
                display_text="行程解析",
            )
        ),
        QuickReplyItem(
            action=PostbackAction(
                label="🍷 酒標辨識", data=json.dumps({"action": "wine"}),
                display_text="酒標辨識",
            )
        ),
        QuickReplyItem(
            action=PostbackAction(
                label="📝 報告審稿", data=json.dumps({"action": "proofread"}),
                display_text="報告審稿",
            )
        ),
        QuickReplyItem(
            action=PostbackAction(
                label="📋 文字提取", data=json.dumps({"action": "extract"}),
                display_text="文字提取",
            )
        ),
    ]


# --- Route handlers ---


@app.route("/")
def health_check():
    return "OK"


@app.route("/ics/<filename>")
def serve_ics(filename):
    """提供 .ics 檔案下載。"""
    from flask import make_response
    filepath = os.path.join(ICS_DIR, filename)
    if not os.path.isfile(filepath):
        abort(404)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    resp = make_response(content)
    resp.headers["Content-Type"] = "application/octet-stream"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.route("/cal/<file_id>")
def calendar_download_page(file_id):
    """HTML 下載頁，避免 LINE 攔截 .ics URL。"""
    filename = f"{file_id}.ics"
    filepath = os.path.join(ICS_DIR, filename)
    if not os.path.isfile(filepath):
        abort(404)
    base = BASE_URL.rstrip("/") if BASE_URL else request.host_url.rstrip("/")
    download_url = f"{base}/ics/{filename}"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>下載行事曆</title>
<style>
body{{font-family:-apple-system,sans-serif;text-align:center;padding:40px 20px;background:#f5f5f5}}
.btn{{display:inline-block;padding:16px 32px;background:#06C755;color:#fff;
text-decoration:none;border-radius:8px;font-size:18px;margin-top:20px}}
</style></head><body>
<h2>📅 行事曆事件</h2>
<p>點擊下方按鈕加入行事曆</p>
<a class="btn" href="{download_url}">加入行事曆</a>
</body></html>"""


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

        # OCR + 分類
        ocr_text, content_type, error = ocr_and_classify(image_b64)
        if error:
            reply_message(event, [TextMessage(text=f"⚠️ {error}")])
            return

        # 暫存 session（含分類結果）
        session_set(user_id, ocr_text, image_b64, content_type)

        # 預覽文字（前 200 字）
        preview = ocr_text[:200]
        if len(ocr_text) > 200:
            preview += "..."

        # 根據分類結果決定 Quick Reply
        # 主選項：AI 判定的類型；副選項：「其他」展開所有選項
        TYPE_CONFIG = {
            "schedule": ("📅 行程解析", "schedule"),
            "wine_label": ("🍷 酒標辨識", "wine"),
            "report": ("📝 報告審稿", "proofread"),
        }

        if content_type in TYPE_CONFIG:
            label, action = TYPE_CONFIG[content_type]
            quick_items = [
                QuickReplyItem(
                    action=PostbackAction(
                        label=label,
                        data=json.dumps({"action": action}),
                        display_text=label.split(" ", 1)[1],
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="🔄 其他選項",
                        data=json.dumps({"action": "show_all"}),
                        display_text="其他選項",
                    )
                ),
            ]
        else:
            # general：直接顯示所有選項
            quick_items = _all_action_items()

        reply_message(event, [
            TextMessage(
                text=f"📄 文字預覽：\n\n{preview}\n\n請選擇處理方式：",
                quick_reply=QuickReply(items=quick_items),
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

    elif action == "schedule":
        try:
            result, error = parse_schedule(ocr_text)
            if error:
                reply_message(event, [TextMessage(text=f"⚠️ {error}")])
                return
            events = result.get("events", [])
            summary = format_schedule_text(events)
            if events:
                filename = generate_ics(events)
                base = BASE_URL.rstrip("/") if BASE_URL else request.host_url.rstrip("/")
                # 用 /cal/ 頁面避免 LINE 攔截 .ics URL
                file_id = filename.replace(".ics", "")
                cal_url = f"{base}/cal/{file_id}"
                host_ics_dir = os.environ.get("HOST_ICS_DIR", "")
                summary += f"\n📎 加入行事曆：\n{cal_url}"
                if host_ics_dir:
                    summary += f"\n💻 本機：{os.path.join(host_ics_dir, filename)}"
            reply_message(event, [TextMessage(text=summary)])
        except Exception as e:
            logger.error(f"Schedule parsing failed: {e}")
            reply_message(event, [TextMessage(text=f"行程解析失敗：{str(e)}")])

    elif action == "show_all":
        # 展開所有選項
        reply_message(event, [
            TextMessage(
                text="請選擇處理方式：",
                quick_reply=QuickReply(items=_all_action_items()),
            )
        ])

    elif action == "wine":
        # 酒標辨識（暫時回傳提示，待完整實作）
        reply_message(event, [TextMessage(
            text="🍷 酒標辨識功能開發中，敬請期待！\n\n"
                 f"📄 目前提取的文字：\n{ocr_text[:2000]}"
        )])

    else:
        reply_message(event, [TextMessage(text="未知操作，請重新傳送圖片。")])


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text
    logger.info(f"Received text: user={user_id}, text={user_text[:50]}")
    try:
        # 先判斷是否為行程訊息
        text_type = classify_text(user_text)
        if text_type == "schedule":
            result, error = parse_schedule(user_text)
            if error:
                reply_message(event, [TextMessage(text=f"⚠️ {error}")])
                return
            events = result.get("events", [])
            if events:
                summary = format_schedule_text(events)
                filename = generate_ics(events)
                base = BASE_URL.rstrip("/") if BASE_URL else request.host_url.rstrip("/")
                file_id = filename.replace(".ics", "")
                cal_url = f"{base}/cal/{file_id}"
                host_ics_dir = os.environ.get("HOST_ICS_DIR", "")
                summary += f"\n📎 加入行事曆：\n{cal_url}"
                if host_ics_dir:
                    summary += f"\n💻 本機：{os.path.join(host_ics_dir, filename)}"
                reply_message(event, [TextMessage(text=summary)])
                return
        # 一般對話
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

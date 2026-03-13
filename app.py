"""
LINE Bot - 自動下載文件檔案 + 圖片 AI 分流處理 + 文字對話 + 行程解析
- 文件：自動下載儲存
- 圖片：OCR 預覽 → Quick Reply 選模式（事實查核 / 文字提取 / 行程解析）
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
    PushMessageRequest,
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
            "claims": [],  # 事實聲明（審稿後填入）
            "timestamp": time.time(),
        }
    _session_cleanup()


def session_update(user_id, **kwargs):
    """更新 session 中的指定欄位。"""
    with _sessions_lock:
        data = _sessions.get(user_id)
        if data and (time.time() - data["timestamp"]) < SESSION_TTL:
            data.update(kwargs)
            data["timestamp"] = time.time()


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


# --- API retry wrapper ---


def claude_api_call(create_kwargs, max_retries=3):
    """呼叫 Claude API，遇到 429 rate limit 自動重試（exponential backoff）。
    create_kwargs: 傳給 claude_client.messages.create() 的參數 dict。
    回傳 response 或 raise exception。
    """
    for attempt in range(max_retries):
        try:
            return claude_client.messages.create(**create_kwargs)
        except anthropic.RateLimitError as e:
            wait = 2 ** attempt * 30  # 30s, 60s, 120s
            logger.warning(f"Rate limit hit (attempt {attempt + 1}/{max_retries}), waiting {wait}s...")
            time.sleep(wait)
            if attempt == max_retries - 1:
                raise
        except anthropic.APIStatusError as e:
            if e.status_code == 429:
                wait = 2 ** attempt * 30
                logger.warning(f"429 error (attempt {attempt + 1}/{max_retries}), waiting {wait}s...")
                time.sleep(wait)
                if attempt == max_retries - 1:
                    raise
            else:
                raise


RATE_LIMIT_MSG = "⏳ API 流量限制中，請稍候 30 秒再試一次。"


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


def push_message(user_id, messages):
    """主動推送訊息給用戶（不需 reply token）。"""
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.push_message(
            PushMessageRequest(to=user_id, messages=messages)
        )


# --- Claude API functions ---


def ocr_and_classify(image_b64):
    """OCR + 內容分類：提取文字並判斷圖片類型。回傳 (ocr_text, content_type, error)。
    content_type: "schedule" / "wine_label" / "report" / "general"
    """
    if not claude_client:
        return None, None, "ANTHROPIC_API_KEY 未設定，無法分析圖片。"
    response = claude_api_call({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "messages": [
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
    })
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
    response = claude_api_call({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 10,
        "messages": [{
            "role": "user",
            "content": (
                "判斷以下文字是否包含具體的行程、會議、約會資訊"
                "（有明確的日期/時間/事件）。\n"
                "只回答一個字：schedule 或 chat\n\n"
                f"{text[:500]}"
            ),
        }],
    })
    result = response.content[0].text.strip().lower()
    logger.info(f"Text classified as: {result}")
    return "schedule" if "schedule" in result else "chat"


def chat_with_claude(user_id, user_message):
    """文字對話：帶記憶的 Claude 對話。"""
    if not claude_client:
        return "ANTHROPIC_API_KEY 未設定。"
    chat_history_append(user_id, "user", user_message)
    messages = chat_history_get(user_id)
    response = claude_api_call({
        "model": "claude-opus-4-20250514",
        "max_tokens": 1000,
        "system": "你是一個友善的繁體中文助手。回覆請簡潔扼要。",
        "messages": messages,
    })
    reply = response.content[0].text
    chat_history_append(user_id, "assistant", reply)
    return reply


def proofread_text(text):
    """報告審稿：校對錯字、標點、空白。"""
    if not claude_client:
        return "ANTHROPIC_API_KEY 未設定。"
    response = claude_api_call({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "system": (
            "你是繁體中文校對專家。請針對以下文字進行：\n"
            "1. 錯字修正\n"
            "2. 標點符號修正\n"
            "3. 異常空白標記\n"
            "用「原文 → 修正」格式列出所有問題。"
            "如果沒有問題，回覆「未發現錯誤」。"
        ),
        "messages": [
            {"role": "user", "content": text},
        ],
    })
    return response.content[0].text


def identify_claims_for_check(text):
    """識別文中事實聲明，自動選取查核目標。
    數字相關：最多 10 個（不足全選）；其他重要事實：最重要 5 個。
    回傳 (number_claims, fact_claims)
    """
    if not claude_client:
        return [], []
    response = claude_api_call({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 3000,
        "messages": [{
            "role": "user",
            "content": (
                "分析以下文字，找出所有可查核的事實聲明。\n\n"
                "分兩類輸出：\n\n"
                "【數字類】含具體數字的聲明（營收、成長率、市佔率、價格、數量等）。\n"
                "選取規則：最多 10 個，不足 10 個就全部列出。\n\n"
                "【事實類】不含數字但可查核的事實（事件、政策、人事、引述等）。\n"
                "選取規則：選最重要的 5 個。\n\n"
                "輸出格式（嚴格遵守，不要加多餘說明）：\n"
                "===NUMBER===\n"
                "1|聲明摘要|原文片段\n"
                "2|聲明摘要|原文片段\n"
                "===FACT===\n"
                "1|聲明摘要|原文片段\n"
                "2|聲明摘要|原文片段\n\n"
                "如果某類別沒有聲明，該區塊寫 NONE\n\n"
                f"文字內容：\n{text}"
            ),
        }],
    })
    raw = response.content[0].text.strip()
    number_claims = []
    fact_claims = []
    current_section = None

    for line in raw.split("\n"):
        line = line.strip()
        if "===NUMBER===" in line:
            current_section = "number"
            continue
        elif "===FACT===" in line:
            current_section = "fact"
            continue
        if not line or line == "NONE":
            continue
        parts = line.split("|", 2)
        if len(parts) >= 2:
            try:
                claim_id = int(parts[0].strip())
            except ValueError:
                continue
            claim = {
                "id": claim_id,
                "claim": parts[1].strip(),
                "source": parts[2].strip() if len(parts) > 2 else "",
            }
            if current_section == "number":
                number_claims.append(claim)
            elif current_section == "fact":
                fact_claims.append(claim)

    logger.info(f"Claims identified: {len(number_claims)} numbers, {len(fact_claims)} facts")
    return number_claims, fact_claims


def verify_claims_with_search(number_claims, fact_claims, original_text):
    """使用網路搜尋查核事實聲明。回傳 (report_text, stats)。"""
    if not claude_client:
        return "ANTHROPIC_API_KEY 未設定。", {}

    # 組裝聲明列表（統一編號）
    claims_parts = []
    global_id = 0
    if number_claims:
        claims_parts.append("【數字類聲明】")
        for c in number_claims:
            global_id += 1
            claims_parts.append(f"{global_id}. {c['claim']}")
            if c['source']:
                claims_parts.append(f"   原文：「{c['source'][:100]}」")
    if fact_claims:
        claims_parts.append("\n【事實類聲明】")
        for c in fact_claims:
            global_id += 1
            claims_parts.append(f"{global_id}. {c['claim']}")
            if c['source']:
                claims_parts.append(f"   原文：「{c['source'][:100]}」")

    claims_str = "\n".join(claims_parts)

    response = claude_api_call({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 16000,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 20}],
        "messages": [{
            "role": "user",
            "content": (
                "你是事實查核專家。請逐一上網搜尋求證以下聲明。\n\n"
                "對每個聲明：\n"
                "1. 用網路搜尋找相關新聞、報告、官方資料\n"
                "2. 判斷：✅ 已確認 / ⚠️ 無法確認 / ❌ 有誤\n"
                "3. 簡述佐證或反證\n"
                "4. 附上參考來源網址\n\n"
                f"原文摘要：\n{original_text[:1000]}\n\n"
                f"需查核的聲明：\n{claims_str}\n\n"
                "回覆用繁體中文。"
            ),
        }],
    })

    # 從回應中提取文字（跳過 web search 結果 block）
    result_text = ""
    for block in response.content:
        if hasattr(block, "type") and block.type == "text":
            result_text += block.text

    # 統計判定結果
    stats = {
        "confirmed": result_text.count("✅"),
        "uncertain": result_text.count("⚠️"),
        "wrong": result_text.count("❌"),
    }
    logger.info(f"Fact check stats: {stats}")
    return result_text, stats


def _run_fact_check(user_id, text):
    """背景執行事實查核完整流程。"""
    try:
        # Step 1: 識別並選取聲明
        number_claims, fact_claims = identify_claims_for_check(text)
        total_selected = len(number_claims) + len(fact_claims)

        if total_selected == 0:
            push_message(user_id, [TextMessage(
                text="🔎 未在文字中偵測到可查核的事實聲明。"
            )])
            return

        push_message(user_id, [TextMessage(
            text=f"🔎 找到 {len(number_claims)} 個數字聲明 + {len(fact_claims)} 個事實聲明，正在上網查核..."
        )])

        # Step 2: 上網查核
        report, stats = verify_claims_with_search(number_claims, fact_claims, text)

        total_checked = sum(stats.values())
        error_rate = stats["wrong"] / total_checked if total_checked > 0 else 0

        # Step 3: 組裝報告
        summary = (
            f"\n━━ 查核摘要 ━━\n"
            f"✅ 已確認：{stats['confirmed']} 項\n"
            f"⚠️ 無法確認：{stats['uncertain']} 項\n"
            f"❌ 有誤：{stats['wrong']} 項\n"
            f"錯誤率：{error_rate:.0%}"
        )
        full_report = (
            f"🔍 事實查核報告\n"
            f"查核 {total_checked} 項（數字 {len(number_claims)} + 事實 {len(fact_claims)}）\n\n"
            f"{report}\n{summary}"
        )

        # LINE 5000 字元限制 — 分段送出
        if len(full_report) <= 5000:
            push_message(user_id, [TextMessage(text=full_report)])
        else:
            chunks = [full_report[i:i + 4900] for i in range(0, len(full_report), 4900)]
            for chunk in chunks[:5]:
                push_message(user_id, [TextMessage(text=chunk)])

        # Step 4: 高錯誤率 → 主動更深入查核
        if error_rate > 0.2 and total_checked > 0:
            push_message(user_id, [TextMessage(
                text=f"⚠️ 錯誤率 {error_rate:.0%} 超過 20%！正在進行更深入的查核..."
            )])

            deeper_response = claude_api_call({
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 16000,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 30}],
                "messages": [{
                    "role": "user",
                    "content": (
                        "前一輪事實查核發現錯誤率超過 20%，需要更深入查核。\n"
                        "請重新檢視以下文字中的所有數字和事實聲明，不限數量。\n"
                        "特別注意與已確認有誤的聲明相似的模式。\n\n"
                        f"原文：\n{text}\n\n"
                        f"前一輪結果：\n{summary}\n\n"
                        "對每個新發現的問題：\n"
                        "1. 上網搜尋求證\n"
                        "2. 判斷：✅ 已確認 / ⚠️ 無法確認 / ❌ 有誤\n"
                        "3. 附上佐證來源\n\n"
                        "回覆用繁體中文。"
                    ),
                }],
            })

            deeper_text = ""
            for block in deeper_response.content:
                if hasattr(block, "type") and block.type == "text":
                    deeper_text += block.text

            deeper_stats = {
                "confirmed": deeper_text.count("✅"),
                "uncertain": deeper_text.count("⚠️"),
                "wrong": deeper_text.count("❌"),
            }
            deeper_report = (
                f"🔍 深度查核結果\n"
                f"額外查核：✅{deeper_stats['confirmed']} ⚠️{deeper_stats['uncertain']} ❌{deeper_stats['wrong']}\n\n"
                f"{deeper_text}"
            )
            if len(deeper_report) <= 5000:
                push_message(user_id, [TextMessage(text=deeper_report)])
            else:
                chunks = [deeper_report[i:i + 4900] for i in range(0, len(deeper_report), 4900)]
                for chunk in chunks[:5]:
                    push_message(user_id, [TextMessage(text=chunk)])

    except Exception as e:
        logger.error(f"Fact check failed: {e}")
        push_message(user_id, [TextMessage(text=f"事實查核失敗：{str(e)}")])


# --- Schedule parsing functions ---


def parse_schedule(text):
    """從文字中解析行程/會議資訊，回傳結構化 JSON。"""
    if not claude_client:
        return None, "ANTHROPIC_API_KEY 未設定。"
    response = claude_api_call({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "system": (
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
        "messages": [{"role": "user", "content": text}],
    })
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
                label="🔍 事實查核", data=json.dumps({"action": "proofread"}),
                display_text="事實查核",
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
            "report": ("🔍 事實查核", "proofread"),
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

    except anthropic.RateLimitError:
        reply_message(event, [TextMessage(text=RATE_LIMIT_MSG)])
    except Exception as e:
        logger.error(f"Image processing failed: {e}")
        if "rate_limit" in str(e).lower():
            reply_message(event, [TextMessage(text=RATE_LIMIT_MSG)])
        else:
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
        # 事實查核：立即回覆 → 背景上網求證 → push 結果
        reply_message(event, [TextMessage(
            text="🔎 收到，正在進行事實查核...\n大約需要 1-2 分鐘，完成後會主動通知你。"
        )])
        threading.Thread(
            target=_run_fact_check,
            args=(user_id, ocr_text),
            daemon=True,
        ).start()

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

    elif action == "text_chat":
        # 長文字 → 用戶選擇聊天
        try:
            reply = chat_with_claude(user_id, ocr_text)
            if len(reply) > 5000:
                reply = reply[:4997] + "..."
            reply_message(event, [TextMessage(text=reply)])
        except Exception as e:
            logger.error(f"Chat failed: {e}")
            reply_message(event, [TextMessage(text=f"回覆失敗：{str(e)}")])

    else:
        reply_message(event, [TextMessage(text="未知操作，請重新傳送。")])


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
        # 長文字 → 先問用戶：事實查核 or 聊天
        if len(user_text) > 200:
            session_set(user_id, user_text, "", content_type="long_text")
            preview = user_text[:150] + "..." if len(user_text) > 150 else user_text
            reply_message(event, [
                TextMessage(
                    text=f"📄 收到一段文字（{len(user_text)} 字）：\n\n{preview}\n\n請問你想：",
                    quick_reply=QuickReply(items=[
                        QuickReplyItem(
                            action=PostbackAction(
                                label="🔍 事實查核",
                                data=json.dumps({"action": "proofread"}),
                                display_text="事實查核",
                            )
                        ),
                        QuickReplyItem(
                            action=PostbackAction(
                                label="💬 聊聊內容",
                                data=json.dumps({"action": "text_chat"}),
                                display_text="聊聊內容",
                            )
                        ),
                    ]),
                )
            ])
            return

        # 一般對話（短文字）
        reply = chat_with_claude(user_id, user_text)
        if len(reply) > 5000:
            reply = reply[:4997] + "..."
        reply_message(event, [TextMessage(text=reply)])
    except anthropic.RateLimitError:
        reply_message(event, [TextMessage(text=RATE_LIMIT_MSG)])
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        if "rate_limit" in str(e).lower():
            reply_message(event, [TextMessage(text=RATE_LIMIT_MSG)])
        else:
            reply_message(event, [TextMessage(text=f"回覆失敗：{str(e)}")])


@handler.add(MessageEvent)
def handle_other_messages(event):
    pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"LINE Bot starting on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)

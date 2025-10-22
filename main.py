# main.py — покращена версія
import os
import io
import time
import datetime
import re
import threading
import requests
import json
from queue import Queue, Empty
from flask import Flask, request, Response

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.rich_media_message import RichMediaMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

# ============ CONFIG ============
VIBER_TOKEN = os.environ.get("VIBER_TOKEN") or "PUT_YOUR_TOKEN"
VISION_API_KEY = os.environ.get("VISION_API_KEY") or "PUT_YOUR_VISION_KEY"
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID") or "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID") or "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_TOKEN_FILE = os.environ.get("GOOGLE_TOKEN_FILE", "token.json")
DAILY_LIMIT_DEFAULT = int(os.environ.get("DAILY_LIMIT_DEFAULT", 8))
ADMIN_ID = os.environ.get("ADMIN_ID") or "uJBIST3PYaJLoflfY/9zkQ=="

WORKER_COUNT = int(os.environ.get("WORKER_COUNT", 2))
DELETE_SHEET_INTERVAL = int(os.environ.get("DELETE_SHEET_INTERVAL", 180))  # sec
USERS_CACHE_TTL = int(os.environ.get("USERS_CACHE_TTL", 25))  # sec
TOKENS_TTL = int(os.environ.get("TOKENS_TTL", 300))  # sec - keep tokens for 5 min

# ============ INIT ============
app = Flask(__name__)
viber = Api(BotConfiguration(name='Джексон🤖', avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png', auth_token=VIBER_TOKEN))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, ['https://www.googleapis.com/auth/drive.file','https://www.googleapis.com/auth/spreadsheets'])
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# ============ STATE ============
task_queue = Queue()
props = {}  # sheet_name -> created_ts_ms
pending_reports = {}  # file_name -> public_url
users_cache = {"ts": 0, "rows": []}  # cached rows of Лист1!A:D
processed_tokens = {}  # token -> ts

logfile = "/tmp/viber_bot_debug.log"
def log(*args):
    line = f"[{datetime.datetime.utcnow().isoformat()}] " + " ".join(map(str,args))
    print(line, flush=True)
    try:
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ============ Helpers: safe_execute with retries ============
def safe_execute(callable_request, retries=3, backoff=1.0):
    """callable_request() should return a request object with .execute() or a function that executes"""
    for attempt in range(1, retries+1):
        try:
            result = callable_request().execute() if hasattr(callable_request(), "execute") else callable_request()
            return result
        except HttpError as he:
            log("[Google API HttpError]", he)
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)
        except Exception as e:
            log("[Google API] retry", attempt, "due to", e)
            if attempt == retries:
                raise
            time.sleep(backoff * attempt)

# ============ Users cache ============
def load_users_cached():
    now = time.time()
    if now - users_cache["ts"] < USERS_CACHE_TTL and users_cache["rows"]:
        return users_cache["rows"]
    try:
        res = safe_execute(lambda: sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="Лист1!A:D"))
        rows = res.get("values", []) or []
        users_cache["rows"] = rows
        users_cache["ts"] = now
        return rows
    except Exception as e:
        log("load_users_cached error", e)
        return users_cache["rows"]

def invalidate_users_cache():
    users_cache["ts"] = 0

# ============ processed_tokens cleanup ============
def add_processed_token(token):
    processed_tokens[token] = time.time()

def cleanup_tokens_worker():
    while True:
        now = time.time()
        to_del = [t for t,ts in processed_tokens.items() if now - ts > TOKENS_TTL]
        for t in to_del:
            processed_tokens.pop(t, None)
        time.sleep(60)

# ============ Sheet name sanitizer & create ============
_MAX_SHEET_NAME_LEN = 100
_BAD_CHARS = re.compile(r'[:\\/?*\[\]]')  # chars Google forbids in sheet titles

def sanitize_sheet_name(name):
    s = _BAD_CHARS.sub("", name)
    s = s[:_MAX_SHEET_NAME_LEN]
    if not s:
        s = "sheet"
    return s

def create_sheet_if_not_exists(sheet_id, sheet_name):
    sheet_name = sanitize_sheet_name(sheet_name)
    try:
        ss = safe_execute(lambda: sheets_service.spreadsheets().get(spreadsheetId=sheet_id))
        existing = [s['properties']['title'] for s in ss.get("sheets", [])]
        if sheet_name in existing:
            return sheet_name
        body = {"requests":[{"addSheet":{"properties":{"title": sheet_name}}}]}
        safe_execute(lambda: sheets_service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body))
        return sheet_name
    except Exception as e:
        log("create_sheet_if_not_exists error", e)
        return None

# ============ Vision API (with retry) ============
def vision_detect_text_from_bytes(img_bytes, retries=2, timeout=15):
    import base64
    content = base64.b64encode(img_bytes).decode('utf-8')
    payload = {"requests":[{"image":{"content":content},"features":[{"type":"TEXT_DETECTION"}]}]}
    url = f"https://vision.googleapis.com/v1/images:annotate?key={VISION_API_KEY}"
    for attempt in range(1, retries+1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            js = r.json()
            return js.get('responses', [{}])[0].get('fullTextAnnotation', {}).get('text', '') or ''
        except Exception as e:
            log("Vision API attempt", attempt, "error", e)
            if attempt == retries:
                return ""
            time.sleep(1 * attempt)
    return ""

# ============ Barcode filtering (your original logic) ============
def filter_barcodes_from_text(text):
    clean = text.replace("O","0").replace("I","1").replace("L","1")
    raw = re.findall(r"\d{8,20}", clean)
    forbidden_prefixes = ["00","1","436","202","22","403","675","459","311","377","391","2105","451","288","240","442","044","363","971","097","044","44","536","053","82","066","66","29","36","46","38","43","26","39","35","53","30","67","063","63","0674","674","0675","675","319","086","86","095","9508","11","21","050","507","6721","06721","2309","999","249","9798"]
    out=[]
    for c in raw:
        if c in out: continue
        if len(c) not in [8,10,12,13,14,18]: continue
        if (len(c) in [8,13]) and not is_valid_ean(c): continue
        if any(c.startswith(p) for p in forbidden_prefixes): continue
        out.append(c)
    return out

def is_valid_ean(code):
    try:
        digits = [int(d) for d in code]
    except:
        return False
    if len(digits)==13:
        s=sum(d*(3 if i%2 else 1) for i,d in enumerate(digits[:-1]))
    elif len(digits)==8:
        s=sum(d*(1 if i%2 else 3) for i,d in enumerate(digits[:-1]))
    else:
        return False
    return (10-(s%10))%10==digits[-1]

# ============ Queue worker ============
def process_task(user_id, file_bytes, file_name):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_base = f"photo_{ts}"
    ext = file_name.split('.')[-1] if '.' in file_name else "jpg"

    if not file_bytes:
        log("Empty file_bytes, skipping", file_name)
        try:
            viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка: файл пустий {file_name}")])
        except: pass
        return

    # save debug copy locally
    try:
        with open(f"/tmp/{file_base}.{ext}", "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        log("write local debug failed", e)

    # upload to drive
    try:
        gfile = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=f"image/{ext}")
        resp = safe_execute(lambda: drive_service.files().create(body={'name':f"{file_base}.{ext}", 'parents':[GDRIVE_FOLDER_ID]}, media_body=gfile, fields='id'))
        file_id = resp.get('id')
        add_public_permission(file_id)
        public_url = f"https://drive.google.com/uc?id={file_id}"
    except Exception as e:
        log("Drive upload error", e)
        try: viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка завантаження на Drive: {e}")])
        except: pass
        return

    # Vision -> barcodes
    try:
        text = vision_detect_text_from_bytes(file_bytes)
        barcodes = filter_barcodes_from_text(text) if text else []
    except Exception as e:
        log("Vision/error", e)
        barcodes = []

    # create sheet and write values
    try:
        sheet_name = create_sheet_if_not_exists(SPREADSHEET_ID, file_base)
        if not sheet_name:
            log("Failed to create sheet", file_base)
        else:
            values = [[b] for b in barcodes] if barcodes else [["Штрихкодів не знайдено"]]
            safe_execute(lambda: sheets_service.spreadsheets().values().update(spreadsheetId=SPREADSHEET_ID, range=f"{sheet_name}!A1", valueInputOption="RAW", body={"values":values}))
            props[sheet_name] = int(time.time() * 1000)
    except Exception as e:
        log("Sheets write error", e)

    # send photo
    try:
        viber.send_messages(user_id, [PictureMessage(media=public_url, text=file_name)])
        pending_reports[file_name] = public_url
    except Exception as e:
        log("Viber send photo error", e)

    # send richmedia button (with fallback)
    try:
        rich_media_dict = {
            "Type": "rich_media",
            "ButtonsGroupColumns": 6,
            "ButtonsGroupRows": 1,
            "BgColor": "#FFFFFF",
            "Buttons": [
                {
                    "Columns": 6, "Rows": 1,
                    "ActionType": "reply", "ActionBody": f"report_{file_name}",
                    "Text": "⚠️ Скарга",
                    "TextSize": "medium", "TextVAlign": "middle", "TextHAlign": "center",
                    "BgColor": "#ff6666", "TextOpacity": 100, "TextColor": "#FFFFFF"
                }
            ]
        }
        viber.send_messages(user_id, [RichMediaMessage(rich_media=rich_media_dict, min_api_version=2, alt_text="Скарга")])
    except Exception as e:
        log("RichMedia failed, fallback to text:", e)
        try:
            viber.send_messages(user_id, [TextMessage(text=f"⚠️ Щоб поскаржитись, відправ: report_{file_name}")])
        except: pass

    # send barcodes text
    try:
        text_msg = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
        viber.send_messages(user_id, [TextMessage(text=text_msg)])
    except Exception as e:
        log("Viber send barcode text error", e)

def worker_loop():
    while True:
        try:
            user_id, file_bytes, file_name = task_queue.get(timeout=5)
        except Empty:
            continue
        try:
            process_task(user_id, file_bytes, file_name)
        except Exception as e:
            log("Worker process_task exception", e)
        finally:
            try:
                task_queue.task_done()
            except:
                pass

# ============ Delete old sheets worker ============
def delete_old_sheets_worker():
    while True:
        try:
            ss = safe_execute(lambda: sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID))
            sheets = ss.get("sheets", []) or []
            now = int(time.time() * 1000)
            for s in sheets:
                name = s['properties']['title']
                created = props.get(name)
                if created and now - created >= DELETE_SHEET_INTERVAL * 1000:
                    try:
                        safe_execute(lambda: sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests":[{"deleteSheet":{"sheetId": s['properties']['sheetId']}}]}))
                        props.pop(name, None)
                        log("Deleted old sheet", name)
                    except Exception as e:
                        log("Delete sheet failed", e)
        except Exception as e:
            log("delete_old_sheets_worker read error", e)
        time.sleep(60)

# ============ Flask routes ============
@app.route("/", methods=["POST"])
def incoming():
    try:
        viber_request = viber.parse_request(request.get_data())
    except Exception as e:
        log("Failed parse_request", e)
        return Response(status=400)

    # Conversation started
    if isinstance(viber_request, ViberConversationStartedRequest):
        try:
            viber.send_messages(viber_request.user.id, [TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")])
        except Exception as e:
            log("send conversation started failed", e)
        return Response(status=200)

    # dedupe tokens
    message_token = getattr(viber_request, "message_token", None)
    if message_token and message_token in processed_tokens:
        return Response(status=200)
    if message_token:
        add_processed_token(message_token)

    # message handling
    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name or "User"
        text = getattr(message, "text", "") or ""

        # report button pressed
        if text and text.strip().lower().startswith("report_"):
            fname = text.strip()[len("report_"):]
            if fname in pending_reports:
                photo_url = pending_reports.pop(fname)
                try:
                    viber.send_messages(ADMIN_ID, [TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"), PictureMessage(media=photo_url, text="Фото користувача")])
                    viber.send_messages(user_id, [TextMessage(text="Скарга успішно надіслана адміну ✅")])
                except Exception as e:
                    log("report sending failed", e)
            return Response(status=200)

        # id command
        if text and text.strip().lower() == "айді":
            try:
                viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            except: pass
            return Response(status=200)

        # get/add user (cached)
        rows = load_users_cached()
        row_num = None
        row = None
        for idx, r in enumerate(rows):
            if len(r) > 0 and r[0] == user_id:
                row_num = idx + 1
                row = r
                break

        if not row_num:
            # add new user
            try:
                safe_execute(lambda: sheets_service.spreadsheets().values().append(spreadsheetId=SPREADSHEET_ID, range="Лист1!A:D", valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values":[[user_id, user_name, DAILY_LIMIT_DEFAULT, 0]]}))
                invalidate_users_cache()
                rows = load_users_cached()
                for idx, r in enumerate(rows):
                    if len(r) > 0 and r[0] == user_id:
                        row_num = idx + 1
                        row = r
                        break
            except Exception as e:
                log("add_new_user failed", e)
                return Response(status=500)

        # if still not found, fallback values
        if not row:
            row = [user_id, user_name, str(DAILY_LIMIT_DEFAULT), "0"]

        limit = int(row[2]) if len(row) > 2 else DAILY_LIMIT_DEFAULT
        uploaded_today = int(row[3]) if len(row) > 3 and str(row[3]).isdigit() else 0

        # support multi-media
        medias = getattr(message, "media", None)
        if not medias:
            return Response(status=200)

        if not isinstance(medias, list):
            medias = [medias]

        # check limit
        if uploaded_today + len(medias) > limit:
            try: viber.send_messages(user_id, [TextMessage(text=f"🚫 Ви перевищуєте ліміт {limit} фото на сьогодні.")])
            except: pass
            return Response(status=200)

        # process each media: download (with timeout) and enqueue
        for m in medias:
            try:
                r = requests.get(m, timeout=12)
                r.raise_for_status()
                img_bytes = r.content
            except Exception as e:
                log("download image failed", e)
                try: viber.send_messages(user_id, [TextMessage(text=f"❌ Не вдалося завантажити фото: {e}")])
                except: pass
                continue

            fname = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            # update counter immediately (optimistic)
            try:
                new_count = uploaded_today + 1
                safe_execute(lambda: sheets_service.spreadsheets().values().update(spreadsheetId=SPREADSHEET_ID, range=f"Лист1!D{row_num}", valueInputOption="RAW", body={"values":[[new_count]]}))
                uploaded_today = new_count
                invalidate_users_cache()
            except Exception as e:
                log("update_user_counter failed", e)

            try:
                viber.send_messages(user_id, [TextMessage(text=f"📥 Фото '{fname}' отримано. Оброблюю...")])
            except: pass

            task_queue.put((user_id, img_bytes, fname))

    return Response(status=200)

@app.route("/", methods=["GET"])
def ping():
    return "OK", 200

# ============ Start workers ============
def start_workers():
    # token cleanup thread
    threading.Thread(target=cleanup_tokens_worker, daemon=True).start()
    # start delete old sheets
    threading.Thread(target=delete_old_sheets_worker, daemon=True).start()
    # start queue workers
    for _ in range(WORKER_COUNT):
        threading.Thread(target=worker_loop, daemon=True).start()
    log("Workers started")

if __name__ == "__main__":
    start_workers()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

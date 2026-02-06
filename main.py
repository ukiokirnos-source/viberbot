import io
import base64
import requests
import hashlib
import threading
import datetime
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.rich_media_message import RichMediaMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАЛАШТУВАННЯ ==================
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
WEB_APP_URL = "https://ocr-server-hb32.onrender.com/ocr"  # Твій Tesseract сервер
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
GOOGLE_CREDENTIALS_JSON = "credentials.json"
DAILY_LIMIT_DEFAULT = 12
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/spreadsheets']

app = Flask(__name__)
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_JSON, scopes=SCOPES)
drive = build('drive', 'v3', credentials=creds)
sheets = build('sheets', 'v4', credentials=creds)

processed_tokens = set()
processed_images = set()
pending_reports = {}

# ================== SHEETS ==================
def get_users():
    res = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D"
    ).execute()
    return res.get("values", [])

def find_user(user_id):
    rows = get_users()
    for i, r in enumerate(rows):
        if r and r[0] == user_id:
            return i + 1, r
    return None, None

def add_user(user_id, name):
    sheets.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D",
        valueInputOption="RAW",
        body={"values": [[user_id, name, DAILY_LIMIT_DEFAULT, 0]]}
    ).execute()

def update_counter(row, value):
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row}",
        valueInputOption="RAW",
        body={"values": [[value]]}
    ).execute()

def log_to_sheet(msg):
    now = datetime.datetime.utcnow().isoformat()
    sheets.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Логи!A:C",
        valueInputOption="RAW",
        body={"values": [[now, msg, ""]]}
    ).execute()

# ================== DRIVE ==================
def upload_photo(bytes_, name):
    media = MediaIoBaseUpload(io.BytesIO(bytes_), mimetype='image/jpeg')
    file = drive.files().create(
        body={'name': name, 'parents': [GDRIVE_FOLDER_ID]},
        media_body=media,
        fields='id'
    ).execute()
    drive.permissions().create(
        fileId=file['id'],
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()
    return f"https://drive.google.com/uc?id={file['id']}"

# ================== OCR ==================
def process_ocr(img64):
    try:
        r = requests.post(WEB_APP_URL, json={"image": img64}, timeout=60)
        r.raise_for_status()
        return r.json().get("barcodes", [])
    except Exception as e:
        print("OCR error:", e)
        return []

# ================== БРОД ==================
BREAD_KEYWORDS = {
    '6897': "4823117504249", '6896': "4823117504232", '7581': "4823117506656"
}

def handle_bread(text):
    text = text.lower()
    barcodes = []
    for k, v in BREAD_KEYWORDS.items():
        if k in text:
            barcodes.append(v)
    return barcodes

# ================== ОБРОБКА ФОТО В ПОТОЦІ ==================
def process_photo_thread(user_id, img, row, data):
    try:
        limit = int(data[2])
        used = int(data[3])
        if used >= limit:
            viber.send_messages(user_id, [TextMessage(text=f"🚫 Ліміт {limit} фото на сьогодні вичерпано.")])
            return

        img64 = base64.b64encode(img).decode()
        text_barcodes = process_ocr(img64)

        # Спец. хліб
        if any(word in text_barcodes for word in ["полтава хліб", "київхліб"]):
            text_barcodes.extend(handle_bread(" ".join(text_barcodes)))

        update_counter(row, used + 1)
        text = "\n".join(text_barcodes) if text_barcodes else "❌ Штрихкодів не знайдено"
        viber.send_messages(user_id, [TextMessage(text=text)])

        fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        url = upload_photo(img, fname)
        pending_reports[fname] = url

        viber.send_messages(user_id, [
            PictureMessage(media=url, text=""),
            RichMediaMessage(
                rich_media={
                    "Type": "rich_media",
                    "ButtonsGroupColumns": 6,
                    "ButtonsGroupRows": 1,
                    "Buttons": [{
                        "Columns": 6,
                        "Rows": 1,
                        "ActionType": "reply",
                        "ActionBody": f"report_{fname}",
                        "Text": "⚠️ Скарга",
                        "BgColor": "#ff4444",
                        "TextColor": "#ffffff"
                    }]
                },
                min_api_version=2
            ),
            TextMessage(text="──────────────\nГотово.\n──────────────")
        ])
    except Exception as e:
        print("Photo thread error:", e)

# ================== MAIN ==================
@app.route('/', methods=['POST'])
def incoming():
    req = viber.parse_request(request.get_data())

    if isinstance(req, ViberConversationStartedRequest):
        viber.send_messages(req.user.id, [TextMessage(text="Привіт! Відправ фото, а я відправлю штрих-код 😊")])
        return Response(status=200)

    token = getattr(req, 'message_token', None)
    if token in processed_tokens: return Response(status=200)
    processed_tokens.add(token)

    if isinstance(req, ViberMessageRequest):
        msg = req.message
        user_id = req.sender.id
        name = req.sender.name

        if hasattr(msg, 'media') and msg.media:
            img = requests.get(msg.media, timeout=10).content
            img_hash = hashlib.sha256(img).hexdigest()
            if img_hash in processed_images: return Response(status=200)
            processed_images.add(img_hash)

            row, data = find_user(user_id)
            if not row:
                add_user(user_id, name)
                row, data = find_user(user_id)

            # Запускаємо обробку в окремому потоці
            threading.Thread(target=process_photo_thread, args=(user_id, img, row, data)).start()

        elif hasattr(msg, 'text') and msg.text.startswith("report_"):
            fname = msg.text.replace("report_", "")
            if fname in pending_reports:
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від {user_id}"),
                    PictureMessage(media=pending_reports[fname])
                ])
                viber.send_messages(user_id, [TextMessage(text="Скарга відправлена адміну ✅")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

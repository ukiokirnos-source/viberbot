import io
import base64
import requests
import datetime
import hashlib
import re
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

# ================== НАЛАШТУВАННЯ ==================
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
WEB_APP_URL = "https://script.google.com/macros/s/AKfycby4pDNg0fUyxmEV49ObZi3zwt131jEO_U39-E25-W9bK4Wk1crDkgqYqbliJBkVo26Srg/exec"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
GOOGLE_TOKEN_FILE = "token.json"
DAILY_LIMIT_DEFAULT = 12
MONTHLY_LIMIT_DEFAULT = 100
TOTAL_LIMIT = 999
ADMIN_NOTIFY_AT = 100

SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/gmail.readonly'
]

# ================== ІНІЦІАЛІЗАЦІЯ ==================
app = Flask(__name__)
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive = build('drive', 'v3', credentials=creds)
sheets = build('sheets', 'v4', credentials=creds)
gmail = build("gmail", "v1", credentials=creds)

processed_tokens = set()
processed_images = set()
pending_reports = {}
admin_notified = False

# ================== HELPERS ==================
def normalize_barcode(code):
    if not code:
        return None
    code = code.upper().strip()
    replacements = {'O':'0','I':'1','L':'1','S':'5','B':'8','Z':'2'}
    code = ''.join(replacements.get(c, c) for c in code)
    code = re.sub(r'[^0-9]', '', code)
    return code if code else None

# ================== SHEETS ==================
def get_users():
    res = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:F"
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
        range="Лист1!A:F",
        valueInputOption="RAW",
        body={"values": [[user_id, name, DAILY_LIMIT_DEFAULT, 0, MONTHLY_LIMIT_DEFAULT, 0]]}
    ).execute()

def update_counter(row, daily_used, monthly_used):
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row}:F{row}",
        valueInputOption="RAW",
        body={"values": [[daily_used, monthly_used]]}
    ).execute()

def get_total_counter():
    res = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!G1"
    ).execute()
    val = res.get("values", [["0"]])[0][0]
    try:
        return int(val)
    except:
        return 0

def set_total_counter(val):
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!G1",
        valueInputOption="RAW",
        body={"values": [[val]]}
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

# ================== GMAIL SEARCH ==================
def search_gmail_attachments(doc):
    query = f"filename:{doc} newer_than:14d"
    res = gmail.users().messages().list(userId="me", q=query).execute()
    messages = res.get("messages", [])
    files = []
    for m in messages:
        msg = gmail.users().messages().get(userId="me", id=m["id"]).execute()
        parts = msg["payload"].get("parts", [])
        for p in parts:
            filename = p.get("filename")
            if filename and doc in filename:
                att_id = p["body"].get("attachmentId")
                if att_id:
                    att = gmail.users().messages().attachments().get(
                        userId="me", messageId=m["id"], id=att_id
                    ).execute()
                    data = base64.urlsafe_b64decode(att["data"])
                    files.append({"name": filename, "data": data})
    return files

# ================== MAIN ==================
@app.route('/', methods=['POST'])
def incoming():
    global admin_notified
    req = viber.parse_request(request.get_data())
    token = getattr(req, 'message_token', None)
    if token in processed_tokens:
        return Response(status=200)
    processed_tokens.add(token)

    total_used = get_total_counter()
    if total_used >= TOTAL_LIMIT:
        if isinstance(req, ViberMessageRequest):
            viber.send_messages(req.sender.id, [
                TextMessage(text=f"🚫 Глобальний ліміт {TOTAL_LIMIT} фото вичерпано. Бот тимчасово вимкнений.")
            ])
        return Response(status=200)

    if isinstance(req, ViberConversationStartedRequest):
        viber.send_messages(req.user.id, [
            TextMessage(text="Привіт! Відправ фото, а я відправлю штрих-код 😊")
        ])
        return Response(status=200)

    if isinstance(req, ViberMessageRequest):
        msg = req.message
        user_id = req.sender.id
        name = req.sender.name

        # ================== ФОТО ==================
        if hasattr(msg, 'media') and msg.media:
            remaining = TOTAL_LIMIT - total_used
            if remaining <= ADMIN_NOTIFY_AT and not admin_notified:
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ До глобального ліміту {TOTAL_LIMIT} залишилось {remaining} фото.")
                ])
                admin_notified = True

            img = requests.get(msg.media, timeout=10).content
            img_hash = hashlib.sha256(img).hexdigest()
            if img_hash in processed_images:
                return Response(status=200)
            processed_images.add(img_hash)

            row, data = find_user(user_id)
            if not row:
                add_user(user_id, name)
                row, data = find_user(user_id)

            daily_limit = int(data[2])
            daily_used = int(data[3])
            monthly_limit = int(data[4])
            monthly_used = int(data[5])

            if daily_used >= daily_limit:
                viber.send_messages(user_id, [
                    TextMessage(text=f"🚫 Ліміт {daily_limit} фото на день вичерпано.")
                ])
                return Response(status=200)

            if monthly_used >= monthly_limit:
                viber.send_messages(user_id, [
                    TextMessage(text=f"🚫 Ліміт {monthly_limit} фото на місяць вичерпано.")
                ])
                return Response(status=200)

            # Обробка штрихкодів
            img64 = base64.b64encode(img).decode()
            try:
                r = requests.post(WEB_APP_URL, json={"image": img64}, timeout=20)
                raw_barcodes = r.json().get("barcodes", [])
                barcodes = [normalize_barcode(b) for b in raw_barcodes if normalize_barcode(b)]
            except:
                barcodes = []

            # Оновлюємо ліміти
            update_counter(row, daily_used + 1, monthly_used + 1)
            set_total_counter(total_used + 1)

            text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
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

        # ================== СКАРГИ ==================
        elif hasattr(msg, 'text') and msg.text.startswith("report_"):
            fname = msg.text.replace("report_", "")
            if fname in pending_reports:
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від {user_id}"),
                    PictureMessage(media=pending_reports[fname])
                ])
                viber.send_messages(user_id, [TextMessage(text="Скарга відправлена адміну ✅")])

        # ================== ПОШУК ВКЛАДЕНЬ ==================
        elif hasattr(msg, 'text'):
            doc = msg.text.strip()
            files = search_gmail_attachments(doc)
            if not files:
                viber.send_messages(user_id, [TextMessage(text="❌ Вкладень не знайдено")])
            else:
                for f in files:
                    try:
                        media = MediaIoBaseUpload(io.BytesIO(f["data"]), mimetype='application/octet-stream')
                        file_drive = drive.files().create(
                            body={'name': f["name"], 'parents':[GDRIVE_FOLDER_ID]},
                            media_body=media,
                            fields='id'
                        ).execute()
                        drive.permissions().create(
                            fileId=file_drive['id'],
                            body={'type':'anyone','role':'reader'}
                        ).execute()
                        url = f"https://drive.google.com/uc?id={file_drive['id']}"
                        viber.send_messages(user_id, [TextMessage(text=f"📎 {f['name']}: {url}")])
                    except:
                        viber.send_messages(user_id, [TextMessage(text=f"❌ Не вдалося відправити файл {f['name']}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

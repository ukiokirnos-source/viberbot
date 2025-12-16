import io
import base64
import requests
import datetime

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

SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]

# =================================================

app = Flask(__name__)

viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive = build('drive', 'v3', credentials=creds)
sheets = build('sheets', 'v4', credentials=creds)

processed_tokens = set()
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

# ================== MAIN ==================

@app.route('/', methods=['POST'])
def incoming():
    req = viber.parse_request(request.get_data())

    if isinstance(req, ViberConversationStartedRequest):
        viber.send_messages(req.user.id, [
            TextMessage(text="Кидай фото. Штрихкоди повертаю одразу.")
        ])
        return Response(status=200)

    token = getattr(req, 'message_token', None)
    if token in processed_tokens:
        return Response(status=200)
    processed_tokens.add(token)

    if isinstance(req, ViberMessageRequest):
        msg = req.message
        user_id = req.sender.id
        name = req.sender.name

        if hasattr(msg, 'media') and msg.media:
            row, data = find_user(user_id)
            if not row:
                add_user(user_id, name)
                row, data = find_user(user_id)

            limit = int(data[2])
            used = int(data[3])

            if used >= limit:
                viber.send_messages(user_id, [
                    TextMessage(text=f"🚫 Ліміт {limit} фото на сьогодні.")
                ])
                return Response(status=200)

            img = requests.get(msg.media, timeout=10).content
            img64 = base64.b64encode(img).decode()

            r = requests.post(WEB_APP_URL, json={"image": img64}, timeout=20)
            barcodes = r.json().get("barcodes", [])

            update_counter(row, used + 1)

            if barcodes:
                text = "📦 Штрихкоди:\n" + "\n".join(barcodes)
            else:
                text = "❌ Штрихкодів не знайдено"

            viber.send_messages(user_id, [TextMessage(text=text)])

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)

            pending_reports[fname] = url

            viber.send_messages(user_id, [
                PictureMessage(media=url, text="Фото збережено"),
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
                )
            ])

        elif hasattr(msg, 'text') and msg.text.startswith("report_"):
            fname = msg.text.replace("report_", "")
            if fname in pending_reports:
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від {user_id}"),
                    PictureMessage(media=pending_reports[fname])
                ])
                viber.send_messages(user_id, [
                    TextMessage(text="Скарга відправлена адміну ✅")
                ])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

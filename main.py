import io
import threading
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

# ==== Налаштування ====
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_TOKEN_FILE = "token.json"
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
SCRIPT_URL = "https://script.google.com/macros/s/AKfycbw6lHYHxiaYeNeL5GG8QB_85wVhGZQ_CSgxYBKNY0HW0VBKIH2eVJi55A7gdNdJQwofRg/exec"

app = Flask(__name__)

# ==== Ініціалізація Viber ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()
pending_reports = {}

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Apps Script обробка ====
def process_barcodes(file_id):
    """Викликає Apps Script для обробки зображення та отримання штрихкодів"""
    try:
        resp = requests.post(SCRIPT_URL, json={"fileId": file_id}, timeout=40)
        data = resp.json()
        barcodes = data.get("barcodes", [])
        if not barcodes:
            return ["❌ Штрихкодів не знайдено."]
        return barcodes
    except Exception as e:
        return [f"❌ Помилка при запиті до Apps Script: {e}"]

# ==== Відправка штрихкодів ====
def delayed_send(user_id, file_id, file_name, public_url):
    try:
        # 1. Відправляємо фото
        viber.send_messages(user_id, [
            PictureMessage(media=public_url, text=f"Фото: {file_name}")
        ])
    except Exception as e:
        print(f"Помилка при надсиланні фото: {e}")

    # 2. Викликаємо Apps Script
    barcodes = process_barcodes(file_id)
    barcodes_text = "\n".join(barcodes)

    # 3. Відправляємо текст із штрихкодами
    viber.send_messages(user_id, [
        TextMessage(text=barcodes_text)
    ])

    # 4. Кнопка "Скарга"
    rich_media = {
        "Type": "rich_media",
        "ButtonsGroupColumns": 6,
        "ButtonsGroupRows": 1,
        "BgColor": "#FFFFFF",
        "Buttons": [
            {
                "Columns": 6,
                "Rows": 1,
                "ActionType": "reply",
                "ActionBody": f"report_{file_name}",
                "Text": "⚠️ Скарга",
                "TextSize": "medium",
                "TextVAlign": "middle",
                "TextHAlign": "center",
                "BgColor": "#ff6666",
                "TextOpacity": 100,
                "TextColor": "#FFFFFF"
            }
        ]
    }
    pending_reports[file_name] = public_url
    viber.send_messages(user_id, [
        RichMediaMessage(rich_media=rich_media, min_api_version=2, alt_text="Скарга")
    ])

# ==== Основна логіка ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    # При старті
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Надішли мені фото накладної — я знайду штрихкоди.")
        ])
        return Response(status=200)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        text = getattr(message, 'text', '').strip().lower()

        # Скарга
        if text.startswith("report_"):
            file_name = text.replace("report_", "")
            if file_name in pending_reports:
                photo_url = pending_reports.pop(file_name)
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від користувача: {user_id}"),
                    PictureMessage(media=photo_url, text="Фото користувача")
                ])
                viber.send_messages(user_id, [TextMessage(text="✅ Скаргу відправлено адміну.")])
            return Response(status=200)

        # Фото
        if hasattr(message, 'media') and message.media:
            try:
                image_url = message.media
                ext = image_url.split('.')[-1].split('?')[0]
                if ext.lower() not in ['jpg', 'jpeg', 'png']:
                    ext = 'jpg'
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"photo_{timestamp}.{ext}"

                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(
                    body=file_metadata, media_body=media, fields='id'
                ).execute()

                file_id = file.get('id')
                add_public_permission(file_id)

                public_url = f"https://drive.google.com/uc?id={file_id}"
                viber.send_messages(user_id, [
                    TextMessage(text=f"📥 Фото '{file_name}' отримано, оброблюю...")
                ])

                threading.Thread(target=delayed_send, args=(user_id, file_id, file_name, public_url), daemon=True).start()

            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

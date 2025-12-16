import io
import base64
import requests
import datetime

from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАЛАШТУВАННЯ ==================

VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycby4pDNg0fUyxmEV49ObZi3zwt131jEO_U39-E25-W9bK4Wk1crDkgqYqbliJBkVo26Srg/exec"

GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
GOOGLE_TOKEN_FILE = "token.json"

SCOPES = ['https://www.googleapis.com/auth/drive.file']

# =================================================

app = Flask(__name__)

viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)

processed_message_tokens = set()

# ================== DRIVE ==================

def upload_to_drive(img_bytes, filename):
    stream = io.BytesIO(img_bytes)
    media = MediaIoBaseUpload(stream, mimetype='image/jpeg')
    file_metadata = {'name': filename, 'parents': [GDRIVE_FOLDER_ID]}
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()

    file_id = file.get('id')

    drive_service.permissions().create(
        fileId=file_id,
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()

    return f"https://drive.google.com/uc?id={file_id}"

# ================== MAIN ==================

@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Кидай фото зі штрихкодами. Результат буде одразу.")
        ])
        return Response(status=200)

    token = getattr(viber_request, 'message_token', None)
    if token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(token)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id

        if hasattr(message, 'media') and message.media:
            try:
                # 1️⃣ Качаємо фото
                img_bytes = requests.get(message.media, timeout=10).content

                # 2️⃣ В base64
                img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                # 3️⃣ Web App → Vision API
                resp = requests.post(
                    WEB_APP_URL,
                    json={"image": img_base64},
                    timeout=20
                )

                result = resp.json()
                barcodes = result.get("barcodes", [])

                # 4️⃣ Відповідь юзеру
                if barcodes:
                    text = "📦 Штрихкоди:\n" + "\n".join(barcodes)
                else:
                    text = "❌ Штрихкоди не знайдено"

                viber.send_messages(user_id, [
                    TextMessage(text=text)
                ])

                # 5️⃣ Фото — фоном у Drive
                filename = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                public_url = upload_to_drive(img_bytes, filename)

                viber.send_messages(user_id, [
                    PictureMessage(media=public_url, text="📸 Фото збережено")
                ])

            except Exception as e:
                viber.send_messages(user_id, [
                    TextMessage(text=f"❌ Помилка: {str(e)}")
                ])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

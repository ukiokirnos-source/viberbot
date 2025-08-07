import os
import io
import requests
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.viber_requests import ViberMessageRequest
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# === Налаштування ===
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
GOOGLE_CREDENTIALS_FILE = "credentials.json"

app = Flask(__name__)

# === Ініціалізація Viber бота ===
viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',  # Можна змінити
    auth_token=VIBER_TOKEN
))

# === Google Drive авторизація ===
creds = service_account.Credentials.from_service_account_file(
    GOOGLE_CREDENTIALS_FILE,
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build('drive', 'v3', credentials=creds)

# === Flask маршрут ===
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message

        # Якщо це картинка
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            file_name = "photo.jpg"

            # Завантажити фото
            img_data = requests.get(image_url).content
            file_stream = io.BytesIO(img_data)

            # Завантажити на Google Drive
            media = MediaIoBaseUpload(file_stream, mimetype='image/jpeg')
            file_metadata = {
                'name': file_name,
                'parents': [GDRIVE_FOLDER_ID]
            }
            drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()

            # Відповідь лайком
            viber.send_messages(viber_request.sender.id, [
                TextMessage(text="👍 Фото завантажено!")
            ])

    return Response(status=200)

# === Пінг для Render ===
@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

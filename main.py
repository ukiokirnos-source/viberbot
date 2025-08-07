import os
import io
import json
import requests
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.viber_requests import ViberMessageRequest

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.auth.transport.requests import Request

app = Flask(__name__)

# Налаштування Viber
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SCOPES = ['https://www.googleapis.com/auth/drive.file']

viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',
    auth_token=VIBER_TOKEN
))

# Авторизація Google Drive OAuth 2.0 (збережи токен після першого запуску)
def google_drive_service():
    creds = None
    if os.path.exists('token.json'):
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    service = build('drive', 'v3', credentials=creds)
    return service

drive_service = google_drive_service()

@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message

        if hasattr(message, 'media') and message.media:
            image_url = message.media
            filename = "photo.jpg"

            # Завантаження фото
            img_data = requests.get(image_url).content
            file_stream = io.BytesIO(img_data)

            # Завантаження на Google Drive
            media = MediaIoBaseUpload(file_stream, mimetype='image/jpeg')
            file_metadata = {
                'name': filename,
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

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

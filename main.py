import io
import threading
import time
import requests
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.viber_requests import ViberMessageRequest

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ==== Налаштування ====
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_TOKEN_FILE = "token.json"
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/spreadsheets.readonly']

app = Flask(__name__)

# ==== Ініціалізація Viber бота ====
viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google API клієнтів ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

def get_barcodes_from_sheet(sheet_id, sheet_name_part):
    try:
        # Отримуємо всі листи в таблиці
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        target_sheet_name = None
        sheet_name_part_lower = sheet_name_part.lower()

        for sheet in sheets:
            title = sheet['properties']['title']
            if sheet_name_part_lower in title.lower():
                target_sheet_name = title
                break
        
        if not target_sheet_name:
            return f"Лист із назвою, схожою на '{sheet_name_part}', не знайдено."

        # Читаємо штрихкоди з знайденого листа
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{target_sheet_name}!A:A"
        ).execute()
        values = result.get('values', [])
        barcodes = [row[0] for row in values if row]
        if not barcodes:
            return "Штрихкоди не знайдено."
        return "\n".join(barcodes)
    except Exception as e:
        return f"Помилка при зчитуванні штрихкодів: {str(e)}"

def delayed_send_barcodes(user_id, reply_token, sheet_name, delay=120):
    time.sleep(delay)  # Чекаємо 2 хвилини
    barcodes_text = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
    try:
        viber.send_messages(user_id, [
            TextMessage(text=f"Штрихкоди з листа, схожого на '{sheet_name}':\n{barcodes_text}", 
                        reply_to_message_token=reply_token)
        ])
    except Exception as e:
        print(f"Помилка при надсиланні штрихкодів: {e}")

@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        reply_token = viber_request.message_token  # Для відповіді на повідомлення

        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png']:
                ext = 'jpg'
            file_name = f"photo.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)

                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {
                    'name': file_name,
                    'parents': [GDRIVE_FOLDER_ID]
                }
                drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute()

                sheet_name = file_name.rsplit('.', 1)[0]

                viber.send_messages(user_id, [
                    TextMessage(
                        text="Фото отримано. Чекаємо штрихкоди...",
                        reply_to_message_token=reply_token
                    )
                ])

                threading.Thread(
                    target=delayed_send_barcodes,
                    args=(user_id, reply_token, sheet_name),
                    daemon=True
                ).start()

            except Exception as e:
                viber.send_messages(user_id, [
                    TextMessage(text=f"Помилка при обробці зображення: {e}")
                ])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

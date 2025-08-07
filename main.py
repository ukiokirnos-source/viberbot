import os
import time
import threading
import io
from flask import Flask, request
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages import TextMessage
from viberbot.api.viber_requests import ViberMessageRequest
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import requests

app = Flask(__name__)

# Viber bot
viber = Api(BotConfiguration(
    name='BarcodeBot',
    avatar='https://example.com/avatar.jpg',
    auth_token='4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531'
))

# Google API setup
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets.readonly']
SERVICE_ACCOUNT_FILE = 'credentials.json'
FOLDER_ID = '1FteobWxkEUxPq1kBhUiP70a4-X0slbWe'

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=credentials)
sheets_service = build('sheets', 'v4', credentials=credentials)

def upload_to_drive(file_url, file_name):
    response = requests.get(file_url)
    if response.status_code != 200:
        raise Exception("Failed to download image")

    file_metadata = {'name': file_name, 'parents': [FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(response.content), mimetype='image/jpeg')
    uploaded = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return uploaded.get('id')

def find_sheet_by_partial_name(spreadsheet_id, part_name):
    spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in spreadsheet.get('sheets', []):
        title = sheet['properties']['title']
        if part_name.lower() in title.lower():
            return title
    return None

def read_barcodes_from_sheet(spreadsheet_id, sheet_title):
    range_ = f"{sheet_title}!A:A"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_).execute()
    values = result.get('values', [])
    return [row[0] for row in values if row]

def delayed_barcode_reply(sender_id, message_token, file_name):
    time.sleep(120)  # 2 хвилини

    try:
        spreadsheet_id = '1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8'
        sheet_title = find_sheet_by_partial_name(spreadsheet_id, file_name)
        if not sheet_title:
            viber.send_messages(sender_id, [
                TextMessage(text="Не знайдено відповідного аркуша зі штрихкодами 😕", min_api_version=6, reply_type="REPLY", reply_to_message_token=message_token)
            ])
            return

        barcodes = read_barcodes_from_sheet(spreadsheet_id, sheet_title)
        if barcodes:
            response_text = "Штрихкоди з листа:\n" + "\n".join(barcodes)
        else:
            response_text = "Лист знайдено, але штрихкодів нема 😶"

        viber.send_messages(sender_id, [
            TextMessage(text=response_text, min_api_version=6, reply_type="REPLY", reply_to_message_token=message_token)
        ])
    except Exception as e:
        viber.send_messages(sender_id, [
            TextMessage(text=f"Помилка при зчитуванні штрихкодів: {e}")
        ])

@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        sender_id = viber_request.sender.id

        if message.media:
            try:
                file_url = message.media
                file_name = f"photo_{int(time.time())}.jpg"
                upload_to_drive(file_url, file_name)

                # відповідаємо що фото завантажено
                viber.send_messages(sender_id, [
                    TextMessage(text="Фото завантажено! 🔄 Шукаю штрихкоди, зачекай 2 хвилини...", min_api_version=6, reply_type="REPLY", reply_to_message_token=message.token)
                ])

                # чекаємо 2 хв і відповідаємо з штрихкодами
                threading.Thread(target=delayed_barcode_reply, args=(sender_id, message.token, file_name)).start()

            except Exception as e:
                viber.send_messages(sender_id, [
                    TextMessage(text=f"Помилка при обробці зображення: {e}")
                ])
        else:
            viber.send_messages(sender_id, [
                TextMessage(text="Будь ласка, надішли фото 📷")
            ])

    return 'OK'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

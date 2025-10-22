import os
import io
import threading
import time
import datetime
import requests
import json
from flask import Flask, request, Response

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.keyboard_message import KeyboardMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ==== Barcode detection ====
from pyzbar.pyzbar import decode
from PIL import Image

# ==== Google API авторизація через OAuth ====
GOOGLE_USER_KEY = json.loads(os.environ['GOOGLE_SA_JSON'])
GOOGLE_VISION_KEY = json.loads(os.environ['GOOGLE_VISION_JSON'])

creds = Credentials.from_authorized_user_info(GOOGLE_USER_KEY)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# ==== Конфігурація ====
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
DAILY_LIMIT_DEFAULT = 12

# ==== Flask ====
app = Flask(__name__)

# ==== Viber ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

processed_message_tokens = set()

# ==== Google Sheets ====
def get_all_users():
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D"
    ).execute()
    return result.get('values', [])

def find_user_row(user_id):
    rows = get_all_users()
    for idx, row in enumerate(rows):
        if len(row) > 0 and row[0] == user_id:
            return idx + 1, row
    return None, None

def add_new_user(user_id, name):
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[user_id, name, DAILY_LIMIT_DEFAULT, 0]]}
    ).execute()

def update_user_counter(row_number, new_count):
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row_number}",
        valueInputOption="RAW",
        body={"values": [[new_count]]}
    ).execute()

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Barcode detection ====
def extract_barcodes_from_image(file_stream):
    try:
        file_stream.seek(0)
        image = Image.open(file_stream)
        barcodes = decode(image)
        result = []
        forbidden_prefixes = [
            "00", "1", "436", "202", "22", "403", "675", "459", "311", "377", "391", "2105",
            "451", "288", "240", "442", "044", "363", "971", "097", "044", "44", "536", "053",
            "82", "066", "66", "29", "36", "46", "38", "43", "26", "39", "35", "53", "30",
            "67", "063", "63", "0674", "674", "0675", "675", "319", "086", "86", "095",
            "9508", "11", "21", "050", "507", "6721", "06721", "2309", "999", "249", "9798"
        ]
        for barcode in barcodes:
            code = barcode.data.decode('utf-8')
            if any(code.startswith(p) for p in forbidden_prefixes):
                continue
            result.append(code)
        return list(set(result))
    except Exception as e:
        print(f"Помилка при розпізнаванні штрихкодів: {e}")
        return []

# ==== Відправка фото та штрихкодів ====
def delayed_send(user_id, file_name, public_url, file_stream):
    time.sleep(10)
    try:
        viber.send_messages(user_id, [PictureMessage(media=public_url, text=f"Фото: {file_name}")])
        barcodes = extract_barcodes_from_image(file_stream)
        if barcodes:
            barcodes_text = "\n".join(barcodes)
        else:
            barcodes_text = f"❌ Штрихкодів у фото '{file_name}' не знайдено."
        viber.send_messages(user_id, [TextMessage(text=barcodes_text)])
    except Exception as e:
        print(f"Помилка при надсиланні: {e}")

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())
    if isinstance(viber_request, ViberConversationStartedRequest):
        keyboard = {
            "Type": "keyboard",
            "DefaultHeight": True,
            "Buttons": [
                {
                    "Columns": 6,
                    "Rows": 1,
                    "Text": "❗ Скарга",
                    "ActionType": "reply",
                    "ActionBody": "скарга",
                    "BgColor": "#FF0000"
                }
            ]
        }
        viber.send_messages(viber_request.user.id, [
            KeyboardMessage(keyboard=keyboard, text="Привіт! Відправ мені фото для сканування штрихкодів.\nЩоб дізнатися свій ID, напиши: Айді")
        ])
        return Response(status=200)

    message_token = getattr(viber_request, 'message_token', None)
    if message_token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(message_token)

    if isinstance(viber_request, ViberMessageRequest):
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        message = viber_request.message
        text = getattr(message, 'text', '').strip().lower()

        if text == "айді":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        if text == "скарга":
            viber.send_messages(user_id, [TextMessage(text="🚨 Скарга прийнята, чекайте відповіді адміна.")])
            return Response(status=200)

        row_num, row = find_user_row(user_id)
        if not row_num:
            add_new_user(user_id, user_name)
            row_num, row = find_user_row(user_id)
        limit = int(row[2])
        uploaded_today = int(row[3])
        if uploaded_today >= limit:
            viber.send_messages(user_id, [TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return Response(status=200)

        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            ext = ext if ext.lower() in ['jpg', 'jpeg', 'png'] else 'jpg'
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"photo_{timestamp}.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                file_id = file.get('id')
                add_public_permission(file_id)
                update_user_counter(row_num, uploaded_today + 1)

                viber.send_messages(user_id, [TextMessage(text=f"📥 Фото '{file_name}' отримано. Обробляю (10 сек)...")])
                file_stream.seek(0)
                threading.Thread(
                    target=delayed_send,
                    args=(user_id, file_name, f"https://drive.google.com/uc?id={file_id}", file_stream),
                    daemon=True
                ).start()
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])
    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

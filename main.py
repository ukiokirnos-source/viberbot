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

viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)


def find_sheet_name(sheet_id, file_base_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        for sheet in sheets:
            title = sheet.get('properties', {}).get('title', '')
            if title == file_base_name or title == '[NO_BARCODE] ' + file_base_name:
                return title
        return None
    except Exception as e:
        print(f"Помилка при пошуку листа: {e}")
        return None


def get_barcodes_from_sheet(sheet_id, sheet_name):
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A:A"
        ).execute()
        values = result.get('values', [])
        if not values:
            return None
        # Якщо лист про відсутність штрихкодів
        if len(values) == 1 and values[0][0] == "Штрихкодів не знайдено":
            return None
        barcodes = [row[0] for row in values if row]
        if not barcodes:
            return None
        return "\n".join(barcodes)
    except Exception as e:
        return f"Помилка при зчитуванні штрихкодів: {str(e)}"


def delayed_send_barcodes(user_id, file_base_name, file_name, delay=80):
    time.sleep(delay)
    try:
        sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
        if not sheet_name:
            text = f"❌ Не знайдено листа з назвою '{file_base_name}' або '[NO_BARCODE] {file_base_name}'. Можливо, фото ще не оброблено."
        else:
            barcodes_text = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
            if barcodes_text is None:
                text = f"❌ Штрихкодів у фото '{file_name}' не знайдено."
            elif "Помилка" in barcodes_text:
                text = f"❌ Помилка при зчитуванні штрихкодів: {barcodes_text}"
            else:
                text = f"📸 Фото: {file_name}\n🔍 Штрихкоди з листа '{sheet_name}':\n{barcodes_text}"
        viber.send_messages(user_id, [TextMessage(text=text)])
    except Exception as e:
        print(f"Помилка у delayed_send_barcodes: {e}")


@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id

        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png']:
                ext = 'jpg'

            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base_name = f"photo_{timestamp}"
            file_name = f"{file_base_name}.{ext}"

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

                viber.send_messages(user_id, [
                    TextMessage(text=f"📥 Фото '{file_name}' отримано. Чекаємо штрихкоди...")
                ])

                threading.Thread(
                    target=delayed_send_barcodes,
                    args=(user_id, file_base_name, file_name),
                    daemon=True
                ).start()

            except Exception as e:
                viber.send_messages(user_id, [
                    TextMessage(text=f"❌ Помилка при обробці зображення: {e}")
                ])

    return Response(status=200)


@app.route('/', methods=['GET'])
def ping():
    return "OK", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
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


def find_sheet_name(sheet_id, file_base_name):
    """Шукає лист, назва якого точно співпадає з file_base_name."""
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        for sheet in sheets:
            title = sheet.get('properties', {}).get('title', '')
            if title == file_base_name:
                return title
        return None
    except Exception as e:
        print(f"Помилка при пошуку листа: {e}")
        return None


def get_barcodes_from_sheet(sheet_id, sheet_name):
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A:A"
        ).execute()
        values = result.get('values', [])
        # Перевірка позначки [NO_BARCODE]
        if not values or (len(values) == 1 and values[0][0] == "[NO_BARCODE]"):
            return None  # Позначаємо, що штрихкодів немає
        barcodes = [row[0] for row in values if row]
        return "\n".join(barcodes)
    except Exception as e:
        return f"Помилка при зчитуванні штрихкодів: {str(e)}"


def delayed_send_barcodes(user_id, file_base_name, file_name, delay=80):
    time.sleep(delay)  # Чекаємо ~1 хв 20 сек
    sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
    if not sheet_name:
        text = f"❌ Не знайдено листа з назвою '{file_base_name}'"
    else:
        barcodes_text = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
        if barcodes_text is None:
            text = f"❌ Штрихкодів у фото '{file_name}' не знайдено."
        else:
            text = f"📸 Фото: {file_name}\n🔍 Штрихкоди з листа '{sheet_name}':\n{barcodes_text}"
    try:
        viber.send_messages(user_id, [
            TextMessage(text=text)
        ])
    except Exception as e:
        print(f"Помилка при надсиланні штрихкодів: {e}")


@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id

        # Перевірка, чи є медіа (зображення)
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png']:
                ext = 'jpg'

            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base_name = f"photo_{timestamp}"
            file_name = f"{file_base_name}.{ext}"

            try:
                # Завантажуємо фото з URL
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)

                # Завантажуємо на Google Drive
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

                # Відповідаємо користувачу
                viber.send_messages(user_id, [
                    TextMessage(text=f"📥 Фото '{file_name}' отримано. Чекаємо штрихкоди...")
                ])

                # Запускаємо фоновий потік, який через delay відправить штрихкоди
                threading.Thread(
                    target=delayed_send_barcodes,
                    args=(user_id, file_base_name, file_name),
                    daemon=True
                ).start()

            except Exception as e:
                viber.send_messages(user_id, [
                    TextMessage(text=f"❌ Помилка при обробці зображення: {e}")
                ])

    return Response(status=200)


@app.route('/', methods=['GET'])
def ping():
    return "OK", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

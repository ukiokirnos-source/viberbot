import io
import threading
import datetime
import requests
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
SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxnEtNrSKCWimbUXyVLA-xF7ygrlYge40ValoDALGzjcTdU8-7mwAvsxFFiQz9GRc_v4A/exec"  # Apps Script URL
DAILY_LIMIT_DEFAULT = 12
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)

# ==== Ініціалізація Viber бота ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()
pending_reports = {}

# ==== Допоміжні функції ====
def get_all_users():
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="Лист1!A:D"
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

def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Обробка фото та виклик Apps Script ====
def process_and_send_barcodes(user_id, file_name, file_url, file_id):
    try:
        # 1. Надсилаємо фото користувачу
        viber.send_messages(user_id, [PictureMessage(media=file_url, text=f"Фото: {file_name}")])

        # 2. Надсилаємо кнопку "Скарга"
        rich_media_dict = {
            "Type": "rich_media",
            "ButtonsGroupColumns": 6,
            "ButtonsGroupRows": 1,
            "BgColor": "#FFFFFF",
            "Buttons": [{
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
            }]
        }
        pending_reports[file_name] = file_url
        viber.send_messages(user_id, [RichMediaMessage(rich_media=rich_media_dict, min_api_version=2, alt_text="Скарга")])

        # 3. Викликаємо Apps Script для отримання штрихкодів
        r = requests.post(SCRIPT_URL, params={"fileId": file_id}, timeout=20)
        result = r.json()
        if "error" in result:
            barcodes_text = f"❌ Помилка при отриманні штрихкодів: {result['error']}"
        else:
            barcodes = result.get("barcodes", [])
            if not barcodes:
                barcodes_text = f"❌ Штрихкодів у фото '{file_name}' не знайдено."
            else:
                barcodes_text = "\n".join(barcodes)

        viber.send_messages(user_id, [TextMessage(text=barcodes_text)])

    except Exception as e:
        viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка: {e}")])

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")])
        return Response(status=200)

    message_token = getattr(viber_request, 'message_token', None)
    if message_token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(message_token)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        text = getattr(message, 'text', '').strip().lower()

        # Обробка кнопки "Скарга"
        if text.startswith("report_"):
            file_name = text[len("report_"):]
            if file_name in pending_reports:
                photo_url = pending_reports.pop(file_name)
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"),
                    PictureMessage(media=photo_url, text="Фото користувача")
                ])
                viber.send_messages(user_id, [TextMessage(text="Скарга успішно надіслана адміну ✅")])
            return Response(status=200)

        # Команда Айді
        if text == "айді":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Додаємо користувача якщо нема
        row_num, row = find_user_row(user_id)
        if not row_num:
            add_new_user(user_id, user_name)
            row_num, row = find_user_row(user_id)

        limit = int(row[2])
        uploaded_today = int(row[3])
        if uploaded_today >= limit:
            viber.send_messages(user_id, [TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return Response(status=200)

        # Обробка фото
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg','jpeg','png']:
                ext = 'jpg'
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

                threading.Thread(target=process_and_send_barcodes, args=(user_id, file_name, f"https://drive.google.com/uc?id={file_id}", file_id), daemon=True).start()

            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

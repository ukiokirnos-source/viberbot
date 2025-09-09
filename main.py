import io
import threading
import time
import requests
import datetime
from flask import Flask, request, Response

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.rich_media_message import RichMediaMessage
from viberbot.api.messages.data_types.rich_media import RichMedia, Button
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
DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)

# ==== Ініціалізація Viber бота ====
viber = Api(BotConfiguration(
    name='Джексон',
    avatar='https://www.flaticon.com/ru/free-icon/bot_4711994?term=%D0%B1%D0%BE%D1%82&page=1&position=13&origin=tag&related_id=4711994',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()

# ==== Робота з таблицею ====
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

def update_user_limit(row_number, new_limit):
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!C{row_number}",
        valueInputOption="RAW",
        body={"values": [[new_limit]]}
    ).execute()

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Штрихкоди ====
def find_sheet_name(sheet_id, file_base_name):
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
        if not values or (len(values) == 1 and values[0][0] == "[NO_BARCODE]"):
            return None
        return "\n".join(row[0] for row in values if row)
    except Exception as e:
        return f"Помилка при зчитуванні штрихкодів: {str(e)}"

# ==== Delayed send з кнопкою "Помилка" ====
def delayed_send_barcodes(user_id, file_base_name, file_name, public_url):
    time.sleep(80)

    # Надсилаємо фото з кнопкою
    try:
        rich_media = RichMedia(
            Type="rich_media",
            ButtonsGroupColumns=6,
            ButtonsGroupRows=1,
            BgColor="#FFFFFF",
            Buttons=[
                Button(
                    Columns=2,
                    Rows=1,
                    ActionType="reply",
                    ActionBody=f"REPORT:{public_url}",
                    Text="📩 Помилка",
                    TextSize="small",
                    TextVAlign="middle",
                    TextHAlign="center",
                    BgColor="#CCCCCC"
                )
            ]
        )
        viber.send_messages(user_id, [
            RichMediaMessage(rich_media=rich_media, alt_text=f"Фото: {file_name}")
        ])
    except Exception as e:
        print(f"Помилка при надсиланні фото з кнопкою: {e}")

    # Надсилаємо штрихкоди
    sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
    if not sheet_name:
        barcodes_text = f"❌ Не знайдено листа з назвою '{file_base_name}'"
    else:
        barcodes = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
        barcodes_text = barcodes or f"❌ Штрихкодів у фото '{file_name}' не знайдено."

    try:
        viber.send_messages(user_id, [
            TextMessage(text=barcodes_text)
        ])
    except Exception as e:
        print(f"Помилка при надсиланні штрихкодів: {e}")

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")
        ])
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

        # ==== Скарги: пересилаємо адміну оригінальне повідомлення ====
        error_keywords = ["помилка", "error", "bug", "не працює", "fail"]
        if text in error_keywords or any(kw in text for kw in error_keywords):
            try:
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id}): {text}")
                ])
                if getattr(message, "reply_to", None):
                    original_message = message.reply_to
                    if hasattr(original_message, "text"):
                        viber.send_messages(ADMIN_ID, [
                            TextMessage(text=f"📩 Оригінал: {original_message.text}")
                        ])
                    elif hasattr(original_message, "media"):
                        viber.send_messages(ADMIN_ID, [
                            PictureMessage(media=original_message.media, text="📩 Оригінал (фото)")
                        ])
            except Exception as e:
                print(f"Помилка при відправці адміну: {e}")

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
            if ext.lower() not in ['jpg', 'jpeg', 'png']:
                ext = 'jpg'
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base_name = f"photo_{timestamp}"
            file_name = f"{file_base_name}.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(
                    body=file_metadata, media_body=media, fields='id'
                ).execute()
                file_id = file.get('id')
                add_public_permission(file_id)

                update_user_counter(row_num, uploaded_today + 1)

                viber.send_messages(user_id, [
                    TextMessage(text=f"📥 Фото '{file_name}' отримано. Оброблюю (80 сек)...")
                ])

                threading.Thread(
                    target=delayed_send_barcodes,
                    args=(user_id, file_base_name, file_name, f"https://drive.google.com/uc?id={file_id}"),
                    daemon=True
                ).start()
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

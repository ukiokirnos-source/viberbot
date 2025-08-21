import io
import threading
import time
import requests
import datetime
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.keyboard_message import KeyboardMessage, Keyboard, Button
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
viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',
    auth_token=VIBER_TOKEN
))

# ==== Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()

# ==== Google Sheets functions ====
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

# ==== Admin keyboard ====
def send_admin_keyboard(user_id):
    keyboard = {
        "Type": "keyboard",
        "DefaultHeight": True,
        "Buttons": [
            {"Columns": 6, "Rows": 1, "Text": "Перевірити користувачів",
             "ActionType": "reply", "ActionBody": "check_users"},
            {"Columns": 6, "Rows": 1, "Text": "Змінити ліміт",
             "ActionType": "reply", "ActionBody": "change_limit"}
        ]
    }
    viber.send_messages(user_id, [KeyboardMessage(keyboard=keyboard)])

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Sheets for barcodes ====
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

# ==== Delayed sending ====
def delayed_send(user_id, file_base_name, file_name):
    time.sleep(80)
    sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
    if not sheet_name:
        barcodes_text = f"❌ Не знайдено листа '{file_base_name}'"
    else:
        barcodes_text = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
        if barcodes_text is None:
            barcodes_text = f"❌ Штрихкодів у фото '{file_name}' не знайдено."

    # 1. Фото
    photo_url = f"https://drive.google.com/uc?id={file_name}"  # або в тебе можна вставляти прямий URL
    viber.send_messages(user_id, [TextMessage(text=f"📥 Фото оброблено: {file_name}")])

    # 2. Штрихкоди
    viber.send_messages(user_id, [TextMessage(text=f"🔍 Штрихкоди:\n{barcodes_text}")])

    # 3. Кнопка "Помилка"
    keyboard = Keyboard(
        Buttons=[
            Button(
                ActionType='reply',
                ActionBody=f"report_error|{file_name}",
                Text="Помилка",
                Columns=2,
                Rows=1
            )
        ]
    )
    viber.send_messages(user_id, [KeyboardMessage(keyboard=keyboard)])

# ==== Main route ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())
    user_id = getattr(viber_request.sender, 'id', None)
    message_token = getattr(viber_request, 'message_token', None)

    if message_token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(message_token)

    # Новий користувач
    row_num, row = find_user_row(user_id)
    if not row_num:
        add_new_user(user_id, getattr(viber_request.sender, 'name', ''))
        row_num, row = find_user_row(user_id)

    # Фото обробка
    if isinstance(viber_request, ViberMessageRequest) and hasattr(viber_request.message, 'media') and viber_request.message.media:
        image_url = viber_request.message.media
        ext = image_url.split('.')[-1].split('?')[0]
        ext = ext if ext.lower() in ['jpg', 'jpeg', 'png'] else 'jpg'
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base_name = f"photo_{timestamp}"
        file_name = f"{file_base_name}.{ext}"

        try:
            img_data = requests.get(image_url).content
            file_stream = io.BytesIO(img_data)
            media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
            file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            file_id = file.get('id')
            add_public_permission(file_id)

            # Лічильник
            uploaded_today = int(row[3])
            limit = int(row[2])
            if uploaded_today >= limit:
                viber.send_messages(user_id, [TextMessage(text=f"🚫 Досягнуто ліміт {limit} фото")])
                return Response(status=200)
            update_user_counter(row_num, uploaded_today + 1)

            # Відправка після 80 сек
            threading.Thread(
                target=delayed_send,
                args=(user_id, file_base_name, file_name),
                daemon=True
            ).start()

            viber.send_messages(user_id, [TextMessage(text=f"📥 Фото '{file_name}' отримано. Обробка...")])

        except Exception as e:
            viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при завантаженні фото: {str(e)}")])

    # Обробка кнопки "Помилка"
    if isinstance(viber_request, ViberMessageRequest) and hasattr(viber_request.message, 'text'):
        if viber_request.message.text.startswith("report_error|"):
            reported_file = viber_request.message.text.split("|")[1]
            viber.send_messages(ADMIN_ID, [TextMessage(text=f"⚠ Користувач {user_id} скаржиться на фото: {reported_file}")])
            viber.send_messages(user_id, [TextMessage(text="✅ Повідомлення адміну відправлено")])

    return Response(status=200)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

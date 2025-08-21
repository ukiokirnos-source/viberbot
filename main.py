import io
import threading
import time
import requests
import datetime
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.keyboard_message import KeyboardMessage
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
viber = Api(BotConfiguration(name='ФотоЗагрузBot', avatar='', auth_token=VIBER_TOKEN))
processed_message_tokens = set()
pending_limit_change = {}  # для вводу нового ліміту адміном

# ==== Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# ==== Робота з таблицею ====
def get_all_users():
    result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="Лист1!A:D").execute()
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

# ==== Клавіатура адміну ====
def send_admin_keyboard(user_id):
    keyboard = {
        "Type": "keyboard",
        "DefaultHeight": True,
        "Buttons": [
            {"Columns": 6, "Rows": 1, "Text": "Перевірити користувачів", "ActionType": "reply", "ActionBody": "check_users"},
            {"Columns": 6, "Rows": 1, "Text": "Змінити ліміт", "ActionType": "reply", "ActionBody": "change_limit"}
        ]
    }
    viber.send_messages(user_id, [KeyboardMessage(keyboard=keyboard)])

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        drive_service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Штрихкоди ====
def find_sheet_name(sheet_id, file_base_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        for sheet in spreadsheet.get('sheets', []):
            if sheet.get('properties', {}).get('title', '') == file_base_name:
                return file_base_name
        return None
    except Exception as e:
        print(f"Помилка при пошуку листа: {e}")
        return None

def get_barcodes_from_sheet(sheet_id, sheet_name):
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"{sheet_name}!A:A").execute()
        values = result.get('values', [])
        if not values or (len(values) == 1 and values[0][0] == "[NO_BARCODE]"):
            return None
        return "\n".join(row[0] for row in values if row)
    except Exception as e:
        return f"Помилка при зчитуванні штрихкодів: {str(e)}"

def delayed_send_barcodes(user_id, file_base_name, file_name, delay=80):
    time.sleep(delay)
    sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
    if not sheet_name:
        text = f"❌ Не знайдено листа '{file_base_name}'"
    else:
        barcodes_text = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
        if barcodes_text is None:
            text = f"❌ Штрихкодів у фото '{file_name}' не знайдено."
        else:
            text = f"📸 Фото: {file_name}\n🔍 Штрихкоди з листа '{sheet_name}':\n{barcodes_text}"

    keyboard = {
        "Type": "keyboard",
        "DefaultHeight": True,
        "Buttons": [
            {"Columns": 6, "Rows": 1, "Text": "Помилка", "ActionType": "reply", "ActionBody": f"complain_{file_name}"}
        ]
    }
    try:
        viber.send_messages(user_id, [TextMessage(text=text), KeyboardMessage(keyboard=keyboard)])
    except Exception as e:
        print(f"Помилка при надсиланні штрихкодів: {e}")

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [TextMessage(text="Привіт! Надішли фото накладної.\nЩоб дізнатися свій ID: my_id")])
        if viber_request.user.id == ADMIN_ID:
            send_admin_keyboard(viber_request.user.id)
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

        # Кнопка «Помилка»
        if text.startswith("complain_"):
            bad_file = text.replace("complain_", "")
            viber.send_messages(ADMIN_ID, [TextMessage(text=f"⚠️ Користувач {user_name} ({user_id}) позначив фото як помилкове: {bad_file}")])
            viber.send_messages(user_id, [TextMessage(text="Дякуємо, повідомлення адміну відправлено.")])
            return Response(status=200)

        # Адмінські кнопки
        if user_id == ADMIN_ID:
            send_admin_keyboard(user_id)

            if text == "check_users":
                users = get_all_users()
                msg = "Список користувачів:\n" + "\n".join(f"{row[0]} | {row[1]} | Ліміт: {row[2]} | Фото: {row[3]}" for row in users[1:])
                viber.send_messages(user_id, [TextMessage(text=msg)])
                return Response(status=200)

            if text == "change_limit":
                pending_limit_change[user_id] = True
                viber.send_messages(user_id, [TextMessage(text="Введіть: user_id новий_ліміт")])
                return Response(status=200)

            if pending_limit_change.get(user_id):
                parts = text.split()
                if len(parts) == 2:
                    uid, limit_str = parts[0], parts[1]
                    row_num, row = find_user_row(uid)
                    if row_num:
                        update_user_limit(row_num, limit_str)
                        viber.send_messages(user_id, [TextMessage(text=f"Ліміт змінено для {uid} → {limit_str}")])
                    else:
                        viber.send_messages(user_id, [TextMessage(text="Користувач не знайдений")])
                else:
                    viber.send_messages(user_id, [TextMessage(text="Формат: user_id новий_ліміт")])
                pending_limit_change[user_id] = False
                return Response(status=200)

        if text == "my_id":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Додаємо нового користувача
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
            ext = image_url.split('.')[-1].split('?')[0] if image_url else 'jpg'
            if ext.lower() not in ['jpg','jpeg','png']:
                ext = 'jpg'

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base_name = f"photo_{timestamp}"
            file_name = f"{file_base_name}.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents':[GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                add_public_permission(file.get('id'))
                update_user_counter(row_num, uploaded_today + 1)
                viber.send_messages(user_id, [TextMessage(text=f"📥 Фото '{file_name}' отримано. Оброблюю (2 хв)...")])
                threading.Thread(target=delayed_send_barcodes, args=(user_id, file_base_name, file_name), daemon=True).start()
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

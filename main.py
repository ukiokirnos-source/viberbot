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
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/spreadsheets.readonly']

DAILY_LIMIT = 8  # стандартний ліміт фото на день

ADMIN_ID = "тут_твій_user_id"  # встав свій user_id для адмін-панелі

app = Flask(__name__)

# ==== Ініціалізація Viber бота ====
viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()
user_photo_count = {}       # user_id -> кількість фото сьогодні
custom_user_limits = {}     # user_id -> ліміт фото (для адміна можна змінювати)

# ==== Функції ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

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

def delayed_send_barcodes(user_id, file_base_name, file_name, delay=80):
    time.sleep(delay)
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
        viber.send_messages(user_id, [TextMessage(text=text)])
    except Exception as e:
        print(f"Помилка при надсиланні штрихкодів: {e}")

def get_user_limit(user_id):
    return custom_user_limits.get(user_id, DAILY_LIMIT)

def send_admin_keyboard(user_id):
    keyboard = {
        "Type": "keyboard",
        "DefaultHeight": True,
        "Buttons": [
            {"Columns": 6, "Rows": 1, "Text": "Перевірити користувачів", "ActionType": "reply", "ActionBody": "check_users"},
            {"Columns": 6, "Rows": 1, "Text": "Змінити ліміт", "ActionType": "reply", "ActionBody": "change_limit"},
            {"Columns": 6, "Rows": 1, "Text": "Назад", "ActionType": "reply", "ActionBody": "back"}
        ]
    }
    viber.send_messages(user_id, [KeyboardMessage(keyboard=keyboard)])

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    # Привітальне повідомлення
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами у гарній якості.\nЩоб дізнатися свій ID, напиши команду: my_id")
        ])
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
        text = getattr(message, 'text', '').lower()

        # --- Для адміна завжди показуємо кнопки ---
        if user_id == ADMIN_ID:
            send_admin_keyboard(user_id)

        # --- Команда для користувача отримати свій ID ---
        if text == "my_id":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # --- Ліміт фото ---
        count = user_photo_count.get(user_id, 0)
        limit = get_user_limit(user_id)
        if count >= limit:
            viber.send_messages(user_id, [
                TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")
            ])
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
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute()

                file_id = file.get('id')
                add_public_permission(file_id)

                # Збільшуємо лічильник фото
                user_photo_count[user_id] = count + 1
                remaining = limit - user_photo_count[user_id]

                if remaining == 3:
                    viber.send_messages(user_id, [
                        TextMessage(text=f"⚠️ У вас залишилось {remaining} фото на сьогодні.")
                    ])

                viber.send_messages(user_id, [
                    TextMessage(text=f"📥 Фото '{file_name}' отримано.\nОброблюю. Час очікування: 2 хв")
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

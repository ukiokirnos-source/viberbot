import os
import json
import datetime
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages import TextMessage
from viberbot.api.event_type import EventType
from viberbot.api.messages.data_types.keyboard import Keyboard
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ---------------- CONFIG ---------------- #
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_TOKEN_FILE = "token.json"
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/spreadsheets']

DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="  # ID адміна

# ---------------- INIT ---------------- #
app = Flask(__name__)
bot_config = BotConfiguration(
    name="BarcodeBot",
    avatar="",
    auth_token=VIBER_TOKEN
)
viber = Api(bot_config)

# ---------------- DATA STORAGE ---------------- #
user_limits = {}  # {user_id: {"limit": int, "used": int, "date": "YYYY-MM-DD"}}
user_names = {}   # {user_id: name}
last_barcode_messages = {}  # {user_id: [{"file_name": ..., "timestamp": ...}]}

# ---------------- GOOGLE API ---------------- #
creds = Credentials.from_service_account_file(GOOGLE_TOKEN_FILE, scopes=SCOPES)
service_drive = build('drive', 'v3', credentials=creds)
service_sheets = build('sheets', 'v4', credentials=creds)

# ---------------- UTILS ---------------- #
def reset_daily_limit(user_id):
    today = datetime.date.today().isoformat()
    if user_id not in user_limits or user_limits[user_id]["date"] != today:
        user_limits[user_id] = {"limit": DAILY_LIMIT_DEFAULT, "used": 0, "date": today}

def is_admin(user_id):
    return user_id == ADMIN_ID

def send_message(user_id, text, keyboard=None):
    msg = TextMessage(text=text, keyboard=keyboard) if keyboard else TextMessage(text=text)
    viber.send_messages(user_id, [msg])

def error_keyboard(file_name):
    return {
        "Type": "keyboard",
        "DefaultHeight": False,
        "Buttons": [
            {
                "Columns": 6,
                "Rows": 1,
                "BgColor": "#FF0000",  # червона кнопка
                "ActionType": "reply",
                "ActionBody": f"error_report|{file_name}",
                "Text": "<font color='#FFFFFF'>⚠️ Помилка</font>"
            }
        ]
    }

# ---------------- ROUTES ---------------- #
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    # Події
    if viber_request.event_type == EventType.CONVERSATION_STARTED:
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Вітаю! Надішліть фото зі штрихкодами.")
        ])

    elif viber_request.event_type == EventType.SUBSCRIBED:
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Дякую за підписку! Надішліть фото.")
        ])

    elif viber_request.event_type == EventType.MESSAGE:
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        user_names[user_id] = user_name

        reset_daily_limit(user_id)

        if isinstance(viber_request.message, TextMessage):
            text = viber_request.message.text.strip()

            # Адмінська команда: зміна ліміту
            if is_admin(user_id) and text.lower().startswith("змінити ліміт"):
                try:
                    new_limit = int(text.split()[-1])
                    DAILY_LIMIT_DEFAULT = new_limit
                    send_message(user_id, f"✅ Ліміт змінено на {new_limit} фото на день.")
                except ValueError:
                    send_message(user_id, "❌ Введіть команду у форматі: Змінити ліміт 10")
                return Response(status=200)

            # Обробка кнопки Помилка
            if text.startswith("error_report|"):
                file_name = text.split("|")[1]
                timestamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
                admin_text = (
                    f"⚠️ Помилка від користувача {user_name}\n"
                    f"Фото: {file_name}\n"
                    f"Час: {timestamp}"
                )
                send_message(ADMIN_ID, admin_text)
                send_message(user_id, "✅ Вашу скаргу передано адміну.")
                return Response(status=200)

            # Якщо не команда
            send_message(user_id, "Я чекаю фото зі штрихкодами 😉")
            return Response(status=200)

        # Обробка фото
        if viber_request.message.media:
            if user_limits[user_id]["used"] >= user_limits[user_id]["limit"]:
                send_message(user_id, "❌ Ви досягли денного ліміту.")
                return Response(status=200)

            # Імітуємо розпізнавання штрихкодів
            file_name = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            barcodes_text = f"📸 Фото: {file_name}\nШтрихкоди:\n123456789012\n987654321098"

            # Надсилаємо повідомлення з кнопкою "Помилка"
            keyboard = error_keyboard(file_name)
            send_message(user_id, barcodes_text, keyboard=keyboard)

            # Оновлюємо ліміт
            user_limits[user_id]["used"] += 1
            return Response(status=200)

    return Response(status=200)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

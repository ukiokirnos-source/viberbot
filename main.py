import logging
import json
import datetime
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages import TextMessage, PictureMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest, ViberSubscribedRequest, ViberUnsubscribedRequest
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# ------------------ НАЛАШТУВАННЯ ------------------
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_TOKEN_FILE = "token.json"
SCOPES = ['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/spreadsheets']
DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)
viber = Api(BotConfiguration(
    name='BarcodeBot',
    avatar='',
    auth_token=VIBER_TOKEN
))

# ------------------ ЛОГІНГ ------------------
logging.basicConfig(level=logging.INFO)

# ------------------ ПІДКЛЮЧЕННЯ GOOGLE ------------------
creds = Credentials.from_service_account_file(GOOGLE_TOKEN_FILE, scopes=SCOPES)
service_sheets = build('sheets', 'v4', credentials=creds)
sheet = service_sheets.spreadsheets()

# ------------------ СТАН ------------------
user_limits = {}            # {user_id: limit}
last_barcode_messages = {}  # {user_id: [{"file_name": str, "time": datetime}]}

# ------------------ ДОПОМІЖНІ ФУНКЦІЇ ------------------
def get_limit(user_id):
    return user_limits.get(user_id, DAILY_LIMIT_DEFAULT)

def set_limit(user_id, new_limit):
    user_limits[user_id] = new_limit

def save_barcode_info(user_id, file_name):
    if user_id not in last_barcode_messages:
        last_barcode_messages[user_id] = []
    last_barcode_messages[user_id].append({
        "file_name": file_name,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    # зберігаємо тільки останні 5, щоб не роздувати пам'ять
    last_barcode_messages[user_id] = last_barcode_messages[user_id][-5:]

def send_barcode_message(user_id, file_name, codes_text):
    # клавіатура тільки для цього повідомлення
    keyboard = {
        "Type": "keyboard",
        "DefaultHeight": False,
        "Buttons": [
            {
                "Columns": 6,
                "Rows": 1,
                "BgColor": "#FF0000",
                "ActionType": "reply",
                "ActionBody": f"error_report|{file_name}",
                "Text": "<font color='#FFFFFF'>⚠️ Помилка</font>"
            }
        ]
    }
    viber.send_messages(user_id, [
        TextMessage(text=codes_text, keyboard=keyboard)
    ])
    save_barcode_info(user_id, file_name)

def report_error_to_admin(user_id, file_name):
    text = f"⚠️ Користувач {user_id} повідомив про помилку\nФото: {file_name}"
    viber.send_messages(ADMIN_ID, [TextMessage(text=text)])

# ------------------ ОБРОБКА ПОВІДОМЛЕНЬ ------------------
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())
    logging.info("Received request: %s", viber_request)

    # Користувач запустив бота
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Надсилай фото штрихкодів.")
        ])
        return Response(status=200)

    # Нове повідомлення
    elif isinstance(viber_request, ViberMessageRequest):
        user_id = viber_request.sender.id
        message = viber_request.message

        # Кнопка натиснута
        if isinstance(message, TextMessage) and message.text.startswith("error_report|"):
            file_name = message.text.split("|", 1)[1]
            report_error_to_admin(user_id, file_name)
            viber.send_messages(user_id, [
                TextMessage(text="Дякуємо, ми повідомили адміністратора про помилку.")
            ])
            return Response(status=200)

        # Користувач відправив текст адміну: зміна ліміту
        if user_id == ADMIN_ID and isinstance(message, TextMessage) and message.text.startswith("set_limit"):
            try:
                _, target_id, new_limit = message.text.split()
                new_limit = int(new_limit)
                set_limit(target_id, new_limit)
                viber.send_messages(ADMIN_ID, [TextMessage(text=f"Ліміт користувача {target_id} змінено на {new_limit}")])
            except Exception as e:
                viber.send_messages(ADMIN_ID, [TextMessage(text=f"Помилка: {e}")])
            return Response(status=200)

        # Якщо прийшло фото
        if isinstance(message, PictureMessage):
            file_name = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            # Тут повинно бути розпізнавання штрихкодів
            fake_codes = ["123456789012", "987654321098"]  # тестові коди
            codes_text = f"📸 Фото: {file_name}\n🔍 Штрихкоди:\n" + "\n".join(fake_codes)
            send_barcode_message(user_id, file_name, codes_text)
            return Response(status=200)

        # Інакше просто текст
        if isinstance(message, TextMessage):
            viber.send_messages(user_id, [TextMessage(text="Надішли фото зі штрихкодами.")])
            return Response(status=200)

    return Response(status=200)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080, debug=True)

import io
import threading
import requests
import datetime
import traceback
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
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
SCRIPT_URL = "https://script.google.com/macros/s/AKfycbw3qol9XKHcuR8Z0r72bqfnr60S0dL1IeNSqqsa49YqYujuH00MYK1qEvqEIP-ALF4bnw/exec"

app = Flask(__name__)

# ==== Ініціалізація Viber ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google ====
print("[INIT] Ініціалізація Google API...")
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)
print("[INIT] Google API готовий")

processed_message_tokens = set()
pending_reports = {}

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        print(f"[DRIVE] Додаю публічний доступ до файлу {file_id}")
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"[ERROR] Помилка при додаванні доступу: {e}")
        traceback.print_exc()

# ==== Apps Script ====
def process_barcodes(public_url):
    """Викликає Apps Script для обробки зображення та отримання штрихкодів"""
    try:
        print(f"[SCRIPT] Викликаю Apps Script для URL {public_url}")
        resp = requests.post(SCRIPT_URL, json={"imageUrl": public_url}, timeout=40)
        print(f"[SCRIPT] Статус відповіді: {resp.status_code}")
        print(f"[SCRIPT] Тіло відповіді: {resp.text}")
        data = resp.json()
        barcodes = data.get("barcodes", [])
        if not barcodes:
            print("[SCRIPT] Штрихкодів не знайдено.")
            return ["❌ Штрихкодів не знайдено."]
        print(f"[SCRIPT] Отримано штрихкоди: {barcodes}")
        return barcodes
    except Exception as e:
        print(f"[ERROR] Помилка при запиті до Apps Script: {e}")
        traceback.print_exc()
        return [f"❌ Помилка при запиті до Apps Script: {e}"]

# ==== Відправка штрихкодів ====
def delayed_send(user_id, file_name, public_url):
    try:
        barcodes = process_barcodes(public_url)
        barcodes_text = "\n".join(barcodes)
        viber.send_messages(user_id, [
            PictureMessage(media=public_url),
            TextMessage(text=barcodes_text)
        ])
    except Exception as e:
        print(f"[ERROR] Помилка при надсиланні штрихкодів: {e}")
        traceback.print_exc()

    try:
        rich_media = {
            "Type": "rich_media",
            "ButtonsGroupColumns": 6,
            "ButtonsGroupRows": 1,
            "BgColor": "#FFFFFF",
            "Buttons": [
                {
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
                }
            ]
        }
        pending_reports[file_name] = public_url
        viber.send_messages(user_id, [
            RichMediaMessage(rich_media=rich_media, min_api_version=2, alt_text="Скарга")
        ])
    except Exception as e:
        print(f"[ERROR] Помилка при створенні кнопки: {e}")
        traceback.print_exc()

# ==== Основна логіка ====
@app.route('/', methods=['POST'])
def incoming():
    try:
        viber_request = viber.parse_request(request.get_data())
    except Exception as e:
        print(f"[ERROR] Не вдалося розпарсити Viber запит: {e}")
        traceback.print_exc()
        return Response(status=500)

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Надішли мені фото накладної — я знайду штрихкоди.")
        ])
        return Response(status=200)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        token = viber_request.message_token

        if token in processed_message_tokens:
            print(f"[SKIP] Повторне повідомлення token={token}")
            return Response(status=200)

        processed_message_tokens.add(token)

        text = getattr(message, 'text', '').strip().lower()

        # === Скарга ===
        if text.startswith("report_"):
            file_name = text.replace("report_", "")
            if file_name in pending_reports:
                photo_url = pending_reports.pop(file_name)
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від користувача: {user_id}"),
                    PictureMessage(media=photo_url, text=file_name)
                ])
                viber.send_messages(user_id, [TextMessage(text="✅ Скаргу відправлено адміну.")])
            return Response(status=200)

        # === Фото ===
        if hasattr(message, 'media') and message.media:
            try:
                image_url = message.media
                if image_url in processed_message_tokens:
                    print(f"[SKIP] Повторне фото {image_url}")
                    return Response(status=200)
                processed_message_tokens.add(image_url)

                ext = image_url.split('.')[-1].split('?')[0]
                if ext.lower() not in ['jpg', 'jpeg', 'png']:
                    ext = 'jpg'

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"photo_{timestamp}.{ext}"

                print(f"[PHOTO] Отримано фото: {file_name}")
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(
                    body=file_metadata, media_body=media, fields='id'
                ).execute()

                file_id = file.get('id')
                add_public_permission(file_id)
                public_url = f"https://drive.google.com/uc?id={file_id}"

                # 🔹 коротке повідомлення
                viber.send_messages(user_id, [
                    TextMessage(text=f"📸 Фото отримано: {file_name}")
                ])

                # окремий потік для обробки
                def trigger_script_and_send():
                    try:
                        process_barcodes(public_url)
                        import time
                        time.sleep(5)
                        delayed_send(user_id, file_name, public_url)
                    except Exception as e:
                        print(f"[ERROR] trigger_script_and_send: {e}")
                        traceback.print_exc()

                threading.Thread(target=trigger_script_and_send, daemon=True).start()

            except Exception as e:
                print(f"[ERROR] Помилка при обробці фото: {e}")
                traceback.print_exc()
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    print("[START] Бот запущено на порту 5000")
    app.run(host='0.0.0.0', port=5000)

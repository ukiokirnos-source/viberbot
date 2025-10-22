import os
import io
import threading
import time
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
from google.cloud import vision

# ==== Налаштування ====
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
GOOGLE_USER_KEY = json.loads(os.environ['GOOGLE_SA_JSON'])
GOOGLE_VISION_KEY = json.loads(os.environ['GOOGLE_VISION_JSON'])
DAILY_LIMIT_DEFAULT = 12
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)

# ==== Viber ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Google API ====
creds = Credentials.from_authorized_user_info(GOOGLE_USER_KEY)
drive_service = build('drive', 'v3', credentials=creds)
vision_client = vision.ImageAnnotatorClient.from_service_account_info(GOOGLE_VISION_KEY)

# ==== Лічильники ====
processed_message_tokens = set()
user_uploads = {}  # user_id: кількість завантажених фото сьогодні
pending_reports = {}  # file_name: URL

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        drive_service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Vision API ====
def extract_barcodes(file_stream):
    try:
        image = vision.Image(content=file_stream.read())
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        if not texts:
            return []
        text = texts[0].description.replace("O", "0").replace("I", "1").replace("L", "1")
        barcodes = [s for s in text.split() if s.isdigit() and 8 <= len(s) <= 18]
        return list(set(barcodes))
    except Exception as e:
        print(f"Vision API error: {e}")
        return []

# ==== Надсилання фото + кнопки + штрихкодів ====
def delayed_send(user_id, file_name, public_url, img_bytes):
    time.sleep(8)
    try:
        # 1. Фото
        viber.send_messages(user_id, [PictureMessage(media=public_url, text=f"📸 Фото: {file_name}")])

        # 2. Кнопка "Скарга"
        rich_media = {
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
        pending_reports[file_name] = public_url
        viber.send_messages(user_id, [RichMediaMessage(rich_media=rich_media, min_api_version=2, alt_text="Скарга")])

        # 3. Штрихкоди
        barcodes = extract_barcodes(io.BytesIO(img_bytes))
        text = "\n".join(barcodes) if barcodes else f"❌ Штрихкодів у фото '{file_name}' не знайдено."
        viber.send_messages(user_id, [TextMessage(text=text)])

    except Exception as e:
        print(f"Помилка при delayed_send: {e}")

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Відправ мені фото зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")
        ])
        return Response(status=200)

    token = getattr(viber_request, 'message_token', None)
    if token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(token)

    if isinstance(viber_request, ViberMessageRequest):
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        message = viber_request.message
        text = getattr(message, 'text', '').strip().lower()

        # ==== Кнопка "Скарга" ====
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

        # Команда айді
        if text == "айді":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Ліміти
        uploaded = user_uploads.get(user_id, 0)
        if uploaded >= DAILY_LIMIT_DEFAULT:
            viber.send_messages(user_id, [TextMessage(text=f"🚫 Ліміт {DAILY_LIMIT_DEFAULT} фото на сьогодні.")])
            return Response(status=200)

        # Фото
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

                user_uploads[user_id] = uploaded + 1
                viber.send_messages(user_id, [TextMessage(text=f"📥 Фото отримано. Обробляю...")])

                threading.Thread(
                    target=delayed_send,
                    args=(user_id, file_name, f"https://drive.google.com/uc?id={file_id}", img_data),
                    daemon=True
                ).start()
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))

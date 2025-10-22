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

VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
GOOGLE_TOKEN_FILE = "token.json"
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
APPS_SCRIPT_WEBAPP_URL = "https://script.google.com/macros/s/AKfycbxnEtNrSKCWimbUXyVLA-xF7ygrlYge40ValoDALGzjcTdU8-7mwAvsxFFiQz9GRc_v4A/exec"

app = Flask(__name__)

viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, ['https://www.googleapis.com/auth/drive.file'])
drive_service = build('drive', 'v3', credentials=creds)

processed_message_tokens = set()
pending_reports = {}

# ==== Обробка фото через Apps Script ====
def get_barcodes_from_apps_script(file_name):
    try:
        response = requests.post(APPS_SCRIPT_WEBAPP_URL, json={'fileName': file_name}, timeout=30)
        data = response.json()
        if 'barcodes' in data and data['barcodes']:
            return "\n".join(data['barcodes'])
        elif 'error' in data:
            return f"❌ Помилка Apps Script: {data['error']}"
        else:
            return f"❌ Штрихкодів у фото '{file_name}' не знайдено."
    except Exception as e:
        return f"❌ Помилка при виклику Apps Script: {e}"

def delayed_send_barcodes(user_id, file_name, public_url):
    try:
        # 1. Надсилаємо фото
        viber.send_messages(user_id, [PictureMessage(media=public_url, text=file_name)])

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
        pending_reports[file_name] = public_url
        viber.send_messages(user_id, [RichMediaMessage(rich_media=rich_media_dict, min_api_version=2)])

        # 3. Штрихкоди через Apps Script
        barcodes_text = get_barcodes_from_apps_script(file_name)
        viber.send_messages(user_id, [TextMessage(text=barcodes_text)])
    except Exception as e:
        print(f"Помилка в delayed_send_barcodes: {e}")

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
        text = getattr(message, 'text', '').strip().lower()

        # Команда Айді
        if text == "айді":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Обробка фото
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg','jpeg','png']: ext='jpg'
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"photo_{timestamp}.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file = drive_service.files().create(body={'name': file_name,'parents':[GDRIVE_FOLDER_ID]}, media_body=media, fields='id').execute()
                file_id = file.get('id')
                public_url = f"https://drive.google.com/uc?id={file_id}"

                # Відправка у відкладений потік (але штрихкоди тепер миттєво через Apps Script)
                threading.Thread(target=delayed_send_barcodes, args=(user_id, file_name, public_url), daemon=True).start()

            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

import io
import base64
import requests
import hashlib
import threading
from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

# ================== GOOGLE SHEET LOGGING ==================
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime

FOLDER_ID = '1FteobWxkEUxPq1kBhUiP70a4-X0slbWe'
SHEET_ID = '1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8'

scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive.file",
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)

def logToSheet(message, user_id=None):
    try:
        try:
            sh = sheet.worksheet('Логи')
        except:
            sh = sheet.add_worksheet(title='Логи', rows="1000", cols="3")
        now = datetime.datetime.utcnow().isoformat()
        sh.append_row([now, user_id or "N/A", message])
    except Exception as e:
        print(f"LOG ERROR: {e}")

# ================== VIBER BOT ==================
app = Flask(__name__)

VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
WEB_APP_URL = "https://script.google.com/macros/s/AKfycby0SbHTWa7vnKkVj4UU9mTBJH9daldHt_5Pvw8BtzsQKYDt5TU7NvYDLgRaCyZGle4khg/exec"
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

viber = Api(BotConfiguration(name='Джексон🤖', auth_token=VIBER_TOKEN, avatar=""))

processed_tokens = set()
processed_images = set()

# ================== ASYNC OCR WITH LOG ==================
def process_image_async(img64, user_id):
    logToSheet("Starting OCR processing", user_id)
    try:
        r = requests.post(WEB_APP_URL, json={"image": img64}, timeout=60)
        r.raise_for_status()
        barcodes = r.json().get("barcodes", [])
        logToSheet(f"OCR result: {barcodes}", user_id)
    except Exception as e:
        barcodes = []
        logToSheet(f"OCR error: {e}", user_id)
        viber.send_messages(user_id, [TextMessage(text=f"❌ OCR помилка або таймаут: {e}")])
        return

    text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
    viber.send_messages(user_id, [TextMessage(text=text)])
    logToSheet("Response sent to user", user_id)

# ================== VIBER ROUTE ==================
@app.route('/', methods=['POST'])
def incoming():
    req = viber.parse_request(request.get_data())

    # Привітальне повідомлення при старті
    if isinstance(req, ViberConversationStartedRequest):
        viber.send_messages(req.user.id, [TextMessage(text="Привіт! Відправ фото, а я відправлю штрих-код 😊")])
        logToSheet("Conversation started", req.user.id)
        return Response(status=200)

    # Унікальний токен, щоб не дублювати обробку
    token = getattr(req, 'message_token', None)
    if token in processed_tokens:
        return Response(status=200)
    processed_tokens.add(token)

    # Якщо це повідомлення користувача
    if isinstance(req, ViberMessageRequest):
        msg = req.message
        user_id = req.sender.id

        # Обробка фото
        if hasattr(msg, 'media') and msg.media:
            logToSheet("Photo received", user_id)
            try:
                img = requests.get(msg.media, timeout=10).content
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Не вдалося завантажити фото: {e}")])
                logToSheet(f"Failed to download image: {e}", user_id)
                return Response(status=200)

            if len(img) < 5000:
                viber.send_messages(user_id, [TextMessage(text="❌ Фото занадто маленьке або пошкоджене")])
                logToSheet("Image too small", user_id)
                return Response(status=200)

            img_hash = hashlib.sha256(img).hexdigest()
            if img_hash in processed_images:
                logToSheet("Image already processed", user_id)
                return Response(status=200)
            processed_images.add(img_hash)

            img64 = base64.b64encode(img).decode()
            viber.send_messages(user_id, [TextMessage(text="Отримав фото, обробляю...")])
            threading.Thread(target=process_image_async, args=(img64, user_id)).start()

    return Response(status=200)

# ================== ПРОСТИЙ PING ==================
@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

# ================== RUN ==================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

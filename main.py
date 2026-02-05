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

app = Flask(__name__)

# ================== НАЛАШТУВАННЯ ==================
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbyVE81tDdv7Gi9LHvYNBM7u_EQnQ6JKF2vfFQRZAg32qYKgC0xk0qZAdITdbe5kQ_J1fg/exec"  # Script URL
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

viber = Api(BotConfiguration(name='Джексон🤖', auth_token=VIBER_TOKEN, avatar=""))

processed_tokens = set()
processed_images = set()

# ================== ASYNC OCR ==================
def process_image_async(img64, user_id):
    try:
        r = requests.post(WEB_APP_URL, json={"image": img64}, timeout=60)
        r.raise_for_status()
        barcodes = r.json().get("barcodes", [])
    except Exception as e:
        barcodes = []
        viber.send_messages(user_id, [TextMessage(text=f"❌ OCR помилка або таймаут: {e}")])
        return

    text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
    viber.send_messages(user_id, [TextMessage(text=text)])

# ================== ВХІДНІ ПОВІДОМЛЕННЯ ==================
@app.route('/', methods=['POST'])
def incoming():
    req = viber.parse_request(request.get_data())

    # Привітальне повідомлення при старті
    if isinstance(req, ViberConversationStartedRequest):
        viber.send_messages(req.user.id, [TextMessage(text="Привіт! Відправ фото, а я відправлю штрих-код 😊")])
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
            try:
                img = requests.get(msg.media, timeout=10).content
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Не вдалося завантажити фото: {e}")])
                return Response(status=200)

            if len(img) < 5000:
                viber.send_messages(user_id, [TextMessage(text="❌ Фото занадто маленьке або пошкоджене")])
                return Response(status=200)

            img_hash = hashlib.sha256(img).hexdigest()
            if img_hash in processed_images:
                return Response(status=200)
            processed_images.add(img_hash)

            img64 = base64.b64encode(img).decode()

            # Миттєве повідомлення користувачу
            viber.send_messages(user_id, [TextMessage(text="Отримав фото, обробляю...")])

            # Асинхронний POST на Google Script
            threading.Thread(target=process_image_async, args=(img64, user_id)).start()

    return Response(status=200)

# ================== ПРОСТИЙ PING ==================
@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

# ================== RUN ==================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

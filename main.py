import io
import base64
import requests
import datetime
import hashlib

from flask import Flask, request, Response
from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.rich_media_message import RichMediaMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

app = Flask(__name__)

VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
WEB_APP_URL = "https://script.google.com/macros/s/AKfycb.../exec"  # твій Script URL
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

viber = Api(BotConfiguration(name='Джексон🤖', auth_token=VIBER_TOKEN, avatar=""))

processed_tokens = set()
processed_images = set()

@app.route('/', methods=['POST'])
def incoming():
    req = viber.parse_request(request.get_data())
    if isinstance(req, ViberConversationStartedRequest):
        viber.send_messages(req.user.id, [TextMessage(text="Привіт! Відправ фото, а я відправлю штрих-код 😊")])
        return Response(status=200)

    token = getattr(req, 'message_token', None)
    if token in processed_tokens:
        return Response(status=200)
    processed_tokens.add(token)

    if isinstance(req, ViberMessageRequest):
        msg = req.message
        user_id = req.sender.id

        if hasattr(msg, 'media') and msg.media:
            img = requests.get(msg.media, timeout=10).content
            if len(img) < 5000:
                viber.send_messages(user_id, [TextMessage(text="❌ Фото занадто маленьке або пошкоджене")])
                return Response(status=200)

            img_hash = hashlib.sha256(img).hexdigest()
            if img_hash in processed_images:
                return Response(status=200)
            processed_images.add(img_hash)

            img64 = base64.b64encode(img).decode()
            try:
                r = requests.post(WEB_APP_URL, json={"image": img64}, timeout=20)
                barcodes = r.json().get("barcodes", [])
            except Exception as e:
                barcodes = []
                viber.send_messages(user_id, [TextMessage(text=f"❌ OCR помилка: {e}")])

            text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
            viber.send_messages(user_id, [TextMessage(text=text)])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

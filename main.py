import io
import base64
import requests
import datetime
import hashlib
import re
from flask import Flask, request

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАЛАШТУВАННЯ ==================
WHATSAPP_TOKEN = "EAAwJZC7glYnQBRDoArCriXhHPxqu2kNMd9XCZBHMOgZBneeWohP2kerp6XAAuRwr84gpOk2aZAbyAU7Cj1Q0Deg9LBCYqLTAIrSGenSWGP32fn2pi5g4qiVAh6pASk4qnmCL0SnqXptTEIVEybZBUOZBlSOgngNUSjOfK4fof5gXFcTMEZBPMoMbnuZAxbjFxgIyR3nVHKgpUU03a8JpYNTj0qHIlBW4yfZCsAb7FsvnZBT94e9pXTW7fGKhJzW8ZBgOxqgPhj56VUAluHFWEJliwYgSW44"
PHONE_NUMBER_ID = "1017587501445701"
VERIFY_TOKEN = "my_token_123"
ADMIN_PHONE = "380661153200"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbz5dCoxPzCC_GdDDCEsjZQtRwW74rqVvaPpp0Uwj7ioD5DRy9--An-4aiqgJNzWKktKJA/exec"
GMAIL_TOKEN_FILE = "gmail_token.json"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"

DAILY_LIMIT_DEFAULT = 12
TOTAL_LIMIT = 999

# ================== INIT ==================
app = Flask(__name__)

gmail = build("gmail", "v1", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))
drive = build("drive", "v3", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))
sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))

processed_images = set()
pending_reports = {}

# ================== HELPERS ==================
def send_text(phone, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text}
    }
    requests.post(url, headers=headers, json=data)


def send_image(phone, image_url):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {"link": image_url}
    }
    requests.post(url, headers=headers, json=data)


def upload_photo(bytes_, name):
    media = MediaIoBaseUpload(io.BytesIO(bytes_), mimetype='image/jpeg')
    file = drive.files().create(
        body={'name': name, 'parents': [GDRIVE_FOLDER_ID]},
        media_body=media,
        fields='id'
    ).execute()

    drive.permissions().create(
        fileId=file['id'],
        body={'type': 'anyone', 'role': 'reader'}
    ).execute()

    return f"https://drive.google.com/uc?id={file['id']}"


def normalize_barcode(code):
    if not code:
        return None
    code = code.upper().strip()
    replacements = {'O':'0','I':'1','L':'1','S':'5','B':'8','Z':'2'}
    code = ''.join(replacements.get(c,c) for c in code)
    code = re.sub(r'[^0-9]', '', code)
    return code if code else None


# ================== WEBHOOK ==================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "error", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")

        if not messages:
            return "ok", 200

        msg = messages[0]
        phone = msg["from"]

        # ===== ФОТО =====
        if msg["type"] == "image":
            media_id = msg["image"]["id"]

            media_url = requests.get(
                f"https://graph.facebook.com/v18.0/{media_id}",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            ).json()["url"]

            img = requests.get(
                media_url,
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            ).content

            # штрихкод
            try:
                r = requests.post(WEB_APP_URL, json={"image": base64.b64encode(img).decode()})
                raw = r.json().get("barcodes", [])
                barcodes = [normalize_barcode(b) for b in raw if normalize_barcode(b)]
            except:
                barcodes = []

            text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
            send_text(phone, text)

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)

            pending_reports[fname] = url

            send_image(phone, url)
            send_text(phone, f"Напиши:\nreport_{fname}\nякщо є проблема")

        # ===== ТЕКСТ =====
        elif msg["type"] == "text":
            text = msg["text"]["body"]

            if text.startswith("report_"):
                fname = text.replace("report_", "")

                if fname in pending_reports:
                    send_text(ADMIN_PHONE, f"⚠️ Скарга від {phone}")
                    send_image(ADMIN_PHONE, pending_reports[fname])

                send_text(phone, "Скарга відправлена ✅")

            else:
                send_text(phone, "Надішли фото або номер документа")

    except Exception as e:
        print("ERROR:", e)

    return "ok", 200


# ================== RUN ==================
if __name__ == "__main__":
    app.run(port=5000)

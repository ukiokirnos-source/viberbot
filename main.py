import io
import base64
import requests
import datetime
import re
import time
import threading
from flask import Flask, request

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАЛАШТУВАННЯ ==================
WHATSAPP_TOKEN = "EAAwJZC7glYnQBRDwwbdgtCvCxSCdvANFKUDUOsVhm7DyLQZAZA5Ch5EnZALH3pzmKcWZATXaQYOXm26Qbzm06miz6pjPjsMoHEKxS5v2LHGadWxZCNg9IMi2ibeOgqwWbftWu9NmIb19PQ8Ynyp2PKRHZA3DLdfpW0BPdYPO4AkngdT9sC1pqI6TY78ZBUiequqZBFAZDZD"
PHONE_NUMBER_ID = "1017587501445701"
ADMIN_PHONE = "380661153200"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbz5dCoxPzCC_GdDDCEsjZQtRwW74rqVvaPpp0Uwj7ioD5DRy9--An-4aiqgJNzWKktKJA/exec"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GMAIL_TOKEN_FILE = "gmail_token.json"

# ================== INIT ==================
app = Flask(__name__)

gmail = build("gmail", "v1", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))
drive = build("drive", "v3", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))
sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))

pending_reports = {}

# ================== HELPERS ==================
def normalize_barcode(code):
    if not code:
        return None
    code = re.sub(r'[^0-9]', '', str(code))
    return code if code else None


def send_text(phone, text, reply_to=None):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text}
    }

    if reply_to:
        payload["context"] = {"message_id": reply_to}

    requests.post(url, headers=headers, json=payload)


def send_image_admin(url):
    url_api = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    data = {
        "messaging_product": "whatsapp",
        "to": ADMIN_PHONE,
        "type": "image",
        "image": {"link": url}
    }

    requests.post(url_api, headers=headers, json=data)


def send_report_button(phone, fname):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Є проблема з фото?"},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"report_{fname}",
                            "title": "⚠️ Скарга"
                        }
                    }
                ]
            }
        }
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


# ================== WEBHOOK ==================
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

        # ================== IMAGE ==================
        if msg["type"] == "image":

            media_id = msg["image"]["id"]

            media_resp = requests.get(
                f"https://graph.facebook.com/v18.0/{media_id}",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            ).json()

            if "url" not in media_resp:
                return "ok", 200

            media_url = media_resp["url"]

            img = requests.get(
                media_url,
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            ).content

            try:
                r = requests.post(WEB_APP_URL, json={"image": base64.b64encode(img).decode()}, timeout=20)
                data_bc = r.json() if r.ok else {}
                raw = data_bc.get("barcodes", []) or data_bc.get("result", [])
                barcodes = [normalize_barcode(b) for b in raw if b]
            except:
                barcodes = []

            response_text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"

            # reply на фото
            send_text(phone, response_text, reply_to=msg["id"])

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)

            pending_reports[fname] = url

            send_report_button(phone, fname)

        # ================== TEXT ==================
        elif msg["type"] in ["text", "interactive"]:

            payload = ""

            if msg["type"] == "text":
                payload = msg["text"]["body"]
            else:
                payload = msg["interactive"]["button_reply"]["id"]

            if payload.startswith("report_"):
                fname = payload.replace("report_", "")

                if fname in pending_reports:
                    send_text(ADMIN_PHONE, f"⚠️ Скарга від {phone}")
                    send_image_admin(pending_reports[fname])

                send_text(phone, "Скарга відправлена ✅")

    except Exception as e:
        print("ERROR:", e)

    return "ok", 200


if __name__ == "__main__":
    app.run(port=5000)

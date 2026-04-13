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
WHATSAPP_TOKEN = "EAAwJZC7glYnQBRM4PwjO0xZB8h2sjfubxUjpXwMilA5qBTk4pz8v0XinFCxomRWZCcRHGkBngI8fTXjhQjfegFs8GoZCzwpwUymp2I1FQkNj78yw02U0drLOvnOCZCQtHuA2CGHqVsZAfH7EZBKFKDYno3XvtZCGKZBVvzaZBLCWIXper7dYDi7cxIR2zJxhusEZBt7ZAQZDZD"
PHONE_NUMBER_ID = "1017587501445701"
VERIFY_TOKEN = "my_token_123"
ADMIN_PHONE = "380661153200"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbz5dCoxPzCC_GdDDCEsjZQtRwW74rqVvaPpp0Uwj7ioD5DRy9--An-4aiqgJNzWKktKJA/exec"
GMAIL_TOKEN_FILE = "gmail_token.json"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"

# ================== INIT ==================
app = Flask(__name__)

gmail = build("gmail", "v1", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))
drive = build("drive", "v3", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))

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


def send_report_button(phone, fname):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "Є проблема з фото?"
            },
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

        # ================== ФОТО ==================
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

            # ===== ШТРИХКОД =====
            try:
                r = requests.post(
                    WEB_APP_URL,
                    json={"image": base64.b64encode(img).decode()},
                    timeout=20
                )

                print("BARCODE RAW:", r.text)

                try:
                    data_bc = r.json()
                except:
                    data_bc = {}

                raw = (
                    data_bc.get("barcodes")
                    or data_bc.get("result")
                    or []
                )

                barcodes = [normalize_barcode(b) for b in raw if normalize_barcode(b)]

            except Exception as e:
                print("BARCODE ERROR:", e)
                barcodes = []

            # ВІДПОВІДЬ
            text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
            send_text(phone, text)

            # upload
            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)

            pending_reports[fname] = url

            send_image(phone, url)

            # КНОПКА (ОСТАННЯ ПЕРЕД ГОТОВО)
            send_report_button(phone, fname)

            send_text(phone, "------ ГОТОВО ------")

        # ================== ТЕКСТ ==================
        elif msg["type"] == "text":

            text = msg["text"]["body"].strip()
            print("TEXT:", text)

            # ===== СКАРГА =====
            if text.startswith("report_"):
                fname = text.replace("report_", "")

                if fname in pending_reports:
                    send_text(ADMIN_PHONE, f"⚠️ Скарга від {phone}")
                    send_image(ADMIN_PHONE, pending_reports[fname])

                send_text(phone, "Скарга відправлена ✅")

            # ===== GMAIL ПОШУК =====
            else:
                doc = text

                print("SEARCHING GMAIL FOR:", doc)

                files = search_gmail_attachments(doc)

                if not files:
                    send_text(phone, "❌ Вкладень не знайдено")
                else:
                    for f in files:
                        try:
                            media = MediaIoBaseUpload(
                                io.BytesIO(f["data"]),
                                mimetype='application/octet-stream'
                            )

                            file_drive = drive.files().create(
                                body={'name': f["name"], 'parents': [GDRIVE_FOLDER_ID]},
                                media_body=media,
                                fields='id'
                            ).execute()

                            drive.permissions().create(
                                fileId=file_drive['id'],
                                body={'type': 'anyone', 'role': 'reader'}
                            ).execute()

                            url = f"https://drive.google.com/uc?id={file_drive['id']}"

                            send_text(phone, f"📎 {f['name']}: {url}")

                        except Exception as e:
                            print("DRIVE ERROR:", e)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "ok", 200

# ================== BUTTON HANDLER ==================
@app.route("/webhook", methods=["POST"])
def buttons():
    data = request.get_json()

    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")

        if not messages:
            return "ok", 200

        msg = messages[0]
        phone = msg["from"]

        if msg["type"] == "interactive":
            button_id = msg["interactive"]["button_reply"]["id"]

            if button_id.startswith("report_"):
                fname = button_id.replace("report_", "")

                if fname in pending_reports:
                    send_text(ADMIN_PHONE, f"⚠️ Скарга від {phone}")
                    send_image(ADMIN_PHONE, pending_reports[fname])

                send_text(phone, "Скарга відправлена ✅")

    except:
        pass

    return "ok", 200


# ================== RUN ==================
if __name__ == "__main__":
    app.run(port=5000)


# ================== RUN ==================
if __name__ == "__main__":
    app.run(port=5000)

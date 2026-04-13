import io
import base64
import requests
import datetime
import hashlib
import re
import time
import threading
from flask import Flask, request, Response

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАЛАШТУВАННЯ ==================
WHATSAPP_TOKEN = "EAAwJZC7glYnQBRM4PwjO0xZB8h2sjfubxUjpXwMilA5qBTk4pz8v0XinFCxomRWZCcRHGkBngI8fTXjhQjfegFs8GoZCzwpwUymp2I1FQkNj78yw02U0drLOvnOCZCQtHuA2CGHVsZAfH7EZBKFKDYno3XvtZCGKZBVvzaZBLCWIXper7dYDi7cxIR2zJxhusEZBt7ZAQZDZD"
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
sheets = build("sheets", "v4", credentials=Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE))

pending_reports = {}
total_counter = 0

# ================== HELPERS ==================
def normalize_barcode(code):
    if not code:
        return None
    code = re.sub(r'[^0-9]', '', str(code))
    return code if code else None


def search_gmail_attachments(doc):
    query = f"filename:{doc} newer_than:14d"
    res = gmail.users().messages().list(userId="me", q=query).execute()
    messages = res.get("messages", [])

    files = []

    for m in messages:
        msg = gmail.users().messages().get(userId="me", id=m["id"]).execute()
        parts = msg["payload"].get("parts", [])

        for p in parts:
            filename = p.get("filename")
            if filename and doc in filename:
                att_id = p["body"].get("attachmentId")
                if att_id:
                    att = gmail.users().messages().attachments().get(
                        userId="me",
                        messageId=m["id"],
                        id=att_id
                    ).execute()

                    data = base64.urlsafe_b64decode(att["data"])
                    files.append({"name": filename, "data": data})

    return files


def send_text(phone, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    try:
        requests.post(url, headers=headers, json={
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": text}
        })
    except:
        pass


def send_image(phone, image_url):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    try:
        requests.post(url, headers=headers, json={
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "image",
            "image": {"link": image_url}
        })
    except:
        pass


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

    try:
        requests.post(url, headers=headers, json=data)
    except:
        pass


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


# ================== SHEETS SAFE UPDATE ==================
def update_used(row, value):
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"Лист1!D{row}",
            valueInputOption="RAW",
            body={"values": [[value]]}
        ).execute()
    except Exception as e:
        print("SHEETS ERROR:", e)


# ================== WEBHOOK ==================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "error", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    global total_counter

    try:
        data = request.get_json()
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")

        if not messages:
            return "ok", 200

        msg = messages[0]
        phone = msg["from"]

        # ================== IMAGE ==================
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

            try:
                r = requests.post(WEB_APP_URL, json={"image": base64.b64encode(img).decode()})
                raw = r.json().get("barcodes", [])
                barcodes = [normalize_barcode(b) for b in raw if b]
            except:
                barcodes = []

            send_text(phone, "\n".join(barcodes) if barcodes else "❌ Нема штрихкодів")

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)

            pending_reports[fname] = url

            send_image(phone, url)
            send_report_button(phone, fname)
            send_text(phone, "------ ГОТОВО ------")

            try:
                row, user = get_user(phone)
                if row:
                    update_used(row, int(user[3]) + 1)
            except:
                pass

            try:
                total_counter += 1
            except:
                pass

        # ================== TEXT ==================
        elif msg["type"] == "text":

            text = msg["text"]["body"].strip()

            if text.startswith("report_"):
                fname = text.replace("report_", "")

                if fname in pending_reports:
                    send_text(ADMIN_PHONE, f"⚠️ Скарга від {phone}")
                    send_image(ADMIN_PHONE, pending_reports[fname])

                send_text(phone, "Скарга відправлена ✅")

            else:
                files = search_gmail_attachments(text)

                if not files:
                    send_text(phone, "❌ Вкладень не знайдено")
                else:
                    for f in files:
                        media = MediaIoBaseUpload(io.BytesIO(f["data"]), mimetype='application/octet-stream')

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
                        send_text(phone, url)

    except Exception as e:
        print("WEBHOOK ERROR:", e)

    return "ok", 200


# ================== RUN ==================
if __name__ == "__main__":
    app.run(port=5000)

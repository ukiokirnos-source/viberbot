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

# ================== HEADERS ==================
def init_headers():
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A1:E1",
            valueInputOption="RAW",
            body={"values": [["PHONE", "NAME", "DAILY_LIMIT", "USED_TODAY", "TOTAL_USED"]]}
        ).execute()
    except:
        pass

init_headers()

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
    requests.post(url, headers=headers, json={
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text}
    })


def send_image(phone, image_url):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    requests.post(url, headers=headers, json={
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {"link": image_url}
    })


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


# ================== SAFE MEDIA GET (FIX) ==================
def get_media_url(media_id):
    try:
        r = requests.get(
            f"https://graph.facebook.com/v18.0/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        ).json()

        return r.get("url")
    except:
        return None


# ================== SHEETS ==================
def get_user(phone):
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:E"
    ).execute().get("values", [])

    for i, r in enumerate(rows):
        if r and r[0] == phone:
            return i + 1, r
    return None, None


def create_user(phone, name):
    sheets.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:E",
        valueInputOption="RAW",
        body={"values": [[phone, name, 12, 0, 0]]}
    ).execute()


def update_used(row, value):
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row}",
        valueInputOption="RAW",
        body={"values": [[value]]}
    ).execute()


def update_total(val):
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!E2",
        valueInputOption="RAW",
        body={"values": [[val]]}
    ).execute()


# ================== RESET DAILY ==================
def reset_daily():
    while True:
        now = datetime.datetime.now()
        seconds = ((24 - now.hour - 1) * 3600) + ((60 - now.minute - 1) * 60)
        time.sleep(seconds)

        try:
            rows = sheets.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Лист1!D2:D"
            ).execute().get("values", [])

            for i in range(len(rows)):
                sheets.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Лист1!D{i+2}",
                    valueInputOption="RAW",
                    body={"values": [[0]]}
                ).execute()
        except:
            pass

threading.Thread(target=reset_daily, daemon=True).start()


# ================== WEBHOOK ==================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    data = request.get_json()

    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")

        if not messages:
            return "ok", 200

        msg = messages[0]
        phone = msg["from"]

        name = phone
        try:
            name = entry["contacts"][0]["profile"]["name"]
        except:
            pass

        # ================== IMAGE ==================
        if msg["type"] == "image":

            row, user = get_user(phone)

            if not row:
                create_user(phone, name)
                row, user = get_user(phone)

            limit = int(user[2]) if len(user) > 2 else 999
            used = int(user[3]) if len(user) > 3 else 0

            if used >= limit:
                send_text(phone, "🚫 Ліміт вичерпано")
                return "ok", 200

            media_id = msg["image"]["id"]

            media_url = get_media_url(media_id)
            if not media_url:
                print("MEDIA ERROR:", media_id)
                return "ok", 200

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

            send_text(phone, "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено")
            time.sleep(0.3)

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)
            pending_reports[fname] = url

            send_image(phone, url)
            time.sleep(0.3)

            send_report_button(phone, fname)
            time.sleep(0.3)

            send_text(phone, "------ ГОТОВО ------")

            update_used(row, used + 1)

        # ================== TEXT ==================
        elif msg["type"] in ["text", "interactive"]:

            payload = msg["text"]["body"] if msg["type"] == "text" else msg["interactive"]["button_reply"]["id"]

            if payload.startswith("report_"):
                fname = payload.replace("report_", "")

                if fname in pending_reports:
                    send_text(ADMIN_PHONE, f"⚠️ Скарга від {phone}")
                    send_image(ADMIN_PHONE, pending_reports[fname])

                send_text(phone, "Скарга відправлена ✅")

            else:
                files = search_gmail_attachments(payload)

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
                        send_text(phone, f"📎 {f['name']}: {url}")

    except Exception as e:
        print("ERROR:", e)

    return "ok", 200


if __name__ == "__main__":
    app.run(port=5000)

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
from zoneinfo import ZoneInfo

# ================== НАЛАШТУВАННЯ ==================
WHATSAPP_TOKEN = "EAAwJZC7glYnQBRB8Wy8uUb22UsZAUMYYoFEaZCyUR9HduC963ZBEeheqsQhIDGaTbyBVKG2Ks5xMqryQRBEBC1A67FhawW0pkUrFkSRfKl7qhL8p9RrdA6AZAatMXcBM2mlf0n9rpkFTEDWJKI5PZBgW9LVLieea8ZAZBrZCT4epEV9qvhCMdGVAgSIF8ZAbXJqktAZBAZDZD"
PHONE_NUMBER_ID = "989427330931362"
VERIFY_TOKEN = "my_token_123"
ADMIN_PHONE = "380675335947"

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbyqxrVeS8hGFN5o0iysh3YONuB4VniwfGr80QrXITegehy83Xvmx7kcZjUWKrxGG1MhkA/exec"
GMAIL_TOKEN_FILE = "gmail_token.json"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"

SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"

# ================== INIT ==================
app = Flask(__name__)

creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE)

gmail = build("gmail", "v1", credentials=creds)
drive = build("drive", "v3", credentials=creds)
sheets = build("sheets", "v4", credentials=creds)

pending_reports = {}

# антидубль
processed_messages = {}
processed_media = {}


# ================== HEADERS ==================
def init_headers():
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A1:D1",
            valueInputOption="RAW",
            body={
                "values": [[
                    "PHONE",
                    "NAME",
                    "DAILY_LIMIT",
                    "USED_TODAY"
                ]]
            }
        ).execute()

        res = sheets.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!E1"
        ).execute()

        if "values" not in res or not res["values"]:
            sheets.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range="Лист1!E1",
                valueInputOption="RAW",
                body={"values": [[0]]}
            ).execute()

    except Exception as e:
        print("HEADER ERROR:", e)


init_headers()


# ================== RESET DAILY ==================
def reset_daily_usage():
    kyiv_tz = ZoneInfo("Europe/Kyiv")

    while True:
        try:
            now = datetime.datetime.now(kyiv_tz)

            # наступна північ по Києву
            tomorrow = now + datetime.timedelta(days=1)
            midnight = datetime.datetime.combine(
                tomorrow.date(),
                datetime.time.min,
                tzinfo=kyiv_tz
            )

            sleep_seconds = (midnight - now).total_seconds()

            print(f"До reset лишилось: {sleep_seconds:.0f} сек")
            time.sleep(sleep_seconds)

            rows = sheets.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Лист1!D2:D"
            ).execute().get("values", [])

            if rows:
                zeros = [[0] for _ in rows]

                sheets.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Лист1!D2:D{len(rows)+1}",
                    valueInputOption="RAW",
                    body={"values": zeros}
                ).execute()

                print("✅ DAILY LIMIT RESET (Kyiv time)")

        except Exception as e:
            print("RESET ERROR:", e)
            time.sleep(60)


# ================== CLEANUP CACHE ==================
def cleanup_processed():
    while True:
        now = time.time()

        msg_delete = [
            k for k, v in processed_messages.items()
            if now - v > 3600
        ]

        media_delete = [
            k for k, v in processed_media.items()
            if now - v > 3600
        ]

        for k in msg_delete:
            del processed_messages[k]

        for k in media_delete:
            del processed_media[k]

        print("🧹 cache cleaned")
        time.sleep(300)


# ================== HELPERS ==================
def normalize_barcode(code):
    if not code:
        return None

    code = re.sub(r'[^0-9]', '', str(code))
    return code if code else None


def search_gmail_attachments(doc):
    query = f"filename:{doc} newer_than:14d"

    res = gmail.users().messages().list(
        userId="me",
        q=query
    ).execute()

    messages = res.get("messages", [])
    files = []

    for m in messages:
        msg = gmail.users().messages().get(
            userId="me",
            id=m["id"]
        ).execute()

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

                    files.append({
                        "name": filename,
                        "data": data
                    })

    return files


def send_text(phone, text, reply_to=None):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text}
    }

    if reply_to:
        payload["context"] = {"message_id": reply_to}

    requests.post(url, headers=headers, json=payload)

def send_document(phone, file_url, filename):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "document",
        "document": {
            "link": file_url,
            "filename": filename
        }
    }

    requests.post(url, headers=headers, json=payload)
    
def send_image(phone, image_url):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {"link": image_url}
    }

    requests.post(url, headers=headers, json=payload)


def send_report_button(phone, fname):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "Є проблема з фото?"
            },
            "action": {
                "buttons": [{
                    "type": "reply",
                    "reply": {
                        "id": f"report_{fname}",
                        "title": "⚠️ Скарга"
                    }
                }]
            }
        }
    }

    requests.post(url, headers=headers, json=payload)


def upload_photo(bytes_, name):
    media = MediaIoBaseUpload(
        io.BytesIO(bytes_),
        mimetype="image/jpeg"
    )

    file = drive.files().create(
        body={
            "name": name,
            "parents": [GDRIVE_FOLDER_ID]
        },
        media_body=media,
        fields="id"
    ).execute()

    drive.permissions().create(
        fileId=file["id"],
        body={
            "type": "anyone",
            "role": "reader"
        }
    ).execute()

    return f"https://drive.google.com/uc?id={file['id']}"


def increment_global_counter():
    try:
        sheet = sheets.spreadsheets().values()

        res = sheet.get(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!E1"
        ).execute()

        current = 0

        if "values" in res and res["values"]:
            try:
                current = int(res["values"][0][0])
            except:
                current = 0

        current += 1

        sheet.update(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!E1",
            valueInputOption="RAW",
            body={"values": [[current]]}
        ).execute()

    except Exception as e:
        print("TOTAL ERROR:", e)


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


# ================== WEBHOOK ==================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    data = request.get_json()

    if not data:
        return "ok", 200

entry = data["entry"][0]["changes"][0]["value"]

if "messages" not in entry:
    return "ok", 200

    try:
    entry = data["entry"][0]["changes"][0]["value"]
    messages = entry.get("messages")

    if not messages:
        return "ok", 200

    msg = messages[0]

except Exception as e:
    print("ERROR:", e)
    return "ok", 200


        msg = messages[0]
    # ========= IMAGE =========
    if msg["type"] == "image":
        if message_id in processed_messages:
            print("DUPLICATE MESSAGE:", message_id)
            return "ok", 200

        processed_messages[message_id] = time.time()

        phone = msg["from"]

        try:
            name = entry["contacts"][0]["profile"]["name"]
        except:
            name = phone

        # ========= IMAGE =========
        if msg["type"] == "image":

            media_id = msg["image"]["id"]

            # антидубль по фото
            if media_id in processed_media:
                print("DUPLICATE MEDIA:", media_id)
                return "ok", 200

            processed_media[media_id] = time.time()

            row, user = get_user(phone)

            if not row:
                create_user(phone, name)
                row, user = get_user(phone)

            limit = int(user[2]) if len(user) > 2 and str(user[2]).isdigit() else 999
            used = int(user[3]) if len(user) > 3 and str(user[3]).isdigit() else 0

            if used >= limit:
                send_text(phone, "🚫 Ліміт вичерпано")
                return "ok", 200

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
                r = requests.post(
                    WEB_APP_URL,
                    json={"image": base64.b64encode(img).decode()},
                    timeout=20
                )

                data_bc = r.json() if r.ok else {}
                raw = data_bc.get("barcodes", []) or data_bc.get("result", [])

                barcodes = [
                    normalize_barcode(b)
                    for b in raw if b
                ]

            except:
                barcodes = []

            response_text = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"

            send_text(phone, response_text, reply_to=message_id)

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            url = upload_photo(img, fname)

            pending_reports[fname] = url

            send_report_button(phone, fname)

            update_used(row, used + 1)
            increment_global_counter()

        # ========= TEXT =========
        elif msg["type"] in ["text", "interactive"]:

            if msg["type"] == "text":
                payload = msg["text"]["body"]
            else:
                payload = msg["interactive"]["button_reply"]["id"]
            if not payload or len(payload) < 2:
                return "ok", 200

            if payload.startswith("report_"):
                fname = payload.replace("report_", "")

                if fname in pending_reports:
                    send_text(
                        ADMIN_PHONE,
                        f"⚠️ Скарга від {phone}"
                    )
                    send_image(
                        ADMIN_PHONE,
                        pending_reports[fname]
                    )

                send_text(phone, "Скарга відправлена ✅")

            else:
                files = search_gmail_attachments(payload)

                if not files:
                    send_text(phone, "❌ Вкладень не знайдено")
                else:
                    for f in files[:3]:
                        media = MediaIoBaseUpload(
                            io.BytesIO(f["data"]),
                            mimetype="application/octet-stream"
                        )

                        file_drive = drive.files().create(
                            body={
                                "name": f["name"],
                                "parents": [GDRIVE_FOLDER_ID]
                            },
                            media_body=media,
                            fields="id"
                        ).execute()

                        drive.permissions().create(
                            fileId=file_drive["id"],
                            body={
                                "type": "anyone",
                                "role": "reader"
                            }
                        ).execute()

                        url = f"https://drive.google.com/uc?id={file_drive['id']}"

                        send_document(phone, url, f["name"])

    except Exception as e:
        print("ERROR:", e)

    return "ok", 200


if __name__ == "__main__":
    threading.Thread(
        target=reset_daily_usage,
        daemon=True
    ).start()

    threading.Thread(
        target=cleanup_processed,
        daemon=True
    ).start()

    app.run(port=5000) 

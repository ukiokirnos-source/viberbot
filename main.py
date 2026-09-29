import io
import base64
import requests
import time
import threading
from flask import Flask, request

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАЛАШТУВАННЯ ==================
WHATSAPP_TOKEN = "EAAwJZC7glYnQBSj3Fx5ZBKFZAq8e6SZBWUL1n8ez6DPeRJOKiOAnYrW9ZCj8WB6f6y02TNnPuYMAaDsrcO3ERWPv7WtIzTxY4ZBDP9zs3G2affO0sz4FyYNxssvxrp2oRsg1dGK8umAp0KuatZCbM7iCVgFkbisOZABjKc90wCotG0ODk4tZATPOKWvcRwfKP2QZDZD"
PHONE_NUMBER_ID = "989427330931362"
VERIFY_TOKEN = "my_token_123"

GMAIL_TOKEN_FILE = "gmail_token.json"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"

# ================== INIT ==================
app = Flask(__name__)

creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE)

gmail = build("gmail", "v1", credentials=creds)
drive = build("drive", "v3", credentials=creds)

# антидубль
processed_messages = {}


# ================== CLEANUP CACHE ==================
def cleanup_processed():
    while True:
        now = time.time()

        to_delete = [
            k for k, v in processed_messages.items()
            if now - v > 3600
        ]

        for k in to_delete:
            del processed_messages[k]

        time.sleep(300)


# ================== HELPERS ==================
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


def upload_to_drive(data, name):
    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype="application/octet-stream"
    )

    file = drive.files().create(
        body={"name": name, "parents": [GDRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()

    drive.permissions().create(
        fileId=file["id"],
        body={"type": "anyone", "role": "reader"}
    ).execute()

    return f"https://drive.google.com/uc?id={file['id']}"


# ================== WEBHOOK ==================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    # верифікація вебхука Meta
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge", ""), 200
        return "forbidden", 403

    data = request.get_json()

    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")

        if not messages:
            return "ok", 200

        msg = messages[0]
        phone = msg["from"]
        message_id = msg.get("id")

        if msg.get("type") != "text":
            return "ok", 200

        if message_id:
            if message_id in processed_messages:
                return "ok", 200
            processed_messages[message_id] = time.time()

        payload = msg["text"]["body"].strip()

        if not payload:
            return "ok", 200

        files = search_gmail_attachments(payload)

        if not files:
            send_text(phone, "❌ Вкладень не знайдено")
        else:
            for f in files[:3]:
                url = upload_to_drive(f["data"], f["name"])
                send_document(phone, url, f["name"])

    except Exception as e:
        print("ERROR:", e)

    return "ok", 200


if __name__ == "__main__":
    threading.Thread(target=cleanup_processed, daemon=True).start()
    app.run(port=5000)

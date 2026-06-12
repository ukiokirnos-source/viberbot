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
WHATSAPP_TOKEN = "EAAwJZC7glYnQBRB8Wy8uUb22UsZAUMYYoFEaZCyUR9HduC963ZBEeheqsQhIDGaTbyBVKG2Ks5xMqryQRBEBC1A67FhawW0pkUrFkSRfKl7qhL8p9RrdA6AZAatMXcBM2mlf0n9rpkFTEDWJK5PZBgW9LVLieea8ZAZBrZCT4epEV9qvhCMdGVAgSIF8ZAbXJqktAZBAZDZD"
PHONE_NUMBER_ID = "989427330931362"
VERIFY_TOKEN = "my_token_123"
ADMIN_PHONE = "380661153200"

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

processed_messages = {}
processed_media = {}

def init_headers():
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A1:D1",
            valueInputOption="RAW",
            body={"values": [["PHONE","NAME","DAILY_LIMIT","USED_TODAY"]]}
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

def reset_daily_usage():
    kyiv_tz = ZoneInfo("Europe/Kyiv")
    while True:
        try:
            now = datetime.datetime.now(kyiv_tz)
            tomorrow = now + datetime.timedelta(days=1)
            midnight = datetime.datetime.combine(tomorrow.date(), datetime.time.min, tzinfo=kyiv_tz)
            time.sleep((midnight - now).total_seconds())

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

        except Exception as e:
            print("RESET ERROR:", e)
            time.sleep(60)

def cleanup_processed():
    while True:
        now = time.time()
        for k in list(processed_messages):
            if now - processed_messages[k] > 3600:
                del processed_messages[k]
        for k in list(processed_media):
            if now - processed_media[k] > 3600:
                del processed_media[k]
        time.sleep(300)

def normalize_barcode(code):
    return re.sub(r'[^0-9]', '', str(code)) if code else None

def send_text(phone, text, reply_to=None):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","to":phone,"type":"text","text":{"body":text}}
    if reply_to:
        payload["context"] = {"message_id": reply_to}
    requests.post(url, headers=headers, json=payload)

def send_document(phone, file_bytes, filename):
    upload_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {"file": (filename, io.BytesIO(file_bytes))}
    data = {"messaging_product":"whatsapp"}

    r = requests.post(upload_url, headers=headers, files=files, data=data)
    media_id = r.json()["id"]

    msg_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product":"whatsapp",
        "to":phone,
        "type":"document",
        "document":{"id":media_id,"filename":filename}
    }

    requests.post(msg_url, headers=headers, json=payload)

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
        body={"values":[[phone,name,12,0,0]]}
    ).execute()

def update_used(row,value):
    sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row}",
        valueInputOption="RAW",
        body={"values":[[value]]}
    ).execute()

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    data = request.get_json()
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages")
        if not messages:
            return "ok",200

        msg = messages[0]
        message_id = msg["id"]

        if message_id in processed_messages:
            return "ok",200
        processed_messages[message_id]=time.time()

        phone = msg["from"]

        try:
            name = entry["contacts"][0]["profile"]["name"]
        except:
            name = phone

        if msg["type"]=="image":
            media_id = msg["image"]["id"]
            if media_id in processed_media:
                return "ok",200
            processed_media[media_id]=time.time()

            media_resp = requests.get(
                f"https://graph.facebook.com/v18.0/{media_id}",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            ).json()

            media_url = media_resp.get("url")
            img = requests.get(media_url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}).content

            # barcode service
            try:
                r = requests.post(WEB_APP_URL, json={"image": base64.b64encode(img).decode()}, timeout=20)
                data_bc = r.json()
                raw = data_bc.get("barcodes",[]) or data_bc.get("result",[])
                barcodes = [normalize_barcode(b) for b in raw if b]
            except:
                barcodes=[]

            send_text(phone, "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено", reply_to=message_id)

            fname = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            send_document(phone, img, fname)

        elif msg["type"] in ["text","interactive"]:
            payload = msg.get("text",{}).get("body") if msg["type"]=="text" else msg["interactive"]["button_reply"]["id"]

            if payload and payload.startswith("report_"):
                send_text(phone,"Скарга відправлена ✅")
            else:
                send_text(phone,"❌ Вкладень через Drive більше нема, тепер через документ")

    except Exception as e:
        print("ERROR:",e)

    return "ok",200

if __name__=="__main__":
    threading.Thread(target=reset_daily_usage,daemon=True).start()
    threading.Thread(target=cleanup_processed,daemon=True).start()
    app.run(port=5000)

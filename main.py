import os
import io
import sys
import time
import datetime
import re
import threading
import requests
from queue import Queue
from flask import Flask, request, Response

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.rich_media_message import RichMediaMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ==== Налаштування з ENV ====
VIBER_TOKEN = os.environ.get("VIBER_TOKEN")
VISION_API_KEY = os.environ.get("VISION_API_KEY")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GOOGLE_TOKEN_FILE = os.environ.get("GOOGLE_TOKEN_FILE", "token.json")
DAILY_LIMIT_DEFAULT = int(os.environ.get("DAILY_LIMIT_DEFAULT", 8))
ADMIN_ID = os.environ.get("ADMIN_ID")
DELETE_SHEET_INTERVAL = int(os.environ.get("DELETE_SHEET_INTERVAL", 180))  # секунд

app = Flask(__name__)
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# Google API
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, ['https://www.googleapis.com/auth/drive.file','https://www.googleapis.com/auth/spreadsheets'])
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# ==== Черга та змінні ====
task_queue = Queue()
props = {}  # для часу створення листів
pending_reports = {}
processed_message_tokens = set()

# ==== Логи ====
def log_msg(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def send_log_to_admin():
    try:
        with open("log.txt", "r", encoding="utf-8") as f:
            log_content = f.read()
        viber.send_messages(ADMIN_ID, [TextMessage(text=f"📄 Лог:\n{log_content[-1900:]}")])
    except Exception as e:
        print(f"Не вдалося надіслати лог адміну: {e}")

# ==== Google Sheets функції ====
def get_all_users():
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D"
    ).execute()
    return result.get('values', [])

def find_user_row(user_id):
    rows = get_all_users()
    for idx, row in enumerate(rows):
        if len(row) > 0 and row[0] == user_id:
            return idx + 1, row
    return None, None

def add_new_user(user_id, name):
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[user_id, name, DAILY_LIMIT_DEFAULT, 0]]}
    ).execute()

def update_user_counter(row_number, new_count):
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row_number}",
        valueInputOption="RAW",
        body={"values": [[new_count]]}
    ).execute()

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        drive_service.permissions().create(fileId=file_id, body={'type':'anyone','role':'reader'}).execute()
    except Exception as e:
        log_msg(f"Drive permission error: {e}")

# ==== Vision API ====
def extract_barcodes_from_image(img_bytes):
    import base64
    content = base64.b64encode(img_bytes).decode('utf-8')
    payload = {
        "requests":[{"image":{"content":content},"features":[{"type":"TEXT_DETECTION"}]}]
    }
    url = f"https://vision.googleapis.com/v1/images:annotate?key={VISION_API_KEY}"
    try:
        resp = requests.post(url,json=payload)
        text = resp.json()['responses'][0].get('fullTextAnnotation',{}).get('text','')
        return filter_barcodes(text)
    except Exception as e:
        log_msg(f"Vision API error: {e}")
        return []

def filter_barcodes(text):
    clean_text = text.replace("O","0").replace("I","1").replace("L","1")
    raw_matches = re.findall(r"\d{8,20}", clean_text)
    filtered=[]
    for code in raw_matches:
        if code in filtered: continue
        if len(code) not in [8,10,12,13,14,18]: continue
        if len(code) in [8,13] and not is_valid_ean(code): continue
        filtered.append(code)
    return filtered

def is_valid_ean(code):
    digits = [int(d) for d in code]
    if len(digits)==13:
        s=sum(d*(3 if i%2 else 1) for i,d in enumerate(digits[:-1]))
    elif len(digits)==8:
        s=sum(d*(1 if i%2 else 3) for i,d in enumerate(digits[:-1]))
    else: return False
    return (10-(s%10))%10==digits[-1]

# ==== Черга ====
def process_queue_worker():
    while True:
        try:
            user_id, file_bytes, file_name = task_queue.get()
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base = f"photo_{timestamp}"
            file_ext = file_name.split('.')[-1]

            log_msg(f"Починаю обробку фото: {file_name}")

            # Завантаження на Google Drive
            try:
                gfile = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=f'image/{file_ext}')
                f = drive_service.files().create(
                    body={'name': f"{file_base}.{file_ext}", 'parents': [GDRIVE_FOLDER_ID]},
                    media_body=gfile,
                    fields='id'
                ).execute()
                file_id = f['id']
                add_public_permission(file_id)
                public_url = f"https://drive.google.com/uc?id={file_id}"
                log_msg(f"Файл завантажено на Drive, URL={public_url}")
            except Exception as e:
                log_msg(f"Помилка завантаження на Drive: {e}")
                viber.send_messages(user_id, [TextMessage(text=f"❌ Drive upload error: {e}")])
                send_log_to_admin()
                task_queue.task_done()
                continue

            # Vision API
            barcodes = extract_barcodes_from_image(file_bytes)
            log_msg(f"Штрихкоди знайдено: {barcodes if barcodes else 'немає'}")

            # Google Sheets
            try:
                values = [[b] for b in barcodes] if barcodes else [["Штрихкодів не знайдено"]]
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"{file_base}!A1",
                    valueInputOption="RAW",
                    body={"values": values}
                ).execute()
                props[file_base] = time.time()*1000
                log_msg(f"Лист {file_base} оновлено у Google Sheets")
            except Exception as e:
                log_msg(f"Помилка Google Sheets: {e}")

            # Надіслати фото користувачу
            try:
                viber.send_messages(user_id, [PictureMessage(media=public_url, text=file_name)])
                pending_reports[file_base] = public_url
            except Exception as e:
                log_msg(f"Помилка надсилання фото користувачу: {e}")

            # Кнопка скарги
            try:
                rm = {
                    "Type": "rich_media", "ButtonsGroupColumns": 6, "ButtonsGroupRows": 1, "BgColor": "#FFFFFF",
                    "Buttons": [{
                        "Columns": 6, "Rows": 1, "ActionType": "reply", "ActionBody": f"report_{file_base}",
                        "Text": "⚠️ Скарга", "TextSize": "medium", "TextVAlign": "middle", "TextHAlign": "center",
                        "BgColor": "#ff6666", "TextOpacity": 100, "TextColor": "#FFFFFF"
                    }]
                }
                viber.send_messages(user_id, [RichMediaMessage(rich_media=rm)])
            except Exception as e:
                log_msg(f"Помилка надсилання кнопки скарги: {e}")

            # Надіслати текст штрихкодів
            try:
                text_msg = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
                viber.send_messages(user_id, [TextMessage(text=text_msg)])
            except Exception as e:
                log_msg(f"Помилка надсилання тексту штрихкодів: {e}")

            # Відправити лог адміну
            send_log_to_admin()

        except Exception as e:
            log_msg(f"Невідома помилка у воркері: {e}")
        finally:
            task_queue.task_done()

# ==== Видалення старих листів ====
def delete_old_sheets_worker():
    while True:
        ss = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheets = ss.get('sheets',[])
        now = time.time()*1000
        for sheet in sheets:
            name=sheet['properties']['title']
            created=props.get(name)
            if created and now-created>=DELETE_SHEET_INTERVAL*1000:
                try:
                    sheets_service.spreadsheets().batchUpdate(
                        spreadsheetId=SPREADSHEET_ID,
                        body={"requests":[{"deleteSheet":{"sheetId":sheet['properties']['sheetId']}}]}
                    ).execute()
                    props.pop(name)
                except: pass
        time.sleep(60)

# ==== Flask ====
@app.route('/',methods=['POST'])
def incoming():
    viber_request=viber.parse_request(request.get_data())
    if isinstance(viber_request,ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id,[TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.")])
        return Response(status=200)

    message_token=getattr(viber_request,'message_token',None)
    if message_token in processed_message_tokens: return Response(status=200)
    processed_message_tokens.add(message_token)

    if isinstance(viber_request,ViberMessageRequest):
        message=viber_request.message
        user_id=viber_request.sender.id
        user_name=viber_request.sender.name
        text=getattr(message,'text','').strip().lower()

        # Скарга
        if text.startswith("report_"):
            fname=text[len("report_"):]
            if fname in pending_reports:
                photo_url=pending_reports.pop(fname)
                viber.send_messages(ADMIN_ID,[TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"), PictureMessage(media=photo_url,text="Фото користувача")])
                viber.send_messages(user_id,[TextMessage(text="Скарга успішно надіслана адміну ✅")])
            return Response(status=200)

        # Айді
        if text=="айді":
            viber.send_messages(user_id,[TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Користувач
        row_num,row=find_user_row(user_id)
        if not row_num:
            add_new_user(user_id,user_name)
            row_num,row=find_user_row(user_id)

        limit=int(row[2])
        uploaded_today=int(row[3])
        if uploaded_today>=limit:
            viber.send_messages(user_id,[TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return Response(status=200)

        # Фото
        if hasattr(message,'media') and message.media:
            img_data=requests.get(message.media).content
            file_name=f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            update_user_counter(row_num,uploaded_today+1)
            viber.send_messages(user_id,[TextMessage(text=f"📥 Фото '{file_name}' отримано. Оброблюю...")])
            task_queue.put((user_id,img_data,file_name))

    return Response(status=200)

@app.route('/',methods=['GET'])
def ping():
    return "OK",200

# ==== Старт воркерів ====
if "--worker" in sys.argv:
    print("[WORKER] Queue worker started")
    threading.Thread(target=process_queue_worker,daemon=True).start()
    threading.Thread(target=delete_old_sheets_worker,daemon=True).start()
    while True: time.sleep(60)
else:
    port=int(os.environ.get("PORT",5000))
    app.run(host='0.0.0.0',port=port)

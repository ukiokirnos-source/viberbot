import io
import threading
import time
import requests
import datetime
import re
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

# ==== Налаштування ====
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_TOKEN_FILE = "token.json"
VISION_API_KEY = "AIzaSyD1DYOb665j5M1u4EWTbPx461TKDqhi3UI"
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]
DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
DELETE_SHEET_INTERVAL = 180  # секунд (3 хв)

# ==== Flask і Viber ====
app = Flask(__name__)
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# ==== Змінні ====
processed_message_tokens = set()
pending_reports = {}  # file_name: photo_url
task_queue = Queue()
props = {}  # для створених листів

# ==== Google Sheets ====
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
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"[ERROR] Drive permission: {e}")

# ==== Vision API ====
def extract_barcodes_from_image(img_bytes):
    import base64
    base64_img = base64.b64encode(img_bytes).decode('utf-8')
    payload = {
        "requests": [{
            "image": {"content": base64_img},
            "features": [{"type": "TEXT_DETECTION"}]
        }]
    }
    url = f"https://vision.googleapis.com/v1/images:annotate?key={VISION_API_KEY}"
    try:
        resp = requests.post(url, json=payload)
        resp_json = resp.json()
        text = resp_json['responses'][0].get('fullTextAnnotation', {}).get('text', '')
        return filter_barcodes(text)
    except Exception as e:
        print(f"[ERROR] Vision API: {e}")
        return []

def filter_barcodes(text):
    clean_text = text.replace("O", "0").replace("I", "1").replace("L", "1")
    raw_matches = re.findall(r"\d{8,20}", clean_text)
    forbidden_prefixes = ["00","1","436","202","22","403","675","459","311","377","391","2105","451","288","240","442","044","363","971","097","044","44","536","053","82","066","66","29","36","46","38","43","26","39","35","53","30","67","063","63","0674","674","0675","675","319","086","86","095","9508","11","21","050","507","6721","06721","2309","999","249","9798"]
    filtered = []
    for code in raw_matches:
        if code in filtered: continue
        if len(code) not in [8,10,12,13,14,18]: continue
        if (len(code) in [8,13] and not is_valid_ean(code)): continue
        if any(code.startswith(p) for p in forbidden_prefixes): continue
        filtered.append(code)
    return filtered

def is_valid_ean(code):
    digits = [int(d) for d in code]
    if len(digits) == 13:
        s = sum(d* (3 if i%2 else 1) for i,d in enumerate(digits[:-1]))
    elif len(digits) == 8:
        s = sum(d* (1 if i%2 else 3) for i,d in enumerate(digits[:-1]))
    else:
        return False
    checksum = (10 - (s %10))%10
    return checksum == digits[-1]

# ==== Видалення старих листів ====
def delete_old_sheets_worker():
    print("[WORKER] Delete sheets worker started")
    while True:
        ss = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheets = ss.get('sheets', [])
        now = time.time()*1000
        for sheet in sheets:
            name = sheet['properties']['title']
            created = props.get(name)
            if created and now - created >= DELETE_SHEET_INTERVAL*1000:
                try:
                    sheets_service.spreadsheets().batchUpdate(
                        spreadsheetId=SPREADSHEET_ID,
                        body={"requests":[{"deleteSheet":{"sheetId":sheet['properties']['sheetId']}}]}
                    ).execute()
                    props.pop(name)
                    print(f"[WORKER] Deleted old sheet {name}")
                except Exception as e:
                    print(f"[ERROR] Delete sheet: {e}")
        time.sleep(60)

def process_queue_worker():
    while True:
        task = task_queue.get()
        if task is None:
            break
        user_id, file_bytes, file_name = task
        print(f"[QUEUE] Start processing {file_name}")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"photo_{timestamp}"
        file_ext = file_name.split('.')[-1]

        # ==== Завантаження на Google Drive ====
        try:
            gfile = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=f'image/{file_ext}')
            file_metadata = {'name': f"{file_base}.{file_ext}", 'parents':[GDRIVE_FOLDER_ID]}
            f = drive_service.files().create(body=file_metadata, media_body=gfile, fields='id').execute()
            file_id = f['id']
            print(f"[DRIVE] Uploaded {file_name} as {file_id}")
            add_public_permission(file_id)
            public_url = f"https://drive.google.com/uc?id={file_id}"
        except Exception as e:
            print(f"[DRIVE ERROR] {e}")
            viber.send_messages(user_id,[TextMessage(text=f"❌ Drive upload error: {e}")])
            task_queue.task_done()
            continue

        # ==== Отримання штрихкодів через Vision API ====
        try:
            barcodes = extract_barcodes_from_image(file_bytes)
            print(f"[VISION] Found barcodes for {file_name}: {barcodes}")
        except Exception as e:
            print(f"[VISION ERROR] {e}")
            barcodes = []

        # ==== Google Sheets ====
        sheet_name = file_base
        try:
            # Створення або оновлення листа
            values = [[b] for b in barcodes] if barcodes else [["Штрихкодів не знайдено"]]
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values":values}
            ).execute()
            props[sheet_name] = time.time()*1000
            print(f"[SHEET] Updated sheet {sheet_name}")
        except Exception as e:
            print(f"[SHEET ERROR] {e}")

        # ==== Надсилання фото користувачу ====
        try:
            viber.send_messages(user_id, [PictureMessage(media=public_url, text=file_name)])
            print(f"[VIBER] Sent picture {file_name} to user {user_id}")
        except Exception as e:
            print(f"[VIBER ERROR] Picture: {e}")

        # ==== Надсилання кнопки скарги ====
        try:
            rm = {
                "Type": "rich_media",
                "ButtonsGroupColumns": 6,
                "ButtonsGroupRows": 1,
                "BgColor": "#FFFFFF",
                "Buttons":[
                    {"Columns":6,"Rows":1,"ActionType":"reply","ActionBody":f"report_{file_base}","Text":"⚠️ Скарга","TextSize":"medium","TextVAlign":"middle","TextHAlign":"center","BgColor":"#ff6666","TextOpacity":100,"TextColor":"#FFFFFF"}
                ]
            }
            viber.send_messages(user_id,[RichMediaMessage(rich_media=rm)])
            print(f"[VIBER] Sent report button for {file_name}")
        except Exception as e:
            print(f"[VIBER ERROR] Button: {e}")

        # ==== Надсилання тексту штрихкодів ====
        try:
            text_msg = "\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено"
            viber.send_messages(user_id,[TextMessage(text=text_msg)])
            print(f"[VIBER] Sent text barcodes for {file_name}")
        except Exception as e:
            print(f"[VIBER ERROR] Text: {e}")

        task_queue.task_done()
        print(f"[QUEUE] Finished processing {file_name}")

# ==== Flask Routes ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id,[TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")])
        return Response(status=200)

    message_token = getattr(viber_request, 'message_token', None)
    if message_token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(message_token)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        text = getattr(message, 'text', '').strip().lower()

        # Кнопка скарги
        if text.startswith("report_"):
            fname = text[len("report_"):]
            if fname in pending_reports:
                photo_url = pending_reports.pop(fname)
                viber.send_messages(ADMIN_ID, [TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"), PictureMessage(media=photo_url,text="Фото користувача")])
                viber.send_messages(user_id,[TextMessage(text="Скарга успішно надіслана адміну ✅")])
            return Response(status=200)

        # Айді
        if text=="айді":
            viber.send_messages(user_id,[TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Користувач
        row_num,row = find_user_row(user_id)
        if not row_num:
            add_new_user(user_id,user_name)
            row_num,row = find_user_row(user_id)

        limit = int(row[2])
        uploaded_today = int(row[3])
        if uploaded_today >= limit:
            viber.send_messages(user_id,[TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return Response(status=200)

        # Фото
        if hasattr(message,'media') and message.media:
            img_data = requests.get(message.media).content
            file_name = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            update_user_counter(row_num, uploaded_today+1)
            viber.send_messages(user_id,[TextMessage(text=f"📥 Фото '{file_name}' отримано. Оброблюю...")])
            task_queue.put((user_id, img_data, file_name))
            print(f"[QUEUE] Task added for {file_name}")

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

# ==== Старт робітників без __main__ ====
threading.Thread(target=process_queue_worker, daemon=True).start()
threading.Thread(target=delete_old_sheets_worker, daemon=True).start()
print("[INFO] Workers started")

# ==== Запуск Flask (для локального тесту) ====
if __name__=='__main__':
    app.run(host='0.0.0.0', port=5000)

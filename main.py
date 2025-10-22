import io
import threading
import time
import requests
import datetime
import base64
import re
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
API_KEY = "AIzaSyCs1YmkXLNtEc8mfFW7kWt3VNH881Y3mXA"
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]
DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)

# ==== Ініціалізація Viber бота ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google API ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()
pending_reports = {}  # file_name: photo_url

# ==== Таблиця ====
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
        print(f"Помилка при додаванні доступу: {e}")

# ==== Обробка Vision API ====
def extract_barcodes(text):
    clean_text = text.replace('O','0').replace('I','1').replace('L','1')
    forbidden_prefixes = ["00","1","436","202","22","403","675","459","311","377","391",
                          "2105","451","288","240","442","044","363","971","097","044",
                          "44","536","053","82","066","66","29","36","46","38","43",
                          "26","39","35","53","30","67","063","63","0674","674",
                          "0675","675","319","086","86","095","9508","11","21","050",
                          "507","6721","06721","2309","999","249","9798"]
    raw_matches = re.findall(r'\d{8,20}', clean_text)
    filtered = []
    for code in raw_matches:
        if code in filtered: continue
        if len(code) not in [8,10,12,13,14,18]: continue
        if (len(code)==8 or len(code)==13) and not is_valid_ean(code): continue
        if any(code.startswith(p) for p in forbidden_prefixes): continue
        filtered.append(code)
    return filtered

def is_valid_ean(code):
    digits = [int(d) for d in code]
    if len(digits)==13:
        s = sum(d*(1 if i%2==0 else 3) for i,d in enumerate(digits[:-1]))
    elif len(digits)==8:
        s = sum(d*(3 if i%2==0 else 1) for i,d in enumerate(digits[:-1]))
    else:
        return False
    return (10 - s%10)%10 == digits[-1]

def process_image_and_send(user_id, file_base_name, file_name, public_url):
    try:
        # Отримати фото
        response = requests.get(public_url)
        img_data = response.content
        img_base64 = base64.b64encode(img_data).decode('utf-8')

        # Vision API
        payload = {
            "requests": [
                {"image":{"content": img_base64}, "features":[{"type":"TEXT_DETECTION"}]}
            ]
        }
        resp = requests.post(
            f'https://vision.googleapis.com/v1/images:annotate?key={API_KEY}',
            json=payload
        )
        text = resp.json()['responses'][0].get('fullTextAnnotation',{}).get('text','')
        barcodes = extract_barcodes(text)

        # Google Sheet
        sheet_name = file_base_name
        existing_sheets = [s['properties']['title'] for s in sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute().get('sheets',[])]
        if sheet_name in existing_sheets:
            sheet_id = next(s['properties']['sheetId'] for s in sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute().get('sheets',[]) if s['properties']['title']==sheet_name)
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests":[{"deleteSheet":{"sheetId":sheet_id}}]}
            ).execute()
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests":[{"addSheet":{"properties":{"title":sheet_name}}}]}
        ).execute()
        values = [[b] for b in barcodes] if barcodes else [["Штрихкодів не знайдено"]]
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": values}
        ).execute()

        # Надсилання користувачу
        viber.send_messages(user_id, [
            PictureMessage(media=public_url, text=f"Фото: {file_name}"),
            TextMessage(text="\n".join(barcodes) if barcodes else "❌ Штрихкодів не знайдено")
        ])
    except Exception as e:
        viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці фото: {e}")])

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")
        ])
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

        # Скарга
        if text.startswith("report_"):
            file_name = text[len("report_"):]
            if file_name in pending_reports:
                photo_url = pending_reports.pop(file_name)
                try:
                    viber.send_messages(ADMIN_ID, [
                        TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"),
                        PictureMessage(media=photo_url, text="Фото користувача")
                    ])
                    viber.send_messages(user_id, [TextMessage(text="Скарга успішно надіслана адміну ✅")])
                except Exception as e:
                    print(f"Помилка при відправці скарги адміну: {e}")
            return Response(status=200)

        # Айді
        if text == "айді":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        # Користувач
        row_num, row = find_user_row(user_id)
        if not row_num:
            add_new_user(user_id, user_name)
            row_num, row = find_user_row(user_id)
        limit = int(row[2])
        uploaded_today = int(row[3])
        if uploaded_today >= limit:
            viber.send_messages(user_id, [TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return Response(status=200)

        # Фото
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg','jpeg','png']: ext='jpg'
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base_name = f"photo_{timestamp}"
            file_name = f"{file_base_name}.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file = drive_service.files().create(
                    body={'name':file_name,'parents':[GDRIVE_FOLDER_ID]}, media_body=media, fields='id'
                ).execute()
                file_id = file.get('id')
                add_public_permission(file_id)
                update_user_counter(row_num, uploaded_today+1)

                public_url = f"https://drive.google.com/uc?id={file_id}"
                pending_reports[file_name] = public_url

                viber.send_messages(user_id, [TextMessage(text=f"📥 Фото '{file_name}' отримано. Оброблюю...")])

                threading.Thread(
                    target=process_image_and_send,
                    args=(user_id, file_base_name, file_name, public_url),
                    daemon=True
                ).start()
            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

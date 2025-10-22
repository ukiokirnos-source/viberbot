import os
import io
import threading
import time
import datetime
import json
import requests
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
from google.cloud import vision

# ==== Налаштування ====
VIBER_TOKEN = "4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531"
GDRIVE_FOLDER_ID = "1FteobWxkEUxPq1kBhUiP70a4-X0slbWe"
SPREADSHEET_ID = "1W_fiI8FiwDn0sKq0ks7rGcWhXB0HEcHxar1uK4GL1P8"
GOOGLE_USER_KEY = json.loads(os.environ['GOOGLE_SA_JSON'])
GOOGLE_VISION_KEY = json.loads(os.environ['GOOGLE_VISION_JSON'])
DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)

# ==== Viber ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Google API ====
creds = Credentials.from_authorized_user_info(GOOGLE_USER_KEY)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)
vision_client = vision.ImageAnnotatorClient.from_service_account_info(GOOGLE_VISION_KEY)

# ==== Лічильники ====
processed_message_tokens = set()
user_uploads = {}  # user_id: кількість завантажених фото сьогодні
pending_reports = {}  # file_name: URL

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        drive_service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

# ==== Фільтри штрихкодів ====
FORBIDDEN_PREFIXES = ["00", "1", "436", "202", "22", "403", "675", "459", "311", "377", "391", "2105", "451", "288", "240",
                      "442", "044", "363", "971", "097", "044", "44", "536", "053", "82", "066", "66", "29", "36", "46",
                      "38", "43", "26", "39", "35", "53", "30", "67", "063", "63", "0674", "674", "0675", "675", "319",
                      "086", "86", "095", "9508", "11", "21", "050", "507", "6721", "06721", "2309", "999", "249","9798"]

def is_valid_ean(code):
    digits = [int(d) for d in code]
    if len(digits) == 13:
        s = sum(digits[i] * (1 if i % 2 == 0 else 3) for i in range(12))
        checksum = (10 - (s % 10)) % 10
        return checksum == digits[12]
    elif len(digits) == 8:
        s = sum(digits[i] * (3 if i % 2 == 0 else 1) for i in range(7))
        checksum = (10 - (s % 10)) % 10
        return checksum == digits[7]
    return True

def extract_barcodes_from_text(text):
    text = text.replace("O","0").replace("I","1").replace("L","1")
    raw_matches = [s for s in text.split() if s.isdigit() and 8 <= len(s) <= 20]
    filtered = []
    for idx, code in enumerate(raw_matches):
        if code in filtered:
            continue
        if len(code) not in [8,10,12,13,14,18]:
            continue
        if (len(code) in [8,13]) and not is_valid_ean(code):
            continue
        if any(code.startswith(p) for p in FORBIDDEN_PREFIXES):
            continue
        filtered.append(code)
    return filtered

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

def create_new_sheet(sheet_name, barcodes):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_titles = [s.get('properties', {}).get('title') for s in spreadsheet.get('sheets', [])]
        if sheet_name in sheet_titles:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests":[{"deleteSheet":{"sheetId":[s['properties']['sheetId'] for s in spreadsheet['sheets'] if s['properties']['title']==sheet_name][0]}}]}
            ).execute()
        # створюємо новий лист
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": [[b] for b in barcodes] if barcodes else [["Штрихкодів не знайдено"]]}
        ).execute()
    except Exception as e:
        print(f"Помилка при створенні листа {sheet_name}: {e}")

# ==== Delayed send ====
def delayed_send_barcodes(user_id, file_base_name, file_name, img_bytes, row_num, uploaded_today):
    time.sleep(8)
    try:
        # Vision API
        image = vision.Image(content=img_bytes)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        full_text = texts[0].description if texts else ""
        barcodes = extract_barcodes_from_text(full_text)

        # Створення листа
        create_new_sheet(file_base_name, barcodes)

        # Відправка фото
        public_url = f"https://drive.google.com/uc?id={file_id}"
        viber.send_messages(user_id, [PictureMessage(media=public_url, text=f"Фото: {file_name}")])

        # Кнопка
        rich_media_dict = {
            "Type": "rich_media",
            "ButtonsGroupColumns": 6,
            "ButtonsGroupRows": 1,
            "BgColor": "#FFFFFF",
            "Buttons": [{
                "Columns":6,
                "Rows":1,
                "ActionType":"reply",
                "ActionBody":f"report_{file_name}",
                "Text":"⚠️ Скарга",
                "TextSize":"medium",
                "TextVAlign":"middle",
                "TextHAlign":"center",
                "BgColor":"#ff6666",
                "TextOpacity":100,
                "TextColor":"#FFFFFF"
            }]
        }
        pending_reports[file_name] = public_url
        viber.send_messages(user_id, [RichMediaMessage(rich_media=rich_media_dict, min_api_version=2, alt_text="Скарга")])

        # Відправка штрихкодів
        text_to_send = "\n".join(barcodes) if barcodes else f"❌ Штрихкодів у фото '{file_name}' не знайдено."
        viber.send_messages(user_id, [TextMessage(text=text_to_send)])

        # Оновлюємо ліміт
        update_user_counter(row_num, uploaded_today + 1)

    except Exception as e:
        print(f"Помилка у delayed_send_barcodes: {e}")
        viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

# ==== Основний маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())

    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")
        ])
        return Response(status=200)

    token = getattr(viber_request, 'message_token', None)
    if token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(token)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        text = getattr(message, 'text', '').strip().lower()

        # Кнопка "Скарга"
        if text.startswith("report_"):
            file_name = text[len("report_"):]
            if file_name in pending_reports:
                photo_url = pending_reports[file_name]
                viber.send_messages(ADMIN_ID, [TextMessage(text=f"Скарга від {user_name} на файл {file_name}\n{photo_url}")])
                viber.send_messages(user_id, [TextMessage(text="Скаргу відправлено адміну")])
            return Response(status=200)

        # Айді
        if text == "айді":
            viber.send_messages(user_id, [TextMessage(text=f"Твій ID: {user_id}")])
            return Response(status=200)

        # Фото
        if hasattr(message, 'media') and message.media:
            row_num, row_data = find_user_row(user_id)
            if row_num is None:
                add_new_user(user_id, user_name)
                row_num, row_data = find_user_row(user_id)
            uploaded_today = int(row_data[3]) if len(row_data) > 3 else 0
            if uploaded_today >= DAILY_LIMIT_DEFAULT:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Ліміт на сьогодні вичерпано ({DAILY_LIMIT_DEFAULT} фото).")])
                return Response(status=200)
            # Завантаження в гугл диск
            file_bytes = requests.get(message.media).content
            file_name = f"{datetime.datetime.now().strftime('%Y-%m-%d %H-%M-%S')}.jpg"
            file_metadata = {'name': file_name, 'parents':[GDRIVE_FOLDER_ID]}
            media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='image/jpeg')
            uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            file_id = uploaded_file.get('id')
            add_public_permission(file_id)
            file_base_name = file_name.rsplit('.',1)[0]
            # Делаємо delayed
            threading.Thread(target=delayed_send_barcodes, args=(user_id, file_base_name, file_name, file_bytes, row_num, uploaded_today)).start()
            viber.send_messages(user_id, [TextMessage(text=f"✅ Фото прийнято: {file_name}")])
            return Response(status=200)

    return Response(status=200)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)


import io
import threading
import requests
import datetime
import time
import traceback
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
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]
DAILY_LIMIT_DEFAULT = 12
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
SCRIPT_URL = "https://script.google.com/macros/s/AKfycbw3qol9XKHcuR8Z0r72bqfnr60S0dL1IeNSqqsa49YqYujuH00MYK1qEvqEIP-ALF4bnw/exec"

app = Flask(__name__)
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()
processed_images = set()
pending_reports = {}

# ==== Helper Functions ====
def send_viber_text(user_id, text):
    try:
        viber.send_messages(user_id, [TextMessage(text=text)])
    except Exception as e:
        print(f"[ERROR] Помилка при відправці Viber: {e}")

def get_all_users():
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A:D"
        ).execute()
        return result.get('values', [])
    except Exception as e:
        print(f"[ERROR] Помилка при отриманні користувачів: {e}")
        return []

def find_user_row(user_id):
    rows = get_all_users()
    for idx, row in enumerate(rows):
        if row and row[0] == user_id:
            return idx + 1, row
    return None, None

def add_new_user(user_id, name):
    try:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A:D",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[user_id, name, DAILY_LIMIT_DEFAULT, 0]]}
        ).execute()
    except Exception as e:
        print(f"[ERROR] Помилка при додаванні нового користувача: {e}")

def update_user_counter(row_number, new_count):
    try:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"Лист1!D{row_number}",
            valueInputOption="RAW",
            body={"values": [[new_count]]}
        ).execute()
    except Exception as e:
        print(f"[ERROR] Помилка при оновленні лічильника: {e}")

def add_public_permission(file_id):
    try:
        drive_service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
    except Exception as e:
        print(f"[ERROR] Помилка при додаванні доступу: {e}")

def call_script(public_url):
    try:
        resp = requests.post(SCRIPT_URL, json={"imageUrl": public_url}, timeout=40)
        print(f"[SCRIPT] Виклик скрипта завершено, статус: {resp.status_code}")
    except Exception as e:
        print(f"[ERROR] Помилка при виклику скрипта: {e}")
        traceback.print_exc()

# ==== Google Sheets Barcode Functions ====
def find_sheet_name(spreadsheet_id, file_base_name):
    try:
        sheets_meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in sheets_meta['sheets']:
            title = sheet['properties']['title']
            if title == file_base_name:
                return title
        return None
    except Exception as e:
        print(f"[ERROR] Помилка при пошуку листа: {e}")
        return None

def get_barcodes_from_sheet(spreadsheet_id, sheet_name):
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:A"
        ).execute()
        values = result.get('values', [])
        if not values:
            return None
        return "\n".join([row[0] for row in values if row])
    except Exception as e:
        print(f"[ERROR] Помилка при отриманні штрихкодів: {e}")
        return None

# ==== Delayed Processing ====
def delayed_send_with_barcodes(user_id, file_name, file_base_name, public_url):
    try:
        time.sleep(12)  # затримка перед обробкою
        call_script(public_url)

        # 1. Надсилаємо фото
        try:
            viber.send_messages(user_id, [
                PictureMessage(media=public_url, text=f"Фото: {file_name}")
            ])
        except Exception as e:
            print(f"Помилка при надсиланні фото: {e}")

        # 2. Надсилаємо кнопку "Скарга"
        try:
            rich_media_dict = {
                "Type": "rich_media",
                "ButtonsGroupColumns": 6,
                "ButtonsGroupRows": 1,
                "BgColor": "#FFFFFF",
                "Buttons": [
                    {
                        "Columns": 6,
                        "Rows": 1,
                        "ActionType": "reply",
                        "ActionBody": f"report_{file_name}",
                        "Text": "⚠️ Скарга",
                        "TextSize": "medium",
                        "TextVAlign": "middle",
                        "TextHAlign": "center",
                        "BgColor": "#ff6666",
                        "TextOpacity": 100,
                        "TextColor": "#FFFFFF"
                    }
                ]
            }
            pending_reports[file_name] = public_url
            viber.send_messages(user_id, [
                RichMediaMessage(rich_media=rich_media_dict, min_api_version=2, alt_text="Скарга")
            ])
        except Exception as e:
            print(f"Помилка при надсиланні кнопки: {e}")

        # 3. Надсилаємо штрихкоди
        sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
        if not sheet_name:
            barcodes_text = f"❌ Не знайдено листа з назвою '{file_base_name}'"
        else:
            barcodes = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
            barcodes_text = barcodes or f"❌ Штрихкодів у фото '{file_name}' не знайдено."

        try:
            send_viber_text(user_id, barcodes_text)
        except Exception as e:
            print(f"Помилка при надсиланні штрихкодів: {e}")

    except Exception as e:
        print(f"[ERROR] Помилка в delayed_send_with_barcodes: {e}")
        traceback.print_exc()

# ==== Routes ====
@app.route('/', methods=['POST'])
def incoming():
    try:
        viber_request = viber.parse_request(request.get_data())
    except Exception as e:
        print(f"[ERROR] Помилка при парсі запиту: {e}")
        traceback.print_exc()
        return Response(status=500)

    if isinstance(viber_request, ViberConversationStartedRequest):
        send_viber_text(viber_request.user.id,
                        "Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")
        return Response(status=200)

    message_token = getattr(viber_request, 'message_token', None)
    if not message_token or message_token in processed_message_tokens:
        return Response(status=200)
    processed_message_tokens.add(message_token)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        text = getattr(message, 'text', '').strip().lower()

        # Команди
        if text == "айді":
            send_viber_text(user_id, f"Ваш user_id: {user_id}")
            return Response(status=200)

        if text.startswith("report_"):
            file_name = text[len("report_"):]
            if file_name in pending_reports:
                photo_url = pending_reports.pop(file_name)
                try:
                    viber.send_messages(ADMIN_ID, [
                        TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"),
                        PictureMessage(media=photo_url, text="Фото користувача")
                    ])
                    send_viber_text(user_id, "✅ Скаргу відправлено адміну.")
                except Exception as e:
                    print(f"[ERROR] Помилка при надсиланні скарги адміну: {e}")
            return Response(status=200)

        # Облік користувача
        row_num, row = find_user_row(user_id)
        if not row_num:
            add_new_user(user_id, user_name)
            row_num, row = find_user_row(user_id)

        try:
            limit = int(row[2])
            uploaded_today = int(row[3])
        except:
            limit = DAILY_LIMIT_DEFAULT
            uploaded_today = 0

        if uploaded_today >= limit:
            send_viber_text(user_id, f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")
            return Response(status=200)

        # Обробка фото
        if getattr(message, 'media', None):
            image_url = message.media
            if image_url in processed_images:
                return Response(status=200)
            processed_images.add(image_url)

            try:
                ext = image_url.split('.')[-1].split('?')[0].lower()
                if ext not in ['jpg', 'jpeg', 'png']:
                    ext = 'jpg'
                ext_mime = 'jpeg' if ext == 'jpg' else ext

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                file_base_name = f"photo_{timestamp}"
                file_name = f"{file_base_name}.{ext}"

                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext_mime}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                file_id = file.get('id')
                add_public_permission(file_id)
                public_url = f"https://drive.google.com/uc?id={file_id}"

                update_user_counter(row_num, uploaded_today + 1)

                # Виклик функції, яка надсилає фото, кнопку і штрихкоди
                threading.Thread(
                    target=delayed_send_with_barcodes,
                    args=(user_id, file_name, file_base_name, public_url),
                    daemon=True
                ).start()

            except Exception as e:
                send_viber_text(user_id, f"❌ Помилка при обробці: {e}")
                traceback.print_exc()

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

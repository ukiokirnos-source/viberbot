import io
import threading
import time
import requests
import datetime
from flask import Flask, request, Response

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
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

DAILY_LIMIT_DEFAULT = 8
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="

app = Flask(__name__)
viber = Api(BotConfiguration(
    name='ФотоЗагрузBot',
    avatar='https://example.com/avatar.jpg',
    auth_token=VIBER_TOKEN
))

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

processed_message_tokens = set()

# ==== Google Sheets функції ====
def get_all_users():
    try:
        return sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Лист1!A:D"
        ).execute().get('values', [])
    except:
        return []

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

def update_user_limit(row_number, new_limit):
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!C{row_number}",
        valueInputOption="RAW",
        body={"values": [[new_limit]]}
    ).execute()

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Drive permission error: {e}")

# ==== Штрихкоди ====
def find_sheet_name(sheet_id, file_base_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        for sheet in spreadsheet.get('sheets', []):
            title = sheet.get('properties', {}).get('title', '')
            if title == file_base_name:
                return title
        return None
    except:
        return None

def get_barcodes_from_sheet(sheet_id, sheet_name):
    if not sheet_name:
        return "Штрихкодів немає"
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A:A"
        ).execute()
        values = result.get('values', [])
        if not values or (len(values) == 1 and values[0][0] == "[NO_BARCODE]"):
            return "Штрихкодів немає"
        return "\n".join(row[0] for row in values if row)
    except:
        return "Штрихкодів немає"

# ==== Надсилання фото + штрихкод + кнопка ====
def send_photo_with_barcodes(user_id, file_name, file_url, barcodes_text):
    time.sleep(80)  # чекати 80 секунд
    rich_media = {
        "Type": "rich_media",
        "ButtonsGroupColumns": 6,
        "Buttons": [
            {"Columns": 6, "Rows": 3, "ActionType": "open-url", "ActionBody": file_url, "Image": file_url},
            {"Columns": 6, "Rows": 1, "Text": "❌ Помилка", "ActionType": "reply",
             "ActionBody": f"error_report|{user_id}|{file_name}", "TextVAlign": "middle", "TextHAlign": "center", "BgColor": "#FF0000"}
        ]
    }
    text = f"✅ Фото отримано: {file_name}\n🔍 Штрихкоди:\n{barcodes_text}"
    try:
        viber.send_messages(user_id, [RichMediaMessage(rich_media=rich_media)])
        viber.send_messages(user_id, [TextMessage(text=text)])
    except Exception as e:
        print(f"Viber send error: {e}")

# ==== Flask маршрут ====
@app.route('/', methods=['POST'])
def incoming():
    viber_request = viber.parse_request(request.get_data())
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [TextMessage(text="Привіт! Відправ мені накладну.")])
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

        if text == "my_id":
            viber.send_messages(user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return Response(status=200)

        row_num, row = find_user_row(user_id)
        if not row_num:
            add_new_user(user_id, user_name)
            row_num, row = find_user_row(user_id)

        limit = int(row[2])
        uploaded_today = int(row[3])

        if uploaded_today >= limit:
            viber.send_messages(user_id, [TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return Response(status=200)

        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0].lower()
            if ext not in ['jpg', 'jpeg', 'png']:
                ext = 'jpg'

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_base_name = f"photo_{timestamp}"
            file_name = f"{file_base_name}.{ext}"

            try:
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                file_id = file.get('id')
                add_public_permission(file_id)
                update_user_counter(row_num, uploaded_today + 1)

                threading.Thread(
                    target=lambda uid=user_id, fname=file_base_name, fname_full=file_name,
                                 f_url=f"https://drive.google.com/uc?id={file_id}":
                        send_photo_with_barcodes(uid, fname_full, f_url,
                                                 get_barcodes_from_sheet(SPREADSHEET_ID,
                                                                         find_sheet_name(SPREADSHEET_ID,
                                                                                         fname) or "")
                                                 ),
                    daemon=True
                ).start()

                viber.send_messages(user_id, [TextMessage(text=f"✅ Фото отримано: {file_name}. Очікуйте 80 секунд на обробку.")])

            except Exception as e:
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

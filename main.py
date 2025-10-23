import io
import asyncio
import datetime
import ssl
import certifi
from flask import Flask, request, Response
import aiohttp

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages.text_message import TextMessage
from viberbot.api.messages.picture_message import PictureMessage
from viberbot.api.messages.rich_media_message import RichMediaMessage
from viberbot.api.viber_requests import ViberMessageRequest, ViberConversationStartedRequest

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, HttpRequest

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
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1

creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds,
                      requestBuilder=lambda *args, **kwargs: HttpRequest(*args, **kwargs, ssl=ssl_context))
sheets_service = build('sheets', 'v4', credentials=creds,
                       requestBuilder=lambda *args, **kwargs: HttpRequest(*args, **kwargs, ssl=ssl_context))

processed_message_tokens = set()
pending_reports = {}
pending_lock = asyncio.Lock()  # для потокобезпечного доступу

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

def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"Помилка при додаванні доступу: {e}")

def find_sheet_name(sheet_id, file_base_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        for sheet in sheets:
            title = sheet.get('properties', {}).get('title', '')
            if title == file_base_name:
                return title
        return None
    except Exception as e:
        print(f"Помилка при пошуку листа: {e}")
        return None

def get_barcodes_from_sheet(sheet_id, sheet_name):
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A:A"
        ).execute()
        values = result.get('values', [])
        if not values or (len(values) == 1 and values[0][0] == "[NO_BARCODE]"):
            return None
        return "\n".join(row[0] for row in values if row)
    except Exception as e:
        return f"Помилка при зчитуванні штрихкодів: {str(e)}"

# ==== Асинхронне чекання листа ====
async def wait_and_send_barcodes_async(user_id, file_base_name, file_name, public_url, timeout=180, interval=3):
    start_time = asyncio.get_event_loop().time()
    sheet_name = None
    while asyncio.get_event_loop().time() - start_time < timeout:
        try:
            sheet_name = find_sheet_name(SPREADSHEET_ID, file_base_name)
            if sheet_name:
                break
        except Exception as e:
            print(f"SSL або API помилка, повторюємо: {e}")
        await asyncio.sleep(interval)

    # Надсилаємо фото
    try:
        viber.send_messages(user_id, [
            PictureMessage(media=public_url, text=f"Фото: {file_name}")
        ])
    except Exception as e:
        print(f"Помилка при надсиланні фото: {e}")

    # Кнопка "Скарга"
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
        async with pending_lock:
            pending_reports[file_name] = public_url

        viber.send_messages(user_id, [
            RichMediaMessage(rich_media=rich_media_dict, min_api_version=2, alt_text="Скарга")
        ])
    except Exception as e:
        print(f"Помилка при надсиланні кнопки: {e}")

    # Штрихкоди
    if not sheet_name:
        barcodes_text = f"❌ Не знайдено листа з назвою '{file_base_name}' після {timeout} сек."
    else:
        barcodes = get_barcodes_from_sheet(SPREADSHEET_ID, sheet_name)
        barcodes_text = barcodes or f"❌ Штрихкодів у фото '{file_name}' не знайдено."
    try:
        viber.send_messages(user_id, [TextMessage(text=barcodes_text)])
    except Exception as e:
        print(f"Помилка при надсиланні штрихкодів: {e}")

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
            asyncio.create_task(handle_report(user_id, user_name, file_name))
            return Response(status=200)

        if text == "айді":
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
            asyncio.create_task(handle_photo(user_id, user_name, message.media, row_num, uploaded_today))

    return Response(status=200)

# ==== Асинхронні обробники ====
async def handle_report(user_id, user_name, file_name):
    async with pending_lock:
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

async def handle_photo(user_id, user_name, image_url, row_num, uploaded_today):
    ext = image_url.split('.')[-1].split('?')[0]
    if ext.lower() not in ['jpg', 'jpeg', 'png']:
        ext = 'jpg'
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_base_name = f"photo_{timestamp}"
    file_name = f"{file_base_name}.{ext}"

    try:
        # Асинхронне завантаження фото
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                img_data = await resp.read()

        file_stream = io.BytesIO(img_data)
        media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
        file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
        file = await asyncio.to_thread(lambda: drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute())
        file_id = file.get('id')
        await asyncio.to_thread(add_public_permission, file_id)
        await asyncio.to_thread(update_user_counter, row_num, uploaded_today + 1)

        viber.send_messages(user_id, [TextMessage(text=f"📥 Фото '{file_name}' отримано. Чекаю листа...")])
        asyncio.create_task(wait_and_send_barcodes_async(user_id, file_base_name, file_name,
                                                        f"https://drive.google.com/uc?id={file_id}"))
    except Exception as e:
        viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()  # дозволяє asyncio всередині Flask
    app.run(host='0.0.0.0', port=5000)

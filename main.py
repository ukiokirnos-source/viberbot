import os
import io
import uuid
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

# ---- ENV for requests/openssl to use certifi bundle (reduces cert issues) ----
os.environ['SSL_CERT_FILE'] = certifi.where()

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

# ==== Ініціалізація Viber бота ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== SSL контекст для aiohttp (і для googleapiclient, якщо потрібно) ====
ssl_context = ssl.create_default_context(cafile=certifi.where())
ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1

# ==== Ініціалізація Google API (звичний синхронний клієнт) ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
# Не всі версії googleapiclient підтримують ssl в HttpRequest; залишаємо стандартно,
# але встановили SSL_CERT_FILE вище, щоб OpenSSL використав certifi.
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# ---- Глобали ----
processed_message_tokens = set()
pending_reports = {}             # file_name -> public_url
pending_lock = asyncio.Lock()    # lock для pending_reports (асинхр. безпека)

# ==== Допоміжні синхронні функції (їх викликаємо в executor) ====
def get_all_users_sync():
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D"
    ).execute()
    return result.get('values', [])

def find_user_row_sync(user_id):
    rows = get_all_users_sync()
    for idx, row in enumerate(rows):
        if len(row) > 0 and row[0] == user_id:
            return idx + 1, row
    return None, None

def add_new_user_sync(user_id, name):
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="Лист1!A:D",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[user_id, name, DAILY_LIMIT_DEFAULT, 0]]}
    ).execute()

def update_user_counter_sync(row_number, new_count):
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"Лист1!D{row_number}",
        valueInputOption="RAW",
        body={"values": [[new_count]]}
    ).execute()

def add_public_permission_sync(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"[drive permission] Помилка: {e}")

def find_sheet_name_sync(sheet_id, file_base_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        for sheet in sheets:
            title = sheet.get('properties', {}).get('title', '')
            if title == file_base_name:
                return title
        return None
    except Exception as e:
        print(f"[find_sheet_name] Помилка: {e}")
        return None

def get_barcodes_from_sheet_sync(sheet_id, sheet_name):
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

# ==== Асинхронні хендлери (викликаються у фоні) ====
async def wait_and_send_barcodes_async(user_id, file_base_name, file_name, public_url, timeout=180, interval=2):
    """Чекаємо появи листа file_base_name в Google Sheets. Якщо з'явився — відправляємо штрихкоди.
       timeout — сек., interval — опитування (сек.)."""
    start = asyncio.get_event_loop().time()
    sheet_name = None
    while asyncio.get_event_loop().time() - start < timeout:
        try:
            sheet_name = await asyncio.to_thread(find_sheet_name_sync, SPREADSHEET_ID, file_base_name)
            if sheet_name:
                break
        except Exception as e:
            # Логуємо і пробуємо знову; не падаємо
            print(f"[wait_and_send_barcodes] Помилка при пошуку листа: {e}")
        await asyncio.sleep(interval)

    # Надсилаємо фото (не блокуємо loop)
    try:
        await asyncio.to_thread(viber.send_messages, user_id, [
            PictureMessage(media=public_url, text=f"Фото: {file_name}")
        ])
    except Exception as e:
        print(f"[viber send photo] Помилка: {e}")

    # Надсилаємо кнопку "Скарга" — і додаємо в pending_reports під lock
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
    try:
        async with pending_lock:
            pending_reports[file_name] = public_url
        await asyncio.to_thread(viber.send_messages, user_id, [
            RichMediaMessage(rich_media=rich_media_dict, min_api_version=2, alt_text="Скарга")
        ])
    except Exception as e:
        print(f"[viber send button] Помилка: {e}")

    # Отримуємо штрихкоди (синхронно в executor) та відправляємо
    if not sheet_name:
        barcodes_text = f"❌ Не знайдено листа з назвою '{file_base_name}' після {timeout} сек."
    else:
        barcodes = await asyncio.to_thread(get_barcodes_from_sheet_sync, SPREADSHEET_ID, sheet_name)
        barcodes_text = barcodes or f"❌ Штрихкодів у фото '{file_name}' не знайдено."

    try:
        await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text=barcodes_text)])
    except Exception as e:
        print(f"[viber send barcodes] Помилка: {e}")

async def handle_report_async(user_id, user_name, file_name):
    async with pending_lock:
        if file_name in pending_reports:
            photo_url = pending_reports.pop(file_name)
        else:
            photo_url = None

    if photo_url:
        try:
            await asyncio.to_thread(viber.send_messages, ADMIN_ID, [
                TextMessage(text=f"⚠️ Скарга від {user_name} ({user_id})"),
                PictureMessage(media=photo_url, text="Фото користувача")
            ])
            await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text="Скарга успішно надіслана адміну ✅")])
        except Exception as e:
            print(f"[handle_report] Помилка: {e}")
    else:
        # Нічого не знайдено — можна повідомити користувача
        try:
            await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text="❌ Неможливо знайти фото для скарги.")])
        except Exception:
            pass

async def handle_photo_async(user_id, user_name, image_url, row_num, uploaded_today):
    # Унікальне ім'я (uuid + timestamp) щоб уникнути колізій
    ext = image_url.split('.')[-1].split('?')[0]
    if ext.lower() not in ['jpg', 'jpeg', 'png']:
        ext = 'jpg'
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    file_base_name = f"photo_{timestamp}_{unique}"
    file_name = f"{file_base_name}.{ext}"

    try:
        # Асинхронно скачати фото (aiohttp використовує certifi через SSL_CERT_FILE env)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url, ssl=ssl_context) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Помилка при завантаженні зображення: статус {resp.status}")
                img_data = await resp.read()

        file_stream = io.BytesIO(img_data)
        media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
        file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}

        # Завантаження в Google Drive — робимо в thread, бо googleapiclient синхронний
        file = await asyncio.to_thread(lambda: drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute())
        file_id = file.get('id')

        # Дозвіл — теж в thread
        await asyncio.to_thread(add_public_permission_sync, file_id)

        # Оновлюємо лічильник юзера
        await asyncio.to_thread(update_user_counter_sync, row_num, uploaded_today + 1)

        # Відповідь користувачу
        await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text=f"📥 Фото '{file_name}' отримано. Чекаю листа...")])

        # Запускаємо чекання листа у фоні
        asyncio.get_event_loop().create_task(
            wait_and_send_barcodes_async(user_id, file_base_name, file_name, f"https://drive.google.com/uc?id={file_id}")
        )
    except Exception as e:
        print(f"[handle_photo] Помилка: {e}")
        try:
            await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])
        except Exception:
            pass

async def process_viber_request_async(viber_request):
    # Оброблюємо різні типи подій асинхронно
    if isinstance(viber_request, ViberConversationStartedRequest):
        await asyncio.to_thread(viber.send_messages, viber_request.user.id, [
            TextMessage(text="Привіт! Відправ мені накладну зі штрихкодами.\nЩоб дізнатися свій ID, напиши: Айді")
        ])
        return

    message_token = getattr(viber_request, 'message_token', None)
    if message_token in processed_message_tokens:
        return
    processed_message_tokens.add(message_token)

    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        user_name = viber_request.sender.name
        text = getattr(message, 'text', '')
        if text:
            text = text.strip().lower()

        # Обробка скарги
        if isinstance(text, str) and text.startswith("report_"):
            file_name = text[len("report_"):]
            asyncio.get_event_loop().create_task(handle_report_async(user_id, user_name, file_name))
            return

        # Айді
        if text == "айді":
            await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text=f"Ваш user_id: {user_id}")])
            return

        # Беремо або створюємо юзера (виклики google sheets — у thread)
        row = await asyncio.to_thread(find_user_row_sync, user_id)
        row_num, row_data = row if row else (None, None)
        if not row_num:
            await asyncio.to_thread(add_new_user_sync, user_id, user_name)
            row_num, row_data = await asyncio.to_thread(find_user_row_sync, user_id)

        # Захищаємо наявність полів у рядку
        try:
            limit = int(row_data[2]) if row_data and len(row_data) > 2 else DAILY_LIMIT_DEFAULT
            uploaded_today = int(row_data[3]) if row_data and len(row_data) > 3 else 0
        except Exception:
            limit = DAILY_LIMIT_DEFAULT
            uploaded_today = 0

        if uploaded_today >= limit:
            await asyncio.to_thread(viber.send_messages, user_id, [TextMessage(text=f"🚫 Ви досягли ліміту {limit} фото на сьогодні.")])
            return

        # Якщо фото — запускаємо handle_photo_async у фоні
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            asyncio.get_event_loop().create_task(handle_photo_async(user_id, user_name, image_url, row_num, uploaded_today))

# ==== Основний маршрут: відповідаємо 200 і запускаємо обробку в background ====
@app.route('/', methods=['POST'])
def incoming():
    try:
        raw = request.get_data()
        viber_request = viber.parse_request(raw)
    except Exception as e:
        # Негаразд з парсингом — повертаємо 400
        print(f"[incoming parse] Помилка: {e}")
        return Response(status=400)

    # Запускаємо фонову обробку — НЕ чекаємо її
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # Якщо loop не запущений — запускаємо його в окремому треді (звичайно при dev це не буде)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.create_task(process_viber_request_async(viber_request))
    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    # Для dev: дозволяємо nested loop для простого запуску
    import nest_asyncio
    nest_asyncio.apply()
    # Запускаємо Flask. У production – використовуй Gunicorn/uvicorn/fastapi.
    app.run(host='0.0.0.0', port=5000, threaded=True)

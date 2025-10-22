import io
import time
import threading
import requests
import datetime
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
ADMIN_ID = "uJBIST3PYaJLoflfY/9zkQ=="
SCRIPT_URL = "https://script.google.com/macros/s/AKfycbw3qol9XKHcuR8Z0r72bqfnr60S0dL1IeNSqqsa49YqYujuH00MYK1qEvqEIP-ALF4bnw/exec"

app = Flask(__name__)

# ==== Ініціалізація Viber ====
viber = Api(BotConfiguration(
    name='Джексон🤖',
    avatar='https://raw.githubusercontent.com/ukiokirnos-source/viberbot/bea72a7878267cc513cdd87669f9eb6ee0faca50/free-icon-bot-4712106.png',
    auth_token=VIBER_TOKEN
))

# ==== Ініціалізація Google ====
creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)
drive_service = build('drive', 'v3', credentials=creds)
sheets_service = build('sheets', 'v4', credentials=creds)

# множини для унікальності
processed_file_ids = set()       # зберігаємо Drive file_id, щоб не обробляти дублікати
pending_reports = {}             # file_name -> public_url

# ==== Google Drive ====
def add_public_permission(file_id):
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        print(f"[ERROR] Помилка при додаванні доступу: {e}")
        traceback.print_exc()

# ==== Активатор Apps Script (лише тригер) ====
def trigger_apps_script(public_url):
    try:
        print(f"[SCRIPT] Викликаю Apps Script для {public_url}")
        resp = requests.post(SCRIPT_URL, json={"imageUrl": public_url}, timeout=15)
        print(f"[SCRIPT] Статус відповіді: {resp.status_code}")
        # не чекаємо JSON — Apps Script тільки тригерить обробку
        return resp.status_code == 200 or resp.status_code == 202 or resp.status_code == 204
    except Exception as e:
        print(f"[ERROR] Не вдалося викликати Apps Script: {e}")
        traceback.print_exc()
        return False

# ==== Зчитування штрихкодів із Google Sheets ====
def get_barcodes_from_sheet_by_file_name(file_name):
    # очікуємо, що аркуш названий як file_name без розширення
    sheet_name = file_name.rsplit('.', 1)[0]
    try:
        print(f"[SHEETS] Читаю аркуш '{sheet_name}'")
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{sheet_name}'!A:A"
        ).execute()
        values = result.get("values", [])
        barcodes = [row[0] for row in values if row and row[0]]
        print(f"[SHEETS] Отримано {len(barcodes)} рядків")
        return barcodes
    except Exception as e:
        print(f"[ERROR] Помилка читання аркуша '{sheet_name}': {e}")
        traceback.print_exc()
        return []

# ==== Відправка результатів користувачу ====
def send_results_photo_and_barcodes(user_id, file_name, public_url, barcodes):
    try:
        # Надсилаємо фото + штрихкоди (штрихкоди без зайвих слів)
        texts = []
        if barcodes:
            texts.append("\n".join(barcodes))
        else:
            texts.append("❌ Штрихкодів не знайдено.")
        viber.send_messages(user_id, [
            PictureMessage(media=public_url),
            TextMessage(text=texts[0])
        ])
    except Exception as e:
        print(f"[ERROR] Помилка при відправці фото/штрихкодів: {e}")
        traceback.print_exc()

    # Кнопка "Скарга"
    try:
        rich_media = {
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
                    "TextColor": "#FFFFFF"
                }
            ]
        }
        pending_reports[file_name] = public_url
        viber.send_messages(user_id, [
            RichMediaMessage(rich_media=rich_media, min_api_version=2)
        ])
    except Exception as e:
        print(f"[ERROR] Помилка при створенні кнопки: {e}")
        traceback.print_exc()

# ==== Основна логіка обробки після аплоаду ====
def trigger_and_read_then_send(user_id, file_id, file_name, public_url):
    try:
        # Якщо file_id вже оброблявся — нічого не робимо
        if file_id in processed_file_ids:
            print(f"[SKIP] file_id {file_id} вже оброблено.")
            return

        # 1) тригеримо Apps Script (щоб скрипт створив лист у таблиці)
        ok = trigger_apps_script(public_url)
        if not ok:
            print("[WARN] Apps Script не відповів успішно на тригер — все одно чекатиму і спробую прочитати таблицю.")

        # 2) чекати поки скрипт запише лист — кілька спроб
        attempts = 0
        max_attempts = 4
        wait_between = 5  # секунд
        barcodes = []
        while attempts < max_attempts:
            attempts += 1
            print(f"[WAIT] Спроба {attempts}/{max_attempts} — чекаю {wait_between} сек перед читанням...")
            time.sleep(wait_between)
            barcodes = get_barcodes_from_sheet_by_file_name(file_name)
            if barcodes:
                print(f"[OK] Штрихкоди знайдені на спробі {attempts}")
                break
        # 3) відправити результат (навіть якщо пусто)
        send_results_photo_and_barcodes(user_id, file_name, public_url, barcodes)

        # 4) відмітити file_id як оброблений (щоб не робити дубль)
        processed_file_ids.add(file_id)
        print(f"[DONE] Помітка file_id {file_id} як оброблений.")

    except Exception as e:
        print(f"[ERROR] Помилка у trigger_and_read_then_send: {e}")
        traceback.print_exc()
        try:
            viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])
        except:
            pass

# ==== HTTP маршрут бота ====
@app.route('/', methods=['POST'])
def incoming():
    try:
        viber_request = viber.parse_request(request.get_data())
    except Exception as e:
        print(f"[ERROR] Не вдалося розпарсити Viber запит: {e}")
        traceback.print_exc()
        return Response(status=500)

    # при старті розмови
    if isinstance(viber_request, ViberConversationStartedRequest):
        viber.send_messages(viber_request.user.id, [
            TextMessage(text="Привіт! Надішли мені фото накладної — я знайду штрихкоди.")
        ])
        return Response(status=200)

    # обробка повідомлення
    if isinstance(viber_request, ViberMessageRequest):
        message = viber_request.message
        user_id = viber_request.sender.id
        text = getattr(message, 'text', '').strip().lower()

        # обробка скарги
        if text.startswith("report_"):
            file_name = text.replace("report_", "")
            if file_name in pending_reports:
                photo_url = pending_reports.pop(file_name)
                viber.send_messages(ADMIN_ID, [
                    TextMessage(text=f"⚠️ Скарга від користувача: {user_id}"),
                    PictureMessage(media=photo_url)
                ])
                viber.send_messages(user_id, [TextMessage(text="✅ Скаргу відправлено адміну.")])
            return Response(status=200)

        # обробка фото
        if hasattr(message, 'media') and message.media:
            image_url = message.media
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png']:
                ext = 'jpg'
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"photo_{timestamp}.{ext}"

            try:
                # Завантажуємо фото на диск
                img_data = requests.get(image_url).content
                file_stream = io.BytesIO(img_data)
                media = MediaIoBaseUpload(file_stream, mimetype=f'image/{ext}')
                file_metadata = {'name': file_name, 'parents': [GDRIVE_FOLDER_ID]}
                file = drive_service.files().create(
                    body=file_metadata, media_body=media, fields='id'
                ).execute()

                file_id = file.get('id')
                print(f"[DRIVE] Фото завантажено: {file_id}")

                # якщо вже обробляли цей file_id — пропускаємо
                if file_id in processed_file_ids:
                    print(f"[SKIP] file_id {file_id} вже оброблявся — пропуск.")
                    return Response(status=200)

                add_public_permission(file_id)
                public_url = f"https://drive.google.com/uc?id={file_id}"

                # Перше повідомлення — тільки "Фото отримано: {file_name}"
                viber.send_messages(user_id, [
                    TextMessage(text=f"Фото отримано: {file_name}")
                ])

                # Запускаємо фоновий потік, який активує скрипт, чекає та відправить результати
                threading.Thread(target=trigger_and_read_then_send, args=(user_id, file_id, file_name, public_url), daemon=True).start()

            except Exception as e:
                print(f"[ERROR] Помилка при обробці фото: {e}")
                traceback.print_exc()
                viber.send_messages(user_id, [TextMessage(text=f"❌ Помилка при обробці: {e}")])

    return Response(status=200)

@app.route('/', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    print("[START] Бот запущено на порту 5000")
    app.run(host='0.0.0.0', port=5000)

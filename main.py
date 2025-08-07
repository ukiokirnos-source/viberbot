import os
import requests
import json
from flask import Flask, request, redirect, url_for, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from google.auth.transport.requests import Request

# Для роботи OAuth в локальному середовищі, якщо потрібно:
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from viberbot import Api
from viberbot.api.bot_configuration import BotConfiguration
from viberbot.api.messages import PictureMessage
from viberbot.api.viber_requests import ViberMessageRequest

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "change_this_secret")

# Google OAuth конфігурація
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRETS_FILE = "client_secret.json"

# Viber токен
VIBER_AUTH_TOKEN = os.environ.get("VIBER_AUTH_TOKEN", "твій_токен_тут")

viber = Api(BotConfiguration(
    name="ViberUploaderBot",
    avatar="",
    auth_token=VIBER_AUTH_TOKEN
))

UPLOAD_FOLDER = "./uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_credentials():
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open("token.json", "w") as token:
                token.write(creds.to_json())
        return creds
    return None

def get_drive_service():
    creds = get_credentials()
    if creds:
        return build("drive", "v3", credentials=creds)
    return None

def get_redirect_uri():
    try:
        with open(CLIENT_SECRETS_FILE, 'r') as f:
            client_config = json.load(f)
        redirect_uris = client_config['web']['redirect_uris']

        current_host = request.host if request else None
        if current_host:
            https_uri = f"https://{current_host}/oauth2callback"
            if https_uri in redirect_uris:
                return https_uri

            http_uri = f"http://{current_host}/oauth2callback"
            if http_uri in redirect_uris:
                return http_uri

        return redirect_uris[0] if redirect_uris else None
    except Exception as e:
        print(f"Error reading redirect URI: {e}")
        return None

@app.route("/")
def home():
    creds = get_credentials()
    if creds:
        return '✅ Google Drive Authorized | Viber bot is running'
    else:
        # Для Render гарантовано https
        auth_url = url_for("authorize", _external=True, _scheme='https')
        return f'<a href="{auth_url}">Authorize Google Drive</a>'

@app.route("/authorize")
def authorize():
    redirect_uri = get_redirect_uri()
    if not redirect_uri:
        return "Error: No redirect URI configured", 500

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    session["state"] = state
    session["redirect_uri"] = redirect_uri
    return redirect(authorization_url)

@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("state")
    redirect_uri = session.get("redirect_uri")

    if not redirect_uri:
        redirect_uri = get_redirect_uri()

    if not redirect_uri:
        return "Error: No redirect URI found", 500

    try:
        auth_response_url = request.url

        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            state=state,
            redirect_uri=redirect_uri
        )
        flow.fetch_token(authorization_response=auth_response_url)
        creds = flow.credentials
        with open("token.json", "w") as token:
            token.write(creds.to_json())
        return redirect(url_for("home"))
    except Exception as e:
        return f"OAuth error: {str(e)}", 500

@app.route("/viber/webhook", methods=["POST"])
def viber_webhook():
    try:
        viber_request = viber.parse_request(request.get_data())
        if isinstance(viber_request, ViberMessageRequest):
            msg = viber_request.message
            if msg and hasattr(msg, 'type') and msg.type == "picture":
                if not hasattr(msg, 'media') or not msg.media:
                    return "No media in message", 400
                image_url = msg.media
                filename = f"photo_{viber_request.timestamp}.jpg"
                filepath = os.path.join(UPLOAD_FOLDER, filename)

                # Збереження локально
                try:
                    r = requests.get(image_url)
                    r.raise_for_status()
                    with open(filepath, "wb") as f:
                        f.write(r.content)
                except Exception as e:
                    print(f"Error downloading image: {e}")
                    return "Error", 500

                # Завантаження в Google Drive
                drive_service = get_drive_service()
                if drive_service:
                    try:
                        file_metadata = {"name": filename}
                        media = MediaFileUpload(filepath, mimetype="image/jpeg")
                        uploaded_file = drive_service.files().create(
                            body=file_metadata,
                            media_body=media,
                            fields="id"
                        ).execute()

                        file_id = uploaded_file.get('id') if uploaded_file else 'unknown'
                        print(f"Uploaded file ID: {file_id}")

                        viber.send_messages(viber_request.sender.id, [
                            PictureMessage(text="Фото збережено в Google Drive", media=image_url)
                        ])
                    except Exception as e:
                        print(f"Error uploading to Google Drive: {e}")
                        viber.send_messages(viber_request.sender.id, [
                            PictureMessage(text="Фото збережено локально", media=image_url)
                        ])
                else:
                    print("Not authorized with Google Drive - saved locally")
                    viber.send_messages(viber_request.sender.id, [
                        PictureMessage(text="Фото збережено локально", media=image_url)
                    ])
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return "Error", 500
    return "OK"

@app.route('/set_webhook')
def set_webhook():
    try:
        webhook_url = url_for('viber_webhook', _external=True, _scheme='https')
        result = viber.set_webhook(webhook_url)
        return f"Webhook set: {result}"
    except Exception as e:
        return f"Error setting webhook: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

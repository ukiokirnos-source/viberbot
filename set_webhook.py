import requests

bot_token = '4fdbb2493ae7ddc2-cd8869c327e2c592-60fd2dddaa295531'
webhook_url = 'https://viberbot-kixl.onrender.com/webhook'

headers = {
    'X-Viber-Auth-Token': bot_token,
    'Content-Type': 'application/json'
}

data = {
    'url': webhook_url,
    'event_types': [
        'message',
        'delivered',
        'seen',
        'subscribed',
        'unsubscribed',
        'conversation_started'
    ]
}

response = requests.post('https://chatapi.viber.com/pa/set_webhook', headers=headers, json=data)

print("Status:", response.status_code)
print("Response:", response.json())

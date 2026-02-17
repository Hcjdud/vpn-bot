from flask import Flask, request, jsonify
import requests
import sqlite3
import json
import os
from datetime import datetime, timedelta

app = Flask(__name__)

BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = "/tmp/vpn_bot.db"

def init_db():
    """Создание таблиц"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            subscribe_until TEXT,
            trial_used INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            selected_server TEXT DEFAULT 'netherlands'
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ База данных создана")

def send_message(chat_id, text, keyboard=None):
    """Отправка сообщения"""
    url = f"{BASE_URL}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Ошибка отправки: {e}")

@app.route('/', methods=['GET'])
def home():
    return "✅ VPN Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Точка входа для Telegram"""
    try:
        update = request.get_json()
        print(f"📩 Получен запрос: {update}")
        
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            
            if text == "/start":
                keyboard = {
                    "inline_keyboard": [
                        [{"text": "🛡️ Получить доступ", "callback_data": "get_access"}],
                        [{"text": "🌍 Выбрать сервер", "callback_data": "select_server"}],
                        [{"text": "👤 Профиль", "callback_data": "profile"}]
                    ]
                }
                send_message(chat_id, "🌟 Привет! Я VPN бот\n\nВыберите действие:", keyboard)
        
        return "OK", 200
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return "Error", 500

if __name__ == "__main__":
    print("🚀 Запуск бота...")
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

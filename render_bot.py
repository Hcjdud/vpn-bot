#!/usr/bin/env python3
"""
VPN БОТ ДЛЯ RENDER.COM
Адаптирован для вебхуков и keep-alive
"""

import os
import sys
import json
import time
import sqlite3
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests

# ==================== НАСТРОЙКИ ====================

BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
ADMIN_IDS = [8443743937]
REQUIRED_CHANNEL = "@numberbor"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = "/tmp/vpn_bot.db"  # Render использует /tmp для временных файлов!

# Flask приложение для вебхуков
app = Flask(__name__)

# 🌍 Серверы
SERVERS = {
    "netherlands": {"name": "🇳🇱 Нидерланды", "flag": "🇳🇱", "city": "Амстердам", "load": 32, "ping": 45},
    "usa": {"name": "🇺🇸 США", "flag": "🇺🇸", "city": "Нью-Йорк", "load": 45, "ping": 120},
    "germany": {"name": "🇩🇪 Германия", "flag": "🇩🇪", "city": "Франкфурт", "load": 28, "ping": 55}
}

# 📦 Тарифы
PLANS = {
    "1month": {"name": "🌱 1 месяц", "days": 30, "price": 299},
    "3month": {"name": "🌿 3 месяца", "days": 90, "price": 699},
    "6month": {"name": "🌳 6 месяцев", "days": 180, "price": 1199},
    "12month": {"name": "🏝️ 12 месяцев", "days": 365, "price": 1999}
}

# ==================== БАЗА ДАННЫХ ====================

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
    print("✅ База данных готова")

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return dict(user) if user else None

def add_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
        (user_id, username, first_name)
    )
    conn.commit()
    conn.close()

def give_subscription(user_id, days):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    new_date = datetime.now() + timedelta(days=days)
    c.execute(
        "UPDATE users SET subscribe_until = ? WHERE user_id = ?",
        (new_date.isoformat(), user_id)
    )
    conn.commit()
    conn.close()
    return new_date

def check_subscription(user_id):
    """Проверка подписки на канал"""
    url = f"{BASE_URL}/getChatMember"
    params = {"chat_id": REQUIRED_CHANNEL, "user_id": user_id}
    try:
        r = requests.get(url, params=params)
        data = r.json()
        if data.get("ok"):
            status = data["result"].get("status")
            return status in ["creator", "administrator", "member"], []
    except:
        pass
    return False, [REQUIRED_CHANNEL]

# ==================== КЛАВИАТУРЫ ====================

def main_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🛡️ Получить доступ", "callback_data": "get_access"}],
            [{"text": "🌍 Выбрать сервер", "callback_data": "select_server"}],
            [{"text": "👤 Профиль", "callback_data": "profile"}],
            [{"text": "📞 Поддержка", "callback_data": "support"}]
        ]
    }

def servers_keyboard():
    kb = []
    for sid, server in SERVERS.items():
        btn_text = f"{server['flag']} {server['name']} • {server['load']}% • {server['ping']}ms"
        kb.append([{"text": btn_text, "callback_data": f"server_{sid}"}])
    kb.append([{"text": "◀️ Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": kb}

def plans_keyboard():
    kb = []
    for pid, plan in PLANS.items():
        kb.append([{
            "text": f"{plan['name']} — {plan['price']}₽",
            "callback_data": f"buy_{pid}"
        }])
    kb.append([{"text": "◀️ Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": kb}

def sub_keyboard():
    return {
        "inline_keyboard": [
            [{"text": f"📢 Подписаться {REQUIRED_CHANNEL}", 
              "url": f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}"}],
            [{"text": "✅ Я подписался", "callback_data": "check_sub"}]
        ]
    }

def back_keyboard():
    return {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "back_main"}]]}

# ==================== ОТПРАВКА ====================

def send_msg(chat_id, text, keyboard=None):
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
    except:
        pass

def edit_msg(chat_id, msg_id, text, keyboard=None):
    url = f"{BASE_URL}/editMessageText"
    data = {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    try:
        requests.post(url, data=data)
    except:
        pass

def answer_cb(callback_id, text=""):
    url = f"{BASE_URL}/answerCallbackQuery"
    data = {"callback_query_id": callback_id, "text": text}
    try:
        requests.post(url, data=data)
    except:
        pass

# ==================== ОБРАБОТКА ====================

def handle_start(chat_id, user_id, username, first_name):
    add_user(user_id, username, first_name)
    
    subscribed, _ = check_subscription(user_id)
    if not subscribed:
        send_msg(chat_id, "🔐 Подпишитесь на канал:", sub_keyboard())
        return
    
    welcome = f"🌟 <b>Добро пожаловать, {first_name}!</b>\n\n🔒 Быстрый VPN"
    send_msg(chat_id, welcome, main_keyboard())

def handle_callback(data, chat_id, msg_id, user_id, cb_id):
    # Проверка подписки
    if data != "check_sub":
        subscribed, _ = check_subscription(user_id)
        if not subscribed:
            edit_msg(chat_id, msg_id, "🔐 Подпишитесь:", sub_keyboard())
            answer_cb(cb_id)
            return
    
    if data == "check_sub":
        subscribed, _ = check_subscription(user_id)
        if subscribed:
            edit_msg(chat_id, msg_id, "🌟 Главное меню:", main_keyboard())
            answer_cb(cb_id, "✅ Подписка подтверждена!")
        else:
            edit_msg(chat_id, msg_id, "❌ Вы не подписались", sub_keyboard())
            answer_cb(cb_id)
    
    elif data == "back_main":
        edit_msg(chat_id, msg_id, "🏠 Главное меню:", main_keyboard())
        answer_cb(cb_id)
    
    elif data == "get_access":
        edit_msg(chat_id, msg_id, "📦 Выберите тариф:", plans_keyboard())
        answer_cb(cb_id)
    
    elif data == "select_server":
        edit_msg(chat_id, msg_id, "🌍 Выберите сервер:", servers_keyboard())
        answer_cb(cb_id)
    
    elif data.startswith("server_"):
        server_id = data.replace("server_", "")
        server = SERVERS[server_id]
        edit_msg(chat_id, msg_id, f"✅ Выбран: {server['name']}", main_keyboard())
        answer_cb(cb_id, f"✅ {server['name']}")
    
    elif data == "profile":
        user = get_user(user_id)
        if user and user.get("subscribe_until"):
            end = datetime.fromisoformat(user["subscribe_until"])
            days = (end - datetime.now()).days
            status = "✅ Активна" if days > 0 else "❌ Истекла"
        else:
            days = 0
            status = "❌ Нет подписки"
        
        text = f"👤 <b>Профиль</b>\n\n📊 Статус: {status}\n⏱ Осталось: {max(0, days)} дней"
        edit_msg(chat_id, msg_id, text, back_keyboard())
        answer_cb(cb_id)
    
    elif data == "support":
        edit_msg(chat_id, msg_id, "📞 @vpn_support_bot", back_keyboard())
        answer_cb(cb_id)

# ==================== ВЕБХУК ====================

@app.route('/', methods=['GET'])
def home():
    return "✅ VPN Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Точка входа для Telegram"""
    update = request.get_json()
    
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        username = msg["from"].get("username", "")
        first_name = msg["from"].get("first_name", "")
        text = msg.get("text", "")
        
        if text == "/start":
            handle_start(chat_id, user_id, username, first_name)
    
    elif "callback_query" in update:
        cb = update["callback_query"]
        handle_callback(
            cb["data"],
            cb["message"]["chat"]["id"],
            cb["message"]["message_id"],
            cb["from"]["id"],
            cb["id"]
        )
    
    return "OK", 200

# ==================== ЗАПУСК ====================

def keep_alive():
    """Держим бота активным (каждые 10 минут)"""
    while True:
        time.sleep(600)
        try:
            # Пингуем самого себя
            requests.get(f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/")
        except:
            pass

if __name__ == "__main__":
    print("=" * 50)
    print("🌟 VPN БОТ ДЛЯ RENDER")
    print("=" * 50)
    
    init_db()
    
    # Запускаем keep-alive в фоне
    threading.Thread(target=keep_alive, daemon=True).start()
    
    # Получаем порт от Render
    port = int(os.environ.get("PORT", 5000))
    
    # Устанавливаем вебхук
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
    requests.get(f"{BASE_URL}/setWebhook", params={"url": webhook_url})
    
    print(f"✅ Вебхук: {webhook_url}")
    print("✅ Бот запущен!")
    
    # Запускаем Flask сервер
    app.run(host="0.0.0.0", port=port)

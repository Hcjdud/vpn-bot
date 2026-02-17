#!/usr/bin/env python3
"""
🌟 VPN БОТ - ПОЛНАЯ ВЕРСИЯ ДЛЯ RENDER 24/7
Все функции: серверы, подписки, промокоды, админка
"""

from flask import Flask, request, jsonify
import requests
import sqlite3
import json
import os
import sys
import time
import random
import string
import logging
import traceback
from datetime import datetime, timedelta

# ==================== НАСТРОЙКИ ====================

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 🔑 Токен бота
BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
ADMIN_IDS = [8443743937]  # Ваш ID

# 📢 Обязательный канал
REQUIRED_CHANNEL = "@numberbor"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = "/tmp/vpn_bot.db"  # Render использует /tmp

# 🌍 Серверы
SERVERS = {
    "netherlands": {"name": "🇳🇱 Нидерланды", "flag": "🇳🇱", "city": "Амстердам", "load": 32, "ping": 45},
    "usa": {"name": "🇺🇸 США", "flag": "🇺🇸", "city": "Нью-Йорк", "load": 45, "ping": 120},
    "germany": {"name": "🇩🇪 Германия", "flag": "🇩🇪", "city": "Франкфурт", "load": 28, "ping": 55},
    "uk": {"name": "🇬🇧 Великобритания", "flag": "🇬🇧", "city": "Лондон", "load": 38, "ping": 65},
    "singapore": {"name": "🇸🇬 Сингапур", "flag": "🇸🇬", "city": "Сингапур", "load": 22, "ping": 150},
    "japan": {"name": "🇯🇵 Япония", "flag": "🇯🇵", "city": "Токио", "load": 19, "ping": 180}
}

# 📦 Тарифы
PLANS = {
    "1month": {"name": "🌱 1 месяц", "days": 30, "price": 299, "old_price": 499, "popular": False},
    "3month": {"name": "🌿 3 месяца", "days": 90, "price": 699, "old_price": 1197, "popular": True},
    "6month": {"name": "🌳 6 месяцев", "days": 180, "price": 1199, "old_price": 2394, "popular": False},
    "12month": {"name": "🏝️ 12 месяцев", "days": 365, "price": 1999, "old_price": 4788, "popular": False}
}

# 🔌 Протоколы
PROTOCOLS = ["OpenVPN", "WireGuard", "IKEv2"]
TRIAL_DAYS = 3

# ==================== БАЗА ДАННЫХ ====================

def init_db():
    """Создание всех таблиц"""
    try:
        logger.info("Инициализация базы данных...")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # 👤 Пользователи
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscribe_until TEXT,
                trial_used INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                selected_server TEXT DEFAULT 'netherlands',
                selected_protocol TEXT DEFAULT 'OpenVPN',
                config_sent INTEGER DEFAULT 0,
                last_msg_id INTEGER,
                reg_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 🎫 Промокоды
        c.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                days INTEGER,
                uses_left INTEGER,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 📝 Использованные промокоды
        c.execute('''
            CREATE TABLE IF NOT EXISTS used_promos (
                user_id INTEGER,
                code TEXT,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных создана")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при создании БД: {e}")
        traceback.print_exc()
        return False

# ==================== РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ====================

def get_user(user_id):
    """Получить данные пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        return dict(user) if user else None
    except Exception as e:
        logger.error(f"Ошибка get_user: {e}")
        return None

def add_user(user_id, username, first_name):
    """Добавить нового пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка add_user: {e}")
        return False

def give_subscription(user_id, days, admin_give=False):
    """Выдать подписку"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        user = get_user(user_id)
        if user and user.get("subscribe_until") and not admin_give:
            try:
                old_date = datetime.fromisoformat(user["subscribe_until"])
                new_date = old_date + timedelta(days=days)
            except:
                new_date = datetime.now() + timedelta(days=days)
        else:
            new_date = datetime.now() + timedelta(days=days)
        
        c.execute(
            "UPDATE users SET subscribe_until = ? WHERE user_id = ?",
            (new_date.isoformat(), user_id)
        )
        conn.commit()
        conn.close()
        return new_date
    except Exception as e:
        logger.error(f"Ошибка give_subscription: {e}")
        return None

def check_subscription(user_id):
    """Проверить подписку на канал"""
    try:
        channel_id = get_channel_id(REQUIRED_CHANNEL)
        if not channel_id:
            return False, [REQUIRED_CHANNEL]
        
        url = f"{BASE_URL}/getChatMember"
        params = {"chat_id": channel_id, "user_id": user_id}
        r = requests.get(url, params=params)
        data = r.json()
        
        if data.get("ok"):
            status = data["result"].get("status")
            if status in ["creator", "administrator", "member"]:
                return True, []
        
        return False, [REQUIRED_CHANNEL]
    except Exception as e:
        logger.error(f"Ошибка check_subscription: {e}")
        return False, [REQUIRED_CHANNEL]

def get_channel_id(channel_username):
    """Получить ID канала"""
    try:
        url = f"{BASE_URL}/getChat"
        params = {"chat_id": channel_username}
        r = requests.get(url, params=params)
        data = r.json()
        if data.get("ok"):
            return data["result"]["id"]
    except:
        pass
    return None

def get_all_users():
    """Получить всех пользователей"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM users ORDER BY reg_date DESC")
        users = c.fetchall()
        conn.close()
        return [dict(u) for u in users]
    except:
        return []

def ban_user(user_id):
    """Забанить пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def unban_user(user_id):
    """Разбанить пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except:
        return False

# ==================== ПРОМОКОДЫ ====================

def generate_code():
    """Сгенерировать промокод"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(8))

def create_promo(days, uses, admin_id):
    """Создать промокод"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        code = generate_code()
        c.execute(
            "INSERT INTO promocodes (code, days, uses_left, created_by) VALUES (?, ?, ?, ?)",
            (code, days, uses, admin_id)
        )
        conn.commit()
        conn.close()
        return code
    except:
        return None

def use_promo(user_id, code):
    """Активировать промокод"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute("SELECT * FROM promocodes WHERE code = ? AND uses_left > 0", (code.upper(),))
        promo = c.fetchone()
        
        if not promo:
            conn.close()
            return False, "❌ Промокод не найден"
        
        c.execute("SELECT * FROM used_promos WHERE user_id = ? AND code = ?", (user_id, code.upper()))
        if c.fetchone():
            conn.close()
            return False, "❌ Промокод уже использован"
        
        days = promo[1]
        new_date = give_subscription(user_id, days, admin_give=True)
        
        c.execute("UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?", (code.upper(),))
        c.execute("INSERT INTO used_promos (user_id, code) VALUES (?, ?)", (user_id, code.upper()))
        
        conn.commit()
        conn.close()
        return True, f"✅ +{days} дней! Действует до: {new_date.strftime('%d.%m.%Y')}"
    except:
        return False, "❌ Ошибка активации"

def get_all_promos():
    """Все промокоды"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM promocodes ORDER BY created_at DESC")
        promos = c.fetchall()
        conn.close()
        return [dict(p) for p in promos]
    except:
        return []

# ==================== ОТПРАВКА СООБЩЕНИЙ ====================

def send_message(chat_id, text, keyboard=None, parse_mode="HTML"):
    """Отправить сообщение"""
    try:
        url = f"{BASE_URL}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if keyboard:
            data["reply_markup"] = json.dumps(keyboard)
        
        r = requests.post(url, data=data)
        if r.status_code != 200:
            logger.error(f"Ошибка отправки: {r.text}")
        return r.json()
    except Exception as e:
        logger.error(f"Ошибка send_message: {e}")
        return None

def edit_message(chat_id, msg_id, text, keyboard=None):
    """Изменить сообщение"""
    try:
        url = f"{BASE_URL}/editMessageText"
        data = {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if keyboard:
            data["reply_markup"] = json.dumps(keyboard)
        
        r = requests.post(url, data=data)
        return r.json()
    except:
        return None

def answer_callback(callback_id, text="", alert=False):
    """Ответ на callback"""
    try:
        url = f"{BASE_URL}/answerCallbackQuery"
        data = {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": alert
        }
        requests.post(url, data=data)
    except:
        pass

# ==================== КЛАВИАТУРЫ ====================

def main_keyboard(is_admin=False):
    """Главное меню"""
    kb = [
        [{"text": "🛡️ Получить доступ", "callback_data": "get_access"}],
        [{"text": "🌍 Выбрать сервер", "callback_data": "select_server"}],
        [{"text": "📱 Мои устройства", "callback_data": "my_devices"}],
        [{"text": "👤 Профиль", "callback_data": "profile"}],
        [{"text": "🎁 Промокод", "callback_data": "promo"}],
        [{"text": "📞 Поддержка", "callback_data": "support"}]
    ]
    if is_admin:
        kb.append([{"text": "⚙️ АДМИН ПАНЕЛЬ", "callback_data": "admin_menu"}])
    return {"inline_keyboard": kb}

def servers_keyboard():
    """Выбор сервера"""
    kb = []
    for sid, server in SERVERS.items():
        load_emoji = "🟢" if server["load"] < 30 else "🟡" if server["load"] < 60 else "🔴"
        kb.append([{
            "text": f"{server['flag']} {server['name']} • {load_emoji} {server['load']}% • {server['ping']}ms",
            "callback_data": f"server_{sid}"
        }])
    kb.append([{"text": "◀️ Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": kb}

def plans_keyboard():
    """Тарифы"""
    kb = []
    for pid, plan in PLANS.items():
        discount = int((1 - plan["price"]/plan["old_price"])*100)
        popular = " 🔥 ХИТ" if plan.get("popular") else ""
        kb.append([{
            "text": f"{plan['name']} • {plan['price']}₽ • −{discount}%{popular}",
            "callback_data": f"buy_{pid}"
        }])
    kb.append([{"text": "◀️ Назад", "callback_data": "get_access"}])
    return {"inline_keyboard": kb}

def protocols_keyboard():
    """Выбор протокола"""
    kb = []
    for protocol in PROTOCOLS:
        kb.append([{"text": f"🔒 {protocol}", "callback_data": f"protocol_{protocol}"}])
    kb.append([{"text": "◀️ Назад", "callback_data": "back_main"}])
    return {"inline_keyboard": kb}

def devices_keyboard():
    """Устройства"""
    return {
        "inline_keyboard": [
            [{"text": "📱 Android", "callback_data": "device_android"}],
            [{"text": "🍏 iOS", "callback_data": "device_ios"}],
            [{"text": "💻 Windows", "callback_data": "device_windows"}],
            [{"text": "🍎 macOS", "callback_data": "device_macos"}],
            [{"text": "🐧 Linux", "callback_data": "device_linux"}],
            [{"text": "◀️ Назад", "callback_data": "back_main"}]
        ]
    }

def sub_keyboard(channels):
    """Клавиатура подписки"""
    kb = []
    for ch in channels:
        kb.append([{
            "text": f"📢 Подписаться {ch}",
            "url": f"https://t.me/{ch.replace('@', '')}"
        }])
    kb.append([{"text": "✅ Я подписался", "callback_data": "check_sub"}])
    return {"inline_keyboard": kb}

def admin_keyboard():
    """Админ панель"""
    return {
        "inline_keyboard": [
            [{"text": "👥 Все пользователи", "callback_data": "admin_users"}],
            [{"text": "🎫 Создать промокод", "callback_data": "admin_create_promo"}],
            [{"text": "📊 Статистика", "callback_data": "admin_stats"}],
            [{"text": "🎁 Все промокоды", "callback_data": "admin_promos"}],
            [{"text": "◀️ Назад", "callback_data": "back_main"}]
        ]
    }

def back_keyboard():
    """Кнопка назад"""
    return {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "back_main"}]]}

# ==================== ОБРАБОТКА ВЕБХУКА ====================

@app.route('/', methods=['GET'])
def home():
    return "✅ VPN Bot is running 24/7!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Главный обработчик"""
    logger.info("🔥 Вебхук вызван")
    
    try:
        update = request.get_json()
        logger.info(f"📦 Update: {json.dumps(update, ensure_ascii=False)[:200]}...")
        
        # Обработка сообщений
        if "message" in update:
            handle_message(update["message"])
        
        # Обработка callback'ов
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        traceback.print_exc()
        return "Error", 500

def handle_message(message):
    """Обработка сообщений"""
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")
    username = message["from"].get("username", "")
    first_name = message["from"].get("first_name", "Друг")
    
    logger.info(f"💬 Сообщение от {user_id}: {text}")
    
    # Добавляем пользователя
    add_user(user_id, username, first_name)
    
    # Проверка на бан
    user = get_user(user_id)
    if user and user.get("banned"):
        send_message(chat_id, "⛔ Вы забанены")
        return
    
    # Проверка подписки на канал (кроме /start)
    if text != "/start":
        subscribed, channels = check_subscription(user_id)
        if not subscribed:
            send_message(chat_id, "🔐 Подпишитесь на канал:", sub_keyboard(channels))
            return
    
    # Обработка команд
    if text == "/start":
        handle_start(chat_id, user_id, first_name)
    elif text.startswith("/admin") and user_id in ADMIN_IDS:
        send_message(chat_id, "⚙️ Админ панель", admin_keyboard())

def handle_start(chat_id, user_id, first_name):
    """Обработка /start"""
    # Проверка подписки
    subscribed, channels = check_subscription(user_id)
    if not subscribed:
        welcome = f"👋 Привет, {first_name}!\n\n🔐 Для доступа подпишись на канал:"
        send_message(chat_id, welcome, sub_keyboard(channels))
        return
    
    # Приветствие
    welcome = f"🌟 <b>Добро пожаловать, {first_name}!</b>\n\n🔒 Быстрый и безопасный VPN\n✅ 6 серверов\n✅ Безлимитный трафик"
    
    is_admin = user_id in ADMIN_IDS
    send_message(chat_id, welcome, main_keyboard(is_admin))

def handle_callback(callback):
    """Обработка нажатий кнопок"""
    data = callback["data"]
    chat_id = callback["message"]["chat"]["id"]
    msg_id = callback["message"]["message_id"]
    user_id = callback["from"]["id"]
    cb_id = callback["id"]
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    # Проверка подписки (кроме check_sub)
    if data != "check_sub":
        subscribed, channels = check_subscription(user_id)
        if not subscribed:
            edit_message(chat_id, msg_id, "🔐 Подпишитесь на канал:", sub_keyboard(channels))
            answer_callback(cb_id)
            return
    
    is_admin = user_id in ADMIN_IDS
    
    # ===== ОСНОВНЫЕ КНОПКИ =====
    
    if data == "back_main":
        edit_message(chat_id, msg_id, "🏠 Главное меню:", main_keyboard(is_admin))
        answer_callback(cb_id)
    
    elif data == "check_sub":
        subscribed, channels = check_subscription(user_id)
        if subscribed:
            edit_message(chat_id, msg_id, "🌟 Добро пожаловать!", main_keyboard(is_admin))
            answer_callback(cb_id, "✅ Подписка подтверждена!")
        else:
            edit_message(chat_id, msg_id, "❌ Вы не подписались", sub_keyboard(channels))
            answer_callback(cb_id)
    
    elif data == "get_access":
        user = get_user(user_id)
        if user and user.get("subscribe_until") and datetime.fromisoformat(user["subscribe_until"]) > datetime.now():
            text = "🔑 У вас уже есть подписка!\n\nВы можете скачать конфиг или продлить."
            edit_message(chat_id, msg_id, text, subscription_keyboard())
        else:
            text = "📦 <b>Выберите тариф</b>\n\nДоступ ко всем серверам:"
            edit_message(chat_id, msg_id, text, plans_keyboard())
        answer_callback(cb_id)
    
    elif data == "select_server":
        text = "🌍 <b>Выберите сервер</b>\n\n⬇️ Нагрузка • ⏱ Пинг"
        edit_message(chat_id, msg_id, text, servers_keyboard())
        answer_callback(cb_id)
    
    elif data.startswith("server_"):
        server_id = data.replace("server_", "")
        server = SERVERS[server_id]
        
        # Сохраняем выбор сервера
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE users SET selected_server = ? WHERE user_id = ?", (server_id, user_id))
            conn.commit()
            conn.close()
        except:
            pass
        
        text = f"✅ Выбран сервер: {server['name']}\n\nТеперь выберите протокол:"
        edit_message(chat_id, msg_id, text, protocols_keyboard())
        answer_callback(cb_id, f"✅ {server['name']}")
    
    elif data.startswith("protocol_"):
        protocol = data.replace("protocol_", "")
        
        # Сохраняем выбор протокола
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE users SET selected_protocol = ? WHERE user_id = ?", (protocol, user_id))
            conn.commit()
            conn.close()
        except:
            pass
        
        text = f"✅ Выбран протокол: {protocol}\n\nНастройки сохранены!"
        edit_message(chat_id, msg_id, text, main_keyboard(is_admin))
        answer_callback(cb_id)
    
    elif data == "my_devices":
        text = "📱 <b>Инструкции для устройств</b>\n\nВыберите ваше устройство:"
        edit_message(chat_id, msg_id, text, devices_keyboard())
        answer_callback(cb_id)
    
    elif data.startswith("device_"):
        device = data.replace("device_", "")
        instructions = {
            "android": "📱 <b>Android</b>\n\n1. Установите OpenVPN Connect\n2. Скачайте конфиг\n3. Импортируйте",
            "ios": "🍏 <b>iOS</b>\n\n1. Установите OpenVPN Connect\n2. Скачайте конфиг\n3. Импортируйте",
            "windows": "💻 <b>Windows</b>\n\n1. Установите OpenVPN GUI\n2. Поместите конфиг в папку config\n3. Запустите",
            "macos": "🍎 <b>macOS</b>\n\n1. Установите Tunnelblick\n2. Откройте конфиг",
            "linux": "🐧 <b>Linux</b>\n\n1. sudo apt install openvpn\n2. sudo openvpn --config config.ovpn"
        }
        edit_message(chat_id, msg_id, instructions.get(device, "Инструкция готовится"), devices_keyboard())
        answer_callback(cb_id)
    
    elif data == "profile":
        user = get_user(user_id)
        
        if user and user.get("subscribe_until"):
            try:
                end = datetime.fromisoformat(user["subscribe_until"])
                days = (end - datetime.now()).days
                status = "✅ Активна" if days > 0 else "❌ Истекла"
                end_str = end.strftime("%d.%m.%Y")
            except:
                days = 0
                status = "❌ Ошибка"
                end_str = "—"
        else:
            days = 0
            status = "❌ Нет подписки"
            end_str = "—"
        
        server = SERVERS.get(user.get("selected_server", "netherlands"))
        protocol = user.get("selected_protocol", "OpenVPN")
        
        text = f"""👤 <b>Профиль</b>

📊 Статус: {status}
📅 Действует до: {end_str}
⏱ Осталось: {max(0, days)} дн.

🌍 Сервер: {server['name']}
🔌 Протокол: {protocol}

🆔 ID: <code>{user_id}</code>"""
        
        edit_message(chat_id, msg_id, text, back_keyboard())
        answer_callback(cb_id)
    
    elif data == "promo":
        text = "🎁 <b>Введите промокод</b>\n\nОтправьте его в чат:"
        edit_message(chat_id, msg_id, text, back_keyboard())
        answer_callback(cb_id)
    
    elif data == "support":
        text = "📞 <b>Поддержка</b>\n\n👤 @vpn_support_bot"
        edit_message(chat_id, msg_id, text, back_keyboard())
        answer_callback(cb_id)
    
    # ===== АДМИН КНОПКИ =====
    
    elif data == "admin_menu" and is_admin:
        edit_message(chat_id, msg_id, "⚙️ <b>Админ панель</b>", admin_keyboard())
        answer_callback(cb_id)
    
    elif data == "admin_users" and is_admin:
        users = get_all_users()
        text = f"👥 <b>Все пользователи ({len(users)})</b>\n\n"
        for u in users[:10]:
            sub = "✅" if u.get("subscribe_until") and datetime.fromisoformat(u["subscribe_until"]) > datetime.now() else "❌"
            ban = "🔴" if u.get("banned") else "🟢"
            text += f"{ban}{sub} {u.get('first_name', '—')} (@{u.get('username', '—')})\n"
        edit_message(chat_id, msg_id, text, admin_keyboard())
        answer_callback(cb_id)
    
    elif data == "admin_stats" and is_admin:
        users = get_all_users()
        active = sum(1 for u in users if u.get("subscribe_until") and 
                    datetime.fromisoformat(u["subscribe_until"]) > datetime.now())
        banned = sum(1 for u in users if u.get("banned"))
        
        text = f"""📊 <b>Статистика</b>

👥 Всего: {len(users)}
✅ Активных: {active}
🔒 Забанено: {banned}
📈 Конверсия: {active/len(users)*100:.1f}%"""
        
        edit_message(chat_id, msg_id, text, admin_keyboard())
        answer_callback(cb_id)
    
    elif data == "admin_create_promo" and is_admin:
        text = "🎫 <b>Создание промокода</b>\n\nОтправьте: <code>дней использований</code>\nПример: <code>30 10</code>"
        edit_message(chat_id, msg_id, text, admin_keyboard())
        answer_callback(cb_id)
    
    elif data == "admin_promos" and is_admin:
        promos = get_all_promos()
        if not promos:
            text = "📋 Промокодов пока нет"
        else:
            text = "🎫 <b>Промокоды</b>\n\n"
            for p in promos[:10]:
                text += f"🎟 <code>{p['code']}</code> — {p['days']} дн., осталось {p['uses_left']}\n"
        edit_message(chat_id, msg_id, text, admin_keyboard())
        answer_callback(cb_id)

def subscription_keyboard():
    """Клавиатура подписки"""
    return {
        "inline_keyboard": [
            [{"text": "🔄 Продлить", "callback_data": "get_access"}],
            [{"text": "📥 Скачать конфиг", "callback_data": "download_config"}],
            [{"text": "🌍 Сменить сервер", "callback_data": "select_server"}],
            [{"text": "◀️ Назад", "callback_data": "back_main"}]
        ]
    }

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    print("=" * 50)
    print("🌟 VPN БОТ - ПОЛНАЯ ВЕРСИЯ")
    print("=" * 50)
    print(f"🤖 Токен: {BOT_TOKEN[:10]}...")
    print(f"👑 Админ: {ADMIN_IDS[0]}")
    print(f"📢 Канал: {REQUIRED_CHANNEL}")
    print("=" * 50)
    
    # Инициализация БД
    if init_db():
        print("✅ База данных готова")
    else:
        print("❌ Ошибка базы данных")
    
    # Получаем порт от Render
    port = int(os.environ.get("PORT", 5000))
    print(f"🌐 Сервер запущен на порту {port}")
    
    # Запускаем Flask
    app.run(host="0.0.0.0", port=port)

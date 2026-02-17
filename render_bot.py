#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║        🚀 TURBO VPN BOT - МАКСИМАЛЬНАЯ СКОРОСТЬ               ║
║     Удаление старых сообщений вместо редактирования           ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import aiosqlite
import httpx

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Централизованная конфигурация"""
    
    # Telegram
    BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
    ADMIN_IDS = [8443743937]
    REQUIRED_CHANNEL = "@numberbor"
    BOT_USERNAME = "Playinc_bot"
    
    # База данных
    DB_PATH = "/tmp/vpn_bot.db"
    
    # Пробный период - 6 дней
    TRIAL_DAYS = 6
    
    # Реферальная система
    REFERRAL_BONUS_DAYS = 3
    
    # Производительность
    REQUEST_TIMEOUT = 5.0
    
    # Пути
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://vpn-bot-aemr.onrender.com")
    WEBHOOK_PATH = "/webhook"

config = Config()

# ==================== БАЗА ДАННЫХ ====================

class Database:
    """Управление базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def init(self):
        """Инициализация БД"""
        async with aiosqlite.connect(self.db_path) as db:
            # 👤 Пользователи
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    subscribe_until TEXT,
                    trial_used INTEGER DEFAULT 0,
                    banned INTEGER DEFAULT 0,
                    selected_server TEXT DEFAULT 'netherlands',
                    selected_protocol TEXT DEFAULT 'OpenVPN',
                    referred_by INTEGER,
                    referral_code TEXT UNIQUE,
                    referral_count INTEGER DEFAULT 0,
                    last_active TIMESTAMP,
                    last_message_id INTEGER,
                    reg_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 👥 Рефералы
            await db.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Индексы
            await db.execute('CREATE INDEX IF NOT EXISTS idx_referral_code ON users(referral_code)')
            await db.commit()
        
        logger.info("✅ База данных готова")
    
    async def fetch_one(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def fetch_all(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def execute(self, query: str, params: tuple = ()):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(query, params)
            await db.commit()

db = Database(config.DB_PATH)

# ==================== МЕНЕДЖЕР ПОЛЬЗОВАТЕЛЕЙ ====================

class UserManager:
    """Управление пользователями"""
    
    @staticmethod
    async def get(user_id: int) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    
    @staticmethod
    async def get_by_referral_code(code: str) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM users WHERE referral_code = ?", (code,))
    
    @staticmethod
    async def create(user_id: int, username: str, first_name: str, referred_by: int = None):
        existing = await UserManager.get(user_id)
        if existing:
            return existing
        
        referral_code = secrets.token_hex(4).upper()
        
        await db.execute(
            "INSERT INTO users (user_id, username, first_name, referred_by, referral_code, last_active) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, referred_by, referral_code, datetime.now().isoformat())
        )
        
        if referred_by:
            await db.execute(
                "INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                (referred_by, user_id)
            )
            await db.execute(
                "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?",
                (referred_by,)
            )
        
        return await UserManager.get(user_id)
    
    @staticmethod
    async def save_message_id(user_id: int, message_id: int):
        """Сохранить ID последнего сообщения"""
        await db.execute(
            "UPDATE users SET last_message_id = ? WHERE user_id = ?",
            (message_id, user_id)
        )
    
    @staticmethod
    async def update_server(user_id: int, server_id: str):
        await db.execute(
            "UPDATE users SET selected_server = ?, last_active = ? WHERE user_id = ?",
            (server_id, datetime.now().isoformat(), user_id)
        )
    
    @staticmethod
    async def update_protocol(user_id: int, protocol: str):
        await db.execute(
            "UPDATE users SET selected_protocol = ?, last_active = ? WHERE user_id = ?",
            (protocol, datetime.now().isoformat(), user_id)
        )
    
    @staticmethod
    async def activate_trial(user_id: int) -> Tuple[bool, str]:
        user = await UserManager.get(user_id)
        
        if not user:
            return False, "❌ Пользователь не найден"
        
        if user.get("trial_used"):
            return False, "❌ Вы уже использовали пробный период"
        
        trial_end = datetime.now() + timedelta(days=config.TRIAL_DAYS)
        
        await db.execute(
            "UPDATE users SET subscribe_until = ?, trial_used = 1 WHERE user_id = ?",
            (trial_end.isoformat(), user_id)
        )
        
        return True, f"✅ Пробный период {config.TRIAL_DAYS} дней активирован!\n📅 Действует до: {trial_end.strftime('%d.%m.%Y')}"
    
    @staticmethod
    async def give_subscription(user_id: int, days: int, admin_give: bool = False):
        user = await UserManager.get(user_id)
        
        if user and user.get("subscribe_until") and not admin_give:
            try:
                old_date = datetime.fromisoformat(user["subscribe_until"])
                new_date = old_date + timedelta(days=days)
            except:
                new_date = datetime.now() + timedelta(days=days)
        else:
            new_date = datetime.now() + timedelta(days=days)
        
        await db.execute(
            "UPDATE users SET subscribe_until = ?, last_active = ? WHERE user_id = ?",
            (new_date.isoformat(), datetime.now().isoformat(), user_id)
        )
        
        return new_date
    
    @staticmethod
    async def get_referrals(user_id: int) -> List[Dict]:
        return await db.fetch_all(
            """
            SELECT u.user_id, u.username, u.first_name, u.subscribe_until, r.created_at
            FROM referrals r
            JOIN users u ON r.referred_id = u.user_id
            WHERE r.referrer_id = ?
            ORDER BY r.created_at DESC
            """,
            (user_id,)
        )
    
    @staticmethod
    async def get_referral_stats(user_id: int) -> Dict:
        referrals = await UserManager.get_referrals(user_id)
        
        total = len(referrals)
        active = 0
        for r in referrals:
            if r.get("subscribe_until"):
                try:
                    if datetime.fromisoformat(r["subscribe_until"]) > datetime.now():
                        active += 1
                except:
                    pass
        
        return {"total": total, "active": active}

# ==================== ПРОВЕРКА ПОДПИСКИ ====================

class SubscriptionChecker:
    """Проверка подписки на канал"""
    
    def __init__(self, bot):
        self.bot = bot
        self.channel_id = None
    
    async def get_channel_id(self):
        if self.channel_id:
            return self.channel_id
        try:
            chat = await self.bot.get_chat(config.REQUIRED_CHANNEL)
            self.channel_id = chat.id
            return chat.id
        except:
            return None
    
    async def check_user(self, user_id: int) -> Tuple[bool, List[str]]:
        try:
            channel_id = await self.get_channel_id()
            if not channel_id:
                return False, [config.REQUIRED_CHANNEL]
            
            member = await self.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in ["member", "administrator", "creator"]:
                return True, []
            return False, [config.REQUIRED_CHANNEL]
        except:
            return False, [config.REQUIRED_CHANNEL]

# ==================== ДАННЫЕ ====================

SERVERS = {
    "netherlands": {"name": "🇳🇱 Нидерланды", "flag": "🇳🇱", "city": "Амстердам", "load": 32, "ping": 45},
    "usa": {"name": "🇺🇸 США", "flag": "🇺🇸", "city": "Нью-Йорк", "load": 45, "ping": 120},
    "germany": {"name": "🇩🇪 Германия", "flag": "🇩🇪", "city": "Франкфурт", "load": 28, "ping": 55},
    "uk": {"name": "🇬🇧 Великобритания", "flag": "🇬🇧", "city": "Лондон", "load": 38, "ping": 65},
    "singapore": {"name": "🇸🇬 Сингапур", "flag": "🇸🇬", "city": "Сингапур", "load": 22, "ping": 150},
    "japan": {"name": "🇯🇵 Япония", "flag": "🇯🇵", "city": "Токио", "load": 19, "ping": 180}
}

PLANS = {
    "1month": {"name": "🌱 1 месяц", "days": 30, "price": 299, "old_price": 499},
    "3month": {"name": "🌿 3 месяца", "days": 90, "price": 699, "old_price": 1197},
    "6month": {"name": "🌳 6 месяцев", "days": 180, "price": 1199, "old_price": 2394},
    "12month": {"name": "🏝️ 12 месяцев", "days": 365, "price": 1999, "old_price": 4788}
}

PROTOCOLS = ["OpenVPN", "WireGuard", "IKEv2"]

# ==================== КЛАВИАТУРЫ ====================

class KeyboardBuilder:
    """Построитель клавиатур"""
    
    @staticmethod
    def main(is_admin: bool = False):
        buttons = [
            [InlineKeyboardButton("🛡️ ПОДКЛЮЧИТЬ VPN", callback_data="get_access")],
            [InlineKeyboardButton("🌍 ВЫБРАТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("📱 УСТРОЙСТВА", callback_data="my_devices")],
            [InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile")],
            [InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")],
            [InlineKeyboardButton("🎁 ПРОМОКОД", callback_data="promo")],
            [InlineKeyboardButton("📞 ПОДДЕРЖКА", callback_data="support")]
        ]
        if is_admin:
            buttons.append([InlineKeyboardButton("⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def servers():
        buttons = []
        for sid, server in SERVERS.items():
            load = "🟢" if server["load"] < 30 else "🟡" if server["load"] < 60 else "🔴"
            buttons.append([InlineKeyboardButton(
                f"{server['flag']} {server['name']} • {load} {server['load']}% • {server['ping']}ms",
                callback_data=f"server_{sid}"
            )])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def plans():
        buttons = []
        for pid, plan in PLANS.items():
            discount = int((1 - plan["price"]/plan["old_price"])*100)
            buttons.append([InlineKeyboardButton(
                f"{plan['name']} • {plan['price']}₽ • −{discount}%",
                callback_data=f"buy_{pid}"
            )])
        buttons.append([InlineKeyboardButton("🎁 ПРОБНЫЙ ПЕРИОД 6 ДНЕЙ", callback_data="trial")])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def protocols():
        buttons = []
        for protocol in PROTOCOLS:
            buttons.append([InlineKeyboardButton(f"🔒 {protocol}", callback_data=f"protocol_{protocol}")])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def devices():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 ANDROID", callback_data="device_android")],
            [InlineKeyboardButton("🍏 IOS", callback_data="device_ios")],
            [InlineKeyboardButton("💻 WINDOWS", callback_data="device_windows")],
            [InlineKeyboardButton("🍎 MACOS", callback_data="device_macos")],
            [InlineKeyboardButton("🐧 LINUX", callback_data="device_linux")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def subscription():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 ПРОДЛИТЬ", callback_data="get_access")],
            [InlineKeyboardButton("📥 СКАЧАТЬ КОНФИГ", callback_data="download_config")],
            [InlineKeyboardButton("🌍 СМЕНИТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def subscribe(channels):
        buttons = []
        for ch in channels:
            buttons.append([InlineKeyboardButton(
                f"📢 ПОДПИСАТЬСЯ {ch}",
                url=f"https://t.me/{ch.replace('@', '')}"
            )])
        buttons.append([InlineKeyboardButton("✅ Я ПОДПИСАЛСЯ", callback_data="check_sub")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def referrals(referral_code: str):
        ref_link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{referral_code}"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 РЕФЕРАЛЬНАЯ ССЫЛКА", url=ref_link)],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="referral_stats")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def back():
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== FASTAPI ПРИЛОЖЕНИЕ ====================

app = FastAPI()
telegram_app = None
subscription_checker = None
startup_time = time.time()

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С СООБЩЕНИЯМИ ====================

async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Удалить предыдущее сообщение пользователя"""
    user = await UserManager.get(chat_id)
    if user and user.get("last_message_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=user["last_message_id"])
        except:
            pass

async def send_new_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, keyboard=None):
    """Отправить новое сообщение и сохранить его ID"""
    # Удаляем предыдущее
    await delete_previous_message(context, chat_id)
    
    # Отправляем новое
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    
    # Сохраняем ID
    await UserManager.save_message_id(chat_id, msg.message_id)
    return msg

# ==================== ОБРАБОТЧИКИ TELEGRAM ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    user = update.effective_user
    args = context.args
    
    # Проверяем реферальный код
    referred_by = None
    if args and args[0].startswith("ref_"):
        ref_code = args[0].replace("ref_", "")
        referrer = await UserManager.get_by_referral_code(ref_code)
        if referrer and referrer["user_id"] != user.id:
            referred_by = referrer["user_id"]
    
    # Создаем пользователя
    await UserManager.create(user.id, user.username or "", user.first_name or "", referred_by)
    
    # Проверка на бан
    db_user = await UserManager.get(user.id)
    if db_user and db_user.get("banned"):
        await update.message.reply_text("⛔ Доступ заблокирован")
        return
    
    # Проверка подписки
    subscribed, channels = await subscription_checker.check_user(user.id)
    if not subscribed:
        text = f"👋 <b>Привет, {user.first_name}!</b>\n\n🔐 Подпишись на канал:"
        await send_new_message(context, user.id, text, KeyboardBuilder.subscribe(channels))
        return
    
    # Приветствие
    text = (
        f"🌟 <b>ДОБРО ПОЖАЛОВАТЬ!</b>\n\n"
        f"👤 {user.first_name}\n"
        f"🎁 Пробный период {config.TRIAL_DAYS} дней\n"
        f"👥 +{config.REFERRAL_BONUS_DAYS} дня за друга"
    )
    
    is_admin = user.id in config.ADMIN_IDS
    await send_new_message(context, user.id, text, KeyboardBuilder.main(is_admin))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Проверка подписки (кроме check_sub)
    if data not in ["check_sub", "back_main"]:
        subscribed, channels = await subscription_checker.check_user(user_id)
        if not subscribed:
            await send_new_message(
                context, 
                user_id, 
                "🔐 Подпишись на канал:",
                KeyboardBuilder.subscribe(channels)
            )
            return
    
    is_admin = user_id in config.ADMIN_IDS
    
    # ===== НАВИГАЦИЯ =====
    if data == "back_main":
        await send_new_message(
            context,
            user_id,
            "🏠 ГЛАВНОЕ МЕНЮ",
            KeyboardBuilder.main(is_admin)
        )
    
    elif data == "check_sub":
        subscribed, channels = await subscription_checker.check_user(user_id)
        if subscribed:
            await send_new_message(
                context,
                user_id,
                "🌟 ДОБРО ПОЖАЛОВАТЬ!",
                KeyboardBuilder.main(is_admin)
            )
        else:
            await send_new_message(
                context,
                user_id,
                "❌ Подпишись на канал:",
                KeyboardBuilder.subscribe(channels)
            )
    
    # ===== ПРОБНЫЙ ПЕРИОД =====
    elif data == "trial":
        success, msg = await UserManager.activate_trial(user_id)
        await send_new_message(context, user_id, msg, KeyboardBuilder.main(is_admin))
    
    # ===== ПОКУПКА ПОДПИСКИ =====
    elif data == "get_access":
        user = await UserManager.get(user_id)
        if user and user.get("subscribe_until"):
            try:
                if datetime.fromisoformat(user["subscribe_until"]) > datetime.now():
                    await send_new_message(
                        context,
                        user_id,
                        "🔑 У вас активная подписка",
                        KeyboardBuilder.subscription()
                    )
                    return
            except:
                pass
        await send_new_message(
            context,
            user_id,
            "📦 ВЫБЕРИТЕ ТАРИФ",
            KeyboardBuilder.plans()
        )
    
    elif data.startswith("buy_"):
        plan_id = data.replace("buy_", "")
        if plan_id in PLANS:
            plan = PLANS[plan_id]
            new_date = await UserManager.give_subscription(user_id, plan["days"])
            
            # Проверяем реферала
            user = await UserManager.get(user_id)
            if user and user.get("referred_by"):
                await UserManager.give_subscription(user["referred_by"], config.REFERRAL_BONUS_DAYS)
                await send_new_message(
                    context,
                    user["referred_by"],
                    f"🎉 Ваш реферал активировал подписку!\n✅ +{config.REFERRAL_BONUS_DAYS} дня"
                )
            
            await send_new_message(
                context,
                user_id,
                f"✅ Подписка {plan['name']} активирована!\n📅 До: {new_date.strftime('%d.%m.%Y')}",
                KeyboardBuilder.main(is_admin)
            )
    
    # ===== ВЫБОР СЕРВЕРА =====
    elif data == "select_server":
        await send_new_message(
            context,
            user_id,
            "🌍 ВЫБЕРИТЕ СЕРВЕР",
            KeyboardBuilder.servers()
        )
    
    elif data.startswith("server_"):
        server_id = data.replace("server_", "")
        if server_id in SERVERS:
            await UserManager.update_server(user_id, server_id)
            await send_new_message(
                context,
                user_id,
                f"✅ Выбран {SERVERS[server_id]['name']}\n\nВыберите протокол:",
                KeyboardBuilder.protocols()
            )
    
    elif data.startswith("protocol_"):
        protocol = data.replace("protocol_", "")
        await UserManager.update_protocol(user_id, protocol)
        await send_new_message(
            context,
            user_id,
            f"✅ Протокол {protocol} сохранен",
            KeyboardBuilder.main(is_admin)
        )
    
    # ===== УСТРОЙСТВА =====
    elif data == "my_devices":
        await send_new_message(
            context,
            user_id,
            "📱 ВЫБЕРИТЕ УСТРОЙСТВО",
            KeyboardBuilder.devices()
        )
    
    elif data.startswith("device_"):
        device = data.replace("device_", "")
        instructions = {
            "android": "📱 ANDROID\n\n1. Установите OpenVPN Connect\n2. Скачайте конфиг\n3. Импортируйте",
            "ios": "🍏 IOS\n\n1. Установите OpenVPN Connect\n2. Скачайте конфиг\n3. Импортируйте",
            "windows": "💻 WINDOWS\n\n1. Установите OpenVPN GUI\n2. Поместите конфиг в папку config\n3. Запустите",
            "macos": "🍎 MACOS\n\n1. Установите Tunnelblick\n2. Откройте конфиг",
            "linux": "🐧 LINUX\n\n1. sudo apt install openvpn\n2. sudo openvpn --config config.ovpn"
        }
        await send_new_message(
            context,
            user_id,
            instructions.get(device, "Инструкция готовится"),
            KeyboardBuilder.devices()
        )
    
    # ===== ПРОФИЛЬ =====
    elif data == "profile":
        user = await UserManager.get(user_id)
        
        if user and user.get("subscribe_until"):
            try:
                end = datetime.fromisoformat(user["subscribe_until"])
                days = (end - datetime.now()).days
                status = "✅ Активна" if days > 0 else "❌ Истекла"
                end_str = end.strftime("%d.%m.%Y")
            except:
                days = 0
                status = "❌ Ошибка"
                end_str = "-"
        else:
            days = 0
            status = "❌ Нет подписки"
            end_str = "-"
        
        server = SERVERS.get(user.get("selected_server", "netherlands"), SERVERS["netherlands"])
        protocol = user.get("selected_protocol", "OpenVPN")
        
        text = (
            f"👤 ПРОФИЛЬ\n\n"
            f"Статус: {status}\n"
            f"До: {end_str}\n"
            f"Осталось: {max(0, days)} дн.\n\n"
            f"Сервер: {server['name']}\n"
            f"Протокол: {protocol}"
        )
        
        await send_new_message(context, user_id, text, KeyboardBuilder.back())
    
    # ===== РЕФЕРАЛЫ =====
    elif data == "referrals":
        user = await UserManager.get(user_id)
        if user:
            stats = await UserManager.get_referral_stats(user_id)
            text = (
                f"👥 РЕФЕРАЛЫ\n\n"
                f"Ваш код: <code>{user['referral_code']}</code>\n"
                f"Всего: {stats['total']}\n"
                f"Активных: {stats['active']}\n\n"
                f"За каждого друга +{config.REFERRAL_BONUS_DAYS} дня"
            )
            await send_new_message(
                context,
                user_id,
                text,
                KeyboardBuilder.referrals(user['referral_code'])
            )
    
    elif data == "referral_stats":
        user = await UserManager.get(user_id)
        if user:
            referrals = await UserManager.get_referrals(user_id)
            text = "👥 СПИСОК РЕФЕРАЛОВ\n\n"
            if not referrals:
                text += "Пока нет рефералов"
            else:
                for ref in referrals[:10]:
                    name = ref.get('first_name', '—')[:10]
                    date = ref['created_at'][:10]
                    text += f"• @{ref.get('username', name)} - {date}\n"
            await send_new_message(context, user_id, text, KeyboardBuilder.back())
    
    # ===== ПРОМОКОД =====
    elif data == "promo":
        await send_new_message(
            context,
            user_id,
            "🎁 ВВЕДИТЕ ПРОМОКОД",
            KeyboardBuilder.back()
        )
        context.user_data['awaiting_promo'] = True
    
    # ===== ПОДДЕРЖКА =====
    elif data == "support":
        await send_new_message(
            context,
            user_id,
            "📞 ПОДДЕРЖКА\n\n@vpn_support_bot",
            KeyboardBuilder.back()
        )
    
    # ===== СКАЧАТЬ КОНФИГ =====
    elif data == "download_config":
        user = await UserManager.get(user_id)
        if user and user.get("subscribe_until"):
            try:
                if datetime.fromisoformat(user["subscribe_until"]) > datetime.now():
                    server = SERVERS[user.get("selected_server", "netherlands")]
                    config_text = f"""# VPN Config
# Server: {server['name']}
# Generated: {datetime.now().strftime('%Y-%m-%d')}

client
dev tun
proto udp
remote {server['city'].lower()}.vpn.com 1194
resolv-retry infinite
nobind
persist-key
persist-tun
verb 3"""
                    
                    await delete_previous_message(context, user_id)
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=config_text.encode(),
                        filename=f"vpn_{server['city'].lower()}.ovpn",
                        caption=f"✅ Конфиг для {server['name']}"
                    )
                    return
            except:
                pass
        await send_new_message(
            context,
            user_id,
            "❌ Подписка не активна",
            KeyboardBuilder.plans()
        )
    
    # ===== АДМИН ПАНЕЛЬ =====
    elif data == "admin_menu" and is_admin:
        await send_new_message(
            context,
            user_id,
            "⚙️ АДМИН ПАНЕЛЬ",
            KeyboardBuilder.back()
        )

# ==================== FASTAPI ЭНДПОИНТЫ ====================

@app.on_event("startup")
async def startup():
    """При запуске"""
    global telegram_app, subscription_checker
    
    await db.init()
    
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    
    subscription_checker = SubscriptionChecker(telegram_app.bot)
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    webhook_url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await telegram_app.bot.set_webhook(url=webhook_url)
    
    logger.info("✅ Бот запущен!")
    logger.info(f"🚀 Режим: удаление сообщений (максимальная скорость)")

@app.on_event("shutdown")
async def shutdown():
    """При остановке"""
    if telegram_app:
        await telegram_app.stop()

@app.post(config.WEBHOOK_PATH)
async def webhook(request: Request):
    """Вебхук"""
    if not telegram_app:
        return {"ok": False}
    
    json_data = await request.json()
    update = Update.de_json(json_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def home():
    return {
        "status": "online",
        "mode": "delete_messages",
        "trial_days": config.TRIAL_DAYS
    }

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("render_bot_fast_delete:app", host="0.0.0.0", port=port)

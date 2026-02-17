#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║        🚀 VPN BOT - СРОЧНОЕ ИСПРАВЛЕНИЕ БАЗЫ ДАННЫХ           ║
║                 ТАБЛИЦЫ СОЗДАЮТСЯ ПРИ ЗАПУСКЕ                 ║
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

from fastapi import FastAPI, Request
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import aiosqlite

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
    ADMIN_IDS = [8443743937]
    REQUIRED_CHANNEL = "@numberbor"
    BOT_USERNAME = "Playinc_bot"
    DB_PATH = "/tmp/vpn_bot.db"
    TRIAL_DAYS = 6
    REFERRAL_BONUS_DAYS = 3
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://vpn-bot-aemr.onrender.com")
    WEBHOOK_PATH = "/webhook"

config = Config()

# ==================== БАЗА ДАННЫХ (ГАРАНТИРОВАННОЕ СОЗДАНИЕ) ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def init(self):
        """Принудительное создание всех таблиц"""
        try:
            logger.info("📦 Создание базы данных...")
            
            # Создаем директорию если нужно
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            # Подключаемся и создаем таблицы
            async with aiosqlite.connect(self.db_path) as db:
                # Включаем режим WAL для производительности
                await db.execute("PRAGMA journal_mode = WAL")
                
                # Таблица пользователей
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
                        last_active TEXT,
                        last_message_id INTEGER,
                        reg_date TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Таблица рефералов
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS referrals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        referrer_id INTEGER,
                        referred_id INTEGER,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Индексы
                await db.execute('CREATE INDEX IF NOT EXISTS idx_referral_code ON users(referral_code)')
                
                await db.commit()
                
                # Проверяем, что таблицы создались
                cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = await cursor.fetchall()
                logger.info(f"✅ Созданы таблицы: {[t[0] for t in tables]}")
                
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка создания БД: {e}")
            return False
    
    async def execute(self, query: str, params: tuple = ()):
        """Выполнить запрос"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(query, params)
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка execute: {e}")
    
    async def fetch_one(self, query: str, params: tuple = ()):
        """Получить одну запись"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(query, params)
                row = await cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка fetch_one: {e}")
            return None
    
    async def fetch_all(self, query: str, params: tuple = ()):
        """Получить все записи"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(query, params)
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка fetch_all: {e}")
            return []

db = Database(config.DB_PATH)

# ==================== МЕНЕДЖЕР ПОЛЬЗОВАТЕЛЕЙ ====================

class UserManager:
    @staticmethod
    async def get(user_id: int) -> Optional[Dict]:
        try:
            return await db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        except Exception as e:
            logger.error(f"Ошибка get_user: {e}")
            return None
    
    @staticmethod
    async def create(user_id: int, username: str, first_name: str, referred_by: int = None):
        try:
            # Проверяем существование
            existing = await UserManager.get(user_id)
            if existing:
                return existing
            
            # Генерируем код
            referral_code = secrets.token_hex(4).upper()
            
            # Вставляем
            await db.execute(
                """INSERT INTO users 
                   (user_id, username, first_name, referred_by, referral_code, last_active) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, username, first_name, referred_by, referral_code, datetime.now().isoformat())
            )
            
            # Если есть реферер
            if referred_by:
                await db.execute(
                    "INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                    (referred_by, user_id)
                )
            
            logger.info(f"✅ Создан пользователь {user_id}")
            return await UserManager.get(user_id)
            
        except Exception as e:
            logger.error(f"Ошибка create_user: {e}")
            return None
    
    @staticmethod
    async def save_message_id(user_id: int, message_id: int):
        try:
            await db.execute(
                "UPDATE users SET last_message_id = ? WHERE user_id = ?",
                (message_id, user_id)
            )
        except Exception as e:
            logger.error(f"Ошибка save_message_id: {e}")

# ==================== ДАННЫЕ ====================

SERVERS = {
    "netherlands": {"name": "🇳🇱 Нидерланды", "flag": "🇳🇱", "load": 32, "ping": 45},
    "usa": {"name": "🇺🇸 США", "flag": "🇺🇸", "load": 45, "ping": 120},
}

# ==================== КЛАВИАТУРЫ ====================

class KeyboardBuilder:
    @staticmethod
    def main(is_admin: bool = False):
        buttons = [
            [InlineKeyboardButton("🛡️ ПОДКЛЮЧИТЬ VPN", callback_data="get_access")],
            [InlineKeyboardButton("🌍 ВЫБРАТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile")],
            [InlineKeyboardButton("📞 ПОДДЕРЖКА", callback_data="support")]
        ]
        if is_admin:
            buttons.append([InlineKeyboardButton("⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def back():
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== FASTAPI ПРИЛОЖЕНИЕ ====================

app = FastAPI()
telegram_app = None

# ==================== ФУНКЦИИ ДЛЯ СООБЩЕНИЙ ====================

async def send_new_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, keyboard=None):
    """Отправить сообщение"""
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        await UserManager.save_message_id(chat_id, msg.message_id)
        return msg
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return None

# ==================== ОБРАБОТЧИКИ ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка /start"""
    try:
        user = update.effective_user
        logger.info(f"🚀 /start от {user.id}")
        
        # СОЗДАЕМ ПОЛЬЗОВАТЕЛЯ
        await UserManager.create(user.id, user.username or "", user.first_name or "")
        
        text = f"🌟 <b>Добро пожаловать, {user.first_name}!</b>"
        is_admin = user.id in config.ADMIN_IDS
        await send_new_message(context, user.id, text, KeyboardBuilder.main(is_admin))
        
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок"""
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        logger.info(f"🔘 Кнопка: {data} от {user_id}")
        
        is_admin = user_id in config.ADMIN_IDS
        
        if data == "back_main":
            await send_new_message(context, user_id, "🏠 Главное меню", KeyboardBuilder.main(is_admin))
        
        elif data == "profile":
            user = await UserManager.get(user_id)
            
            # Данные по умолчанию
            status = "❌ Нет подписки"
            days = 0
            server = "netherlands"
            
            if user and user.get("subscribe_until"):
                try:
                    end = datetime.fromisoformat(user["subscribe_until"])
                    days = (end - datetime.now()).days
                    if days > 0:
                        status = "✅ Активна"
                    server = user.get("selected_server", "netherlands")
                except:
                    pass
            
            server_name = SERVERS.get(server, SERVERS["netherlands"])["name"]
            
            text = f"👤 ПРОФИЛЬ\n\nСтатус: {status}\nОсталось: {max(0, days)} дн.\nСервер: {server_name}"
            await send_new_message(context, user_id, text, KeyboardBuilder.back())
        
    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")

# ==================== FASTAPI ====================

@app.on_event("startup")
async def startup():
    """Запуск"""
    global telegram_app
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК БОТА")
    
    # СОЗДАЕМ БАЗУ ДАННЫХ ПРЯМО СЕЙЧАС
    if await db.init():
        logger.info("✅ База данных готова")
    else:
        logger.error("❌ Ошибка базы данных")
        return
    
    # Telegram
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Вебхук
    webhook_url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await telegram_app.bot.set_webhook(url=webhook_url)
    
    logger.info(f"✅ Вебхук: {webhook_url}")
    logger.info("✅ Бот готов!")
    logger.info("=" * 50)

@app.on_event("shutdown")
async def shutdown():
    if telegram_app:
        await telegram_app.stop()

@app.post(config.WEBHOOK_PATH)
async def webhook(request: Request):
    if not telegram_app:
        return {"ok": False}
    
    json_data = await request.json()
    update = Update.de_json(json_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def home():
    return {"status": "online"}

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("render_bot:app", host="0.0.0.0", port=port)

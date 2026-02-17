#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║                    🌟 PREMIUM VPN BOT v2.0                    ║
║              Быстрый • Надежный • Профессиональный             ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import uuid
import hmac
import hashlib
import secrets
import logging
import asyncio
import sqlite3
import aiosqlite
import traceback
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from functools import lru_cache, wraps
from dataclasses import dataclass, asdict
from enum import Enum

# ==================== ВНЕШНИЕ ИМПОРТЫ ====================

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import jwt
from pydantic import BaseModel, Field

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Централизованная конфигурация"""
    
    # Telegram
    BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
    ADMIN_IDS = [8443743937]
    REQUIRED_CHANNEL = "@numberbor"
    
    # База данных
    DB_PATH = "/tmp/vpn_bot.db"
    DB_POOL_SIZE = 10
    DB_TIMEOUT = 5.0
    
    # JWT
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30
    
    # Кэширование
    CACHE_TTL = 60  # секунд
    CACHE_MAX_SIZE = 1000
    
    # Производительность
    MAX_CONNECTIONS = 100
    RATE_LIMIT = 30  # запросов в минуту
    
    # Пути
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://vpn-bot-aemr.onrender.com")
    WEBHOOK_PATH = "/webhook"
    HEALTH_PATH = "/health"

config = Config()

# ==================== МОДЕЛИ ДАННЫХ ====================

class UserStatus(Enum):
    ACTIVE = "active"
    BANNED = "banned"
    TRIAL = "trial"
    EXPIRED = "expired"

class ServerLoad(Enum):
    LOW = "🟢"
    MEDIUM = "🟡"
    HIGH = "🔴"

@dataclass
class Server:
    id: str
    name: str
    flag: str
    city: str
    load: int
    ping: int
    
    @property
    def load_status(self) -> ServerLoad:
        if self.load < 30:
            return ServerLoad.LOW
        elif self.load < 60:
            return ServerLoad.MEDIUM
        return ServerLoad.HIGH
    
    @property
    def display_name(self) -> str:
        return f"{self.flag} {self.name}"

@dataclass
class Plan:
    id: str
    name: str
    days: int
    price: int
    old_price: int
    emoji: str
    
    @property
    def discount(self) -> int:
        return int((1 - self.price / self.old_price) * 100)
    
    @property
    def display(self) -> str:
        popular = " 🔥 ХИТ" if self.discount > 40 else ""
        return f"{self.emoji} {self.name} • {self.price}₽ • −{self.discount}%{popular}"

# ==================== ДАННЫЕ ====================

SERVERS = {
    "netherlands": Server(
        id="netherlands",
        name="Нидерланды",
        flag="🇳🇱",
        city="Амстердам",
        load=32,
        ping=45
    ),
    "usa": Server(
        id="usa",
        name="США",
        flag="🇺🇸",
        city="Нью-Йорк",
        load=45,
        ping=120
    ),
    "germany": Server(
        id="germany",
        name="Германия",
        flag="🇩🇪",
        city="Франкфурт",
        load=28,
        ping=55
    ),
    "uk": Server(
        id="uk",
        name="Великобритания",
        flag="🇬🇧",
        city="Лондон",
        load=38,
        ping=65
    ),
    "singapore": Server(
        id="singapore",
        name="Сингапур",
        flag="🇸🇬",
        city="Сингапур",
        load=22,
        ping=150
    ),
    "japan": Server(
        id="japan",
        name="Япония",
        flag="🇯🇵",
        city="Токио",
        load=19,
        ping=180
    )
}

PLANS = {
    "1month": Plan(
        id="1month",
        name="1 месяц",
        days=30,
        price=299,
        old_price=499,
        emoji="🌱"
    ),
    "3month": Plan(
        id="3month",
        name="3 месяца",
        days=90,
        price=699,
        old_price=1197,
        emoji="🌿"
    ),
    "6month": Plan(
        id="6month",
        name="6 месяцев",
        days=180,
        price=1199,
        old_price=2394,
        emoji="🌳"
    ),
    "12month": Plan(
        id="12month",
        name="12 месяцев",
        days=365,
        price=1999,
        old_price=4788,
        emoji="🏝️"
    )
}

PROTOCOLS = ["OpenVPN", "WireGuard", "IKEv2"]
TRIAL_DAYS = 3

# ==================== КЭШИРОВАНИЕ ====================

class Cache:
    """Простой кэш с TTL"""
    
    def __init__(self, ttl: int = 60, maxsize: int = 1000):
        self.ttl = ttl
        self.maxsize = maxsize
        self._cache = {}
        self._timestamps = {}
    
    def get(self, key: str):
        if key in self._cache:
            if time.time() - self._timestamps[key] < self.ttl:
                return self._cache[key]
            else:
                self.delete(key)
        return None
    
    def set(self, key: str, value: Any):
        if len(self._cache) >= self.maxsize:
            oldest = min(self._timestamps.items(), key=lambda x: x[1])[0]
            self.delete(oldest)
        self._cache[key] = value
        self._timestamps[key] = time.time()
    
    def delete(self, key: str):
        self._cache.pop(key, None)
        self._timestamps.pop(key, None)
    
    def clear(self):
        self._cache.clear()
        self._timestamps.clear()

# Глобальные кэши
user_cache = Cache(ttl=30)
server_stats_cache = Cache(ttl=10)
promo_cache = Cache(ttl=300)

# ==================== БАЗА ДАННЫХ (АСИНХРОННАЯ) ====================

class Database:
    """Управление базой данных с пулом соединений"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._pool = asyncio.Queue()
        self._pool_size = 0
    
    async def init(self):
        """Инициализация БД и пула"""
        for _ in range(config.DB_POOL_SIZE):
            conn = await aiosqlite.connect(self.db_path)
            await self._setup_connection(conn)
            await self._pool.put(conn)
            self._pool_size += 1
        await self._create_tables()
        logger.info(f"✅ База данных инициализирована (пул: {self._pool_size})")
    
    async def _setup_connection(self, conn):
        """Настройка соединения"""
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.execute("PRAGMA cache_size = -2000")  # 2MB
        await conn.execute("PRAGMA foreign_keys = ON")
    
    async def _create_tables(self):
        """Создание таблиц"""
        async with self.get_connection() as conn:
            # 👤 Пользователи
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    subscribe_until TEXT,
                    trial_used INTEGER DEFAULT 0,
                    banned INTEGER DEFAULT 0,
                    selected_server TEXT DEFAULT 'netherlands',
                    selected_protocol TEXT DEFAULT 'OpenVPN',
                    total_traffic INTEGER DEFAULT 0,
                    last_active TIMESTAMP,
                    reg_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 🎫 Промокоды
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS promocodes (
                    code TEXT PRIMARY KEY,
                    days INTEGER,
                    uses_left INTEGER,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 📝 Использованные промокоды
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS used_promos (
                    user_id INTEGER,
                    code TEXT,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, code)
                )
            ''')
            
            # 📊 Статистика
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,
                    user_id INTEGER,
                    data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Индексы для скорости
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_subscribe ON users(subscribe_until)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_banned ON users(banned)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_stats_created ON stats(created_at)')
            
            await conn.commit()
    
    @asynccontextmanager
    async def get_connection(self):
        """Получение соединения из пула"""
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)
    
    async def execute(self, query: str, params: tuple = ()):
        """Выполнить запрос"""
        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor
    
    async def fetch_one(self, query: str, params: tuple = ()):
        """Получить одну запись"""
        async with self.get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def fetch_all(self, query: str, params: tuple = ()):
        """Получить все записи"""
        async with self.get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

# Инициализация БД
db = Database(config.DB_PATH)

# ==================== РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ====================

class UserManager:
    """Управление пользователями"""
    
    @staticmethod
    async def get(user_id: int) -> Optional[Dict]:
        """Получить пользователя (с кэшем)"""
        cache_key = f"user:{user_id}"
        cached = user_cache.get(cache_key)
        if cached:
            return cached
        
        user = await db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if user:
            user_cache.set(cache_key, user)
        return user
    
    @staticmethod
    async def create(user_id: int, username: str, first_name: str):
        """Создать пользователя"""
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_active) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, datetime.now().isoformat())
        )
        user_cache.delete(f"user:{user_id}")
    
    @staticmethod
    async def update_subscription(user_id: int, days: int, admin_give: bool = False) -> Optional[datetime]:
        """Обновить подписку"""
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
        user_cache.delete(f"user:{user_id}")
        return new_date
    
    @staticmethod
    async def update_server(user_id: int, server_id: str):
        """Обновить выбранный сервер"""
        await db.execute(
            "UPDATE users SET selected_server = ?, last_active = ? WHERE user_id = ?",
            (server_id, datetime.now().isoformat(), user_id)
        )
        user_cache.delete(f"user:{user_id}")
    
    @staticmethod
    async def update_protocol(user_id: int, protocol: str):
        """Обновить выбранный протокол"""
        await db.execute(
            "UPDATE users SET selected_protocol = ?, last_active = ? WHERE user_id = ?",
            (protocol, datetime.now().isoformat(), user_id)
        )
        user_cache.delete(f"user:{user_id}")
    
    @staticmethod
    async def ban(user_id: int):
        """Забанить пользователя"""
        await db.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
        user_cache.delete(f"user:{user_id}")
    
    @staticmethod
    async def unban(user_id: int):
        """Разбанить пользователя"""
        await db.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
        user_cache.delete(f"user:{user_id}")
    
    @staticmethod
    async def get_all() -> List[Dict]:
        """Получить всех пользователей"""
        return await db.fetch_all("SELECT * FROM users ORDER BY reg_date DESC")
    
    @staticmethod
    async def get_stats() -> Dict:
        """Получить статистику"""
        users = await UserManager.get_all()
        now = datetime.now()
        
        active = sum(1 for u in users if u.get("subscribe_until") and 
                    datetime.fromisoformat(u["subscribe_until"]) > now)
        banned = sum(1 for u in users if u.get("banned"))
        trial = sum(1 for u in users if u.get("trial_used"))
        
        return {
            "total": len(users),
            "active": active,
            "banned": banned,
            "trial": trial,
            "conversion": round(active / len(users) * 100, 1) if users else 0
        }

# ==================== ПРОМОКОДЫ ====================

class PromoManager:
    """Управление промокодами"""
    
    @staticmethod
    def generate_code(length: int = 8) -> str:
        """Генерация красивого промокода"""
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Без похожих символов
        return ''.join(secrets.choice(chars) for _ in range(length))
    
    @staticmethod
    async def create(days: int, uses: int, admin_id: int) -> Optional[str]:
        """Создать промокод"""
        code = PromoManager.generate_code()
        try:
            await db.execute(
                "INSERT INTO promocodes (code, days, uses_left, created_by) VALUES (?, ?, ?, ?)",
                (code, days, uses, admin_id)
            )
            promo_cache.clear()
            return code
        except:
            return None
    
    @staticmethod
    async def use(user_id: int, code: str) -> Tuple[bool, str]:
        """Активировать промокод"""
        code = code.upper()
        
        # Проверяем кэш
        cache_key = f"promo:{code}"
        cached = promo_cache.get(cache_key)
        if cached and cached.get("uses_left", 0) <= 0:
            return False, "❌ Промокод не найден"
        
        # Проверяем в БД
        promo = await db.fetch_one(
            "SELECT * FROM promocodes WHERE code = ? AND uses_left > 0", 
            (code,)
        )
        
        if not promo:
            return False, "❌ Промокод не найден"
        
        # Проверяем, не использовал ли уже
        used = await db.fetch_one(
            "SELECT * FROM used_promos WHERE user_id = ? AND code = ?", 
            (user_id, code)
        )
        
        if used:
            return False, "❌ Промокод уже использован"
        
        days = promo["days"]
        new_date = await UserManager.update_subscription(user_id, days, admin_give=True)
        
        if not new_date:
            return False, "❌ Ошибка активации"
        
        # Обновляем счетчик
        await db.execute(
            "UPDATE promocodes SET uses_left = uses_left - 1 WHERE code = ?", 
            (code,)
        )
        
        # Записываем использование
        await db.execute(
            "INSERT INTO used_promos (user_id, code) VALUES (?, ?)", 
            (user_id, code)
        )
        
        promo_cache.delete(cache_key)
        
        return True, f"✅ Промокод активирован! +{days} дней до {new_date.strftime('%d.%m.%Y')}"
    
    @staticmethod
    async def get_all() -> List[Dict]:
        """Получить все промокоды"""
        return await db.fetch_all("SELECT * FROM promocodes ORDER BY created_at DESC")

# ==================== ПРОВЕРКА ПОДПИСКИ ====================

class SubscriptionChecker:
    """Проверка подписки на каналы"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.channel_id = None
        self._channel_cache = Cache(ttl=3600)  # 1 час
    
    async def get_channel_id(self) -> Optional[int]:
        """Получить ID канала (с кэшем)"""
        cached = self._channel_cache.get("channel_id")
        if cached:
            return cached
        
        try:
            chat = await self.bot.get_chat(config.REQUIRED_CHANNEL)
            self._channel_cache.set("channel_id", chat.id)
            return chat.id
        except Exception as e:
            logger.error(f"Ошибка получения ID канала: {e}")
            return None
    
    async def check_user(self, user_id: int) -> Tuple[bool, List[str]]:
        """Проверить подписку пользователя"""
        try:
            channel_id = await self.get_channel_id()
            if not channel_id:
                return False, [config.REQUIRED_CHANNEL]
            
            member = await self.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in ["member", "administrator", "creator"]:
                return True, []
            return False, [config.REQUIRED_CHANNEL]
        except Exception as e:
            logger.error(f"Ошибка проверки подписки: {e}")
            return False, [config.REQUIRED_CHANNEL]

# ==================== КЛАВИАТУРЫ (ПРЕМИУМ ДИЗАЙН) ====================

class KeyboardBuilder:
    """Построитель красивых клавиатур"""
    
    @staticmethod
    def main(is_admin: bool = False) -> InlineKeyboardMarkup:
        """Главное меню"""
        buttons = [
            [InlineKeyboardButton("🛡️ ПОДКЛЮЧИТЬ VPN", callback_data="get_access")],
            [InlineKeyboardButton("🌍 ВЫБРАТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("📱 УСТРОЙСТВА", callback_data="my_devices")],
            [InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile")],
            [InlineKeyboardButton("🎁 ПРОМОКОД", callback_data="promo")],
            [InlineKeyboardButton("📞 ПОДДЕРЖКА", callback_data="support")]
        ]
        if is_admin:
            buttons.append([InlineKeyboardButton("⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def servers() -> InlineKeyboardMarkup:
        """Серверы с нагрузкой"""
        buttons = []
        for server in SERVERS.values():
            status = server.load_status.value
            buttons.append([InlineKeyboardButton(
                f"{server.flag} {server.name} • {status} {server.load}% • {server.ping}ms",
                callback_data=f"server_{server.id}"
            )])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def plans() -> InlineKeyboardMarkup:
        """Тарифы со скидками"""
        buttons = []
        for plan in PLANS.values():
            popular = " 🔥" if plan.discount > 40 else ""
            buttons.append([InlineKeyboardButton(
                f"{plan.emoji} {plan.name} • {plan.price}₽ • −{plan.discount}%{popular}",
                callback_data=f"buy_{plan.id}"
            )])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def protocols() -> InlineKeyboardMarkup:
        """Протоколы"""
        buttons = []
        for protocol in PROTOCOLS:
            buttons.append([InlineKeyboardButton(
                f"🔒 {protocol}", 
                callback_data=f"protocol_{protocol}"
            )])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def devices() -> InlineKeyboardMarkup:
        """Устройства"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 ANDROID", callback_data="device_android")],
            [InlineKeyboardButton("🍏 IOS", callback_data="device_ios")],
            [InlineKeyboardButton("💻 WINDOWS", callback_data="device_windows")],
            [InlineKeyboardButton("🍎 MACOS", callback_data="device_macos")],
            [InlineKeyboardButton("🐧 LINUX", callback_data="device_linux")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def subscription() -> InlineKeyboardMarkup:
        """Управление подпиской"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 ПРОДЛИТЬ", callback_data="get_access")],
            [InlineKeyboardButton("📥 СКАЧАТЬ КОНФИГ", callback_data="download_config")],
            [InlineKeyboardButton("🌍 СМЕНИТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def subscribe(channels: List[str]) -> InlineKeyboardMarkup:
        """Обязательная подписка"""
        buttons = []
        for ch in channels:
            buttons.append([InlineKeyboardButton(
                f"📢 ПОДПИСАТЬСЯ {ch}",
                url=f"https://t.me/{ch.replace('@', '')}"
            )])
        buttons.append([InlineKeyboardButton("✅ Я ПОДПИСАЛСЯ", callback_data="check_sub")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin() -> InlineKeyboardMarkup:
        """Админ панель"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ", callback_data="admin_users")],
            [InlineKeyboardButton("🎫 СОЗДАТЬ ПРОМОКОД", callback_data="admin_create_promo")],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats")],
            [InlineKeyboardButton("🎁 ВСЕ ПРОМОКОДЫ", callback_data="admin_promos")],
            [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data="admin_settings")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def back() -> InlineKeyboardMarkup:
        """Кнопка назад"""
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================

app = FastAPI(title="Premium VPN Bot API", version="2.0.0")
telegram_app = None
subscription_checker = None
security = HTTPBearer()

# ==================== ЗАВИСИМОСТИ ====================

async def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Проверка администратора"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        user_id = payload.get("sub")
        if not user_id or int(user_id) not in config.ADMIN_IDS:
            raise HTTPException(status_code=403, detail="Access denied")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=403, detail="Invalid token")

# ==================== FASTAPI ЭНДПОИНТЫ ====================

@app.on_event("startup")
async def startup():
    """Инициализация при запуске"""
    global telegram_app, subscription_checker
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║     🚀 ЗАПУСК PREMIUM VPN BOT v2.0          ║")
    logger.info("╚══════════════════════════════════════════════╝")
    
    # Инициализация БД
    await db.init()
    
    # Создание Telegram Application
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    
    subscription_checker = SubscriptionChecker(telegram_app.bot)
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Установка вебхука
    webhook_url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await telegram_app.bot.set_webhook(url=webhook_url)
    
    logger.info(f"✅ Вебхук: {webhook_url}")
    logger.info(f"✅ Админы: {config.ADMIN_IDS}")
    logger.info("✅ Бот готов к работе!")

@app.on_event("shutdown")
async def shutdown():
    """Остановка"""
    if telegram_app:
        await telegram_app.stop()

@app.post(config.WEBHOOK_PATH)
async def webhook(request: Request):
    """Вебхук для Telegram"""
    if not telegram_app:
        return {"ok": False, "error": "Bot not initialized"}
    
    json_data = await request.json()
    update = Update.de_json(json_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get(config.HEALTH_PATH)
async def health():
    """Проверка здоровья"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "uptime": time.time() - startup_time
    }

@app.get("/")
async def home():
    """Главная страница"""
    return {
        "service": "Premium VPN Bot",
        "version": "2.0.0",
        "status": "online",
        "documentation": "/docs"
    }

@app.get("/api/stats")
async def api_stats():
    """Публичная статистика"""
    stats = await UserManager.get_stats()
    return {
        "total_users": stats["total"],
        "active_users": stats["active"],
        "conversion": stats["conversion"]
    }

@app.get("/api/admin/users", dependencies=[Depends(verify_admin)])
async def api_users():
    """Список пользователей (админ)"""
    users = await UserManager.get_all()
    return {"users": users[:100]}  # Ограничиваем для безопасности

@app.post("/api/admin/promo", dependencies=[Depends(verify_admin)])
async def api_create_promo(days: int, uses: int, admin_id: int):
    """Создать промокод (админ)"""
    code = await PromoManager.create(days, uses, admin_id)
    if code:
        return {"success": True, "code": code}
    return {"success": False}

# ==================== ОБРАБОТЧИКИ TELEGRAM ====================

startup_time = time.time()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    # Создаем пользователя
    await UserManager.create(user.id, user.username or "", user.first_name or "")
    
    # Проверка на бан
    db_user = await UserManager.get(user.id)
    if db_user and db_user.get("banned"):
        await update.message.reply_text(
            "⛔ <b>ДОСТУП ЗАБЛОКИРОВАН</b>\n\n"
            "Обратитесь в поддержку: @vpn_support_bot",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Проверка подписки
    subscribed, channels = await subscription_checker.check_user(user.id)
    if not subscribed:
        text = (
            f"👋 <b>Привет, {user.first_name}!</b>\n\n"
            f"🔐 <b>Для доступа подпишись на канал:</b>"
        )
        await update.message.reply_text(
            text,
            reply_markup=KeyboardBuilder.subscribe(channels),
            parse_mode=ParseMode.HTML
        )
        return
    
    # Приветствие
    text = (
        f"🌟 <b>ДОБРО ПОЖАЛОВАТЬ В PREMIUM VPN!</b>\n\n"
        f"👤 <b>Пользователь:</b> {user.first_name}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n\n"
        f"⚡ <b>Доступно:</b>\n"
        f"• 🌍 6 серверов по всему миру\n"
        f"• 🔒 Высокая скорость\n"
        f"• 📱 Поддержка всех устройств\n"
        f"• 🎁 Пробный период 3 дня\n\n"
        f"👇 <b>Выбери действие:</b>"
    )
    
    is_admin = user.id in config.ADMIN_IDS
    await update.message.reply_text(
        text,
        reply_markup=KeyboardBuilder.main(is_admin),
        parse_mode=ParseMode.HTML
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    
    # Проверка подписки (кроме check_sub)
    if data != "check_sub":
        subscribed, channels = await subscription_checker.check_user(user_id)
        if not subscribed:
            await query.edit_message_text(
                "🔐 <b>ПОДПИШИСЬ НА КАНАЛ:</b>",
                reply_markup=KeyboardBuilder.subscribe(channels),
                parse_mode=ParseMode.HTML
            )
            return
    
    is_admin = user_id in config.ADMIN_IDS
    
    # ===== НАВИГАЦИЯ =====
    if data == "back_main":
        text = "🏠 <b>ГЛАВНОЕ МЕНЮ</b>"
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.main(is_admin),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "check_sub":
        subscribed, channels = await subscription_checker.check_user(user_id)
        if subscribed:
            user = await UserManager.get(user_id)
            text = f"🌟 <b>ДОБРО ПОЖАЛОВАТЬ!</b>\n\n✅ Подписка подтверждена"
            await query.edit_message_text(
                text,
                reply_markup=KeyboardBuilder.main(is_admin),
                parse_mode=ParseMode.HTML
            )
            await query.answer("✅ Подписка подтверждена!")
        else:
            await query.edit_message_text(
                "❌ <b>ПОДПИШИСЬ НА КАНАЛ:</b>",
                reply_markup=KeyboardBuilder.subscribe(channels),
                parse_mode=ParseMode.HTML
            )
    
    # ===== ОСНОВНЫЕ КНОПКИ =====
    elif data == "get_access":
        user = await UserManager.get(user_id)
        if user and user.get("subscribe_until"):
            try:
                end = datetime.fromisoformat(user["subscribe_until"])
                if end > datetime.now():
                    days = (end - datetime.now()).days
                    text = (
                        f"🔑 <b>У ВАС АКТИВНАЯ ПОДПИСКА</b>\n\n"
                        f"📅 Действует до: {end.strftime('%d.%m.%Y')}\n"
                        f"⏱ Осталось: {days} дн.\n\n"
                        f"Вы можете скачать конфиг или сменить сервер:"
                    )
                    await query.edit_message_text(
                        text,
                        reply_markup=KeyboardBuilder.subscription(),
                        parse_mode=ParseMode.HTML
                    )
                    return
            except:
                pass
        
        text = (
            "📦 <b>ВЫБЕРИТЕ ТАРИФ</b>\n\n"
            "🔥 Все тарифы включают:\n"
            "• 🌍 6 серверов\n"
            "• 📱 Все устройства\n"
            "• 🚀 Безлимитный трафик\n"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.plans(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "select_server":
        text = "🌍 <b>ВЫБЕРИТЕ СЕРВЕР</b>\n\n"
        text += "⬇️ <b>Нагрузка:</b> 🟢 <30% | 🟡 30-60% | 🔴 >60%\n"
        text += "⏱ <b>Пинг:</b> до сервера в ms\n"
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.servers(),
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("server_"):
        server_id = data.replace("server_", "")
        server = SERVERS[server_id]
        await UserManager.update_server(user_id, server_id)
        
        text = (
            f"✅ <b>СЕРВЕР ВЫБРАН</b>\n\n"
            f"{server.flag} <b>{server.name}</b>\n"
            f"🏙 Город: {server.city}\n"
            f"📊 Нагрузка: {server.load_status.value} {server.load}%\n"
            f"⏱ Пинг: {server.ping}ms\n\n"
            f"Теперь выберите протокол:"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.protocols(),
            parse_mode=ParseMode.HTML
        )
        await query.answer(f"✅ {server.display_name}")
    
    elif data.startswith("protocol_"):
        protocol = data.replace("protocol_", "")
        await UserManager.update_protocol(user_id, protocol)
        
        text = (
            f"✅ <b>НАСТРОЙКИ СОХРАНЕНЫ</b>\n\n"
            f"🔌 Протокол: <b>{protocol}</b>\n"
            f"🌍 Сервер сохранен\n\n"
            f"Теперь вы можете скачать конфиг в профиле"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.main(is_admin),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "my_devices":
        text = (
            "📱 <b>НАСТРОЙКА УСТРОЙСТВ</b>\n\n"
            "Выберите ваше устройство для получения инструкции:"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.devices(),
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("device_"):
        device = data.replace("device_", "")
        instructions = {
            "android": (
                "📱 <b>НАСТРОЙКА ANDROID</b>\n\n"
                "1️⃣ Установите <b>OpenVPN Connect</b> из Play Market\n"
                "2️⃣ Скачайте конфиг-файл (кнопка в профиле)\n"
                "3️⃣ Откройте файл → Импорт → Подключитесь\n\n"
                "✅ Готово!"
            ),
            "ios": (
                "🍏 <b>НАСТРОЙКА iOS</b>\n\n"
                "1️⃣ Установите <b>OpenVPN Connect</b> из App Store\n"
                "2️⃣ Скачайте конфиг-файл\n"
                "3️⃣ Откройте → Импорт → Подключитесь\n\n"
                "✅ Готово!"
            ),
            "windows": (
                "💻 <b>НАСТРОЙКА WINDOWS</b>\n\n"
                "1️⃣ Скачайте <b>OpenVPN GUI</b> с openvpn.net\n"
                "2️⃣ Установите программу\n"
                "3️⃣ Поместите конфиг в папку config\n"
                "4️⃣ Запустите от имени администратора\n\n"
                "✅ Готово!"
            ),
            "macos": (
                "🍎 <b>НАСТРОЙКА macOS</b>\n\n"
                "1️⃣ Установите <b>Tunnelblick</b>\n"
                "2️⃣ Скачайте конфиг-файл\n"
                "3️⃣ Дважды кликните по файлу\n"
                "4️⃣ Подтвердите подключение\n\n"
                "✅ Готово!"
            ),
            "linux": (
                "🐧 <b>НАСТРОЙКА LINUX</b>\n\n"
                "1️⃣ Установите openvpn:\n"
                "   <code>sudo apt install openvpn</code>\n"
                "2️⃣ Скачайте конфиг-файл\n"
                "3️⃣ Запустите:\n"
                "   <code>sudo openvpn --config config.ovpn</code>\n\n"
                "✅ Готово!"
            )
        }
        await query.edit_message_text(
            instructions.get(device, "Инструкция готовится"),
            reply_markup=KeyboardBuilder.devices(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "profile":
        user = await UserManager.get(user_id)
        
        if user and user.get("subscribe_until"):
            try:
                end = datetime.fromisoformat(user["subscribe_until"])
                days = (end - datetime.now()).days
                status = "✅ АКТИВНА" if days > 0 else "❌ ИСТЕКЛА"
                end_str = end.strftime("%d.%m.%Y")
            except:
                days = 0
                status = "❌ ОШИБКА"
                end_str = "—"
        else:
            days = 0
            status = "❌ НЕТ ПОДПИСКИ"
            end_str = "—"
        
        server = SERVERS.get(user.get("selected_server", "netherlands"))
        protocol = user.get("selected_protocol", "OpenVPN")
        
        text = (
            f"👤 <b>ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"📅 <b>Действует до:</b> {end_str}\n"
            f"⏱ <b>Осталось:</b> {max(0, days)} дн.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌍 <b>Текущий сервер:</b>\n"
            f"   {server.display_name}\n"
            f"   🏙 {server.city}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔌 <b>Протокол:</b> {protocol}\n"
        )
        
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.back(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "promo":
        text = (
            "🎁 <b>АКТИВАЦИЯ ПРОМОКОДА</b>\n\n"
            "Отправьте промокод в чат.\n\n"
            "📌 <b>Формат:</b> просто код (например SUMMER2025)\n"
            "⏳ Промокод активируется мгновенно\n\n"
            "<i>Ожидаю ввод...</i>"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.back(),
            parse_mode=ParseMode.HTML
        )
        context.user_data['awaiting_promo'] = True
    
    elif data == "support":
        text = (
            "📞 <b>СЛУЖБА ПОДДЕРЖКИ</b>\n\n"
            "👤 <b>Telegram:</b> @vpn_support_bot\n"
            "📧 <b>Email:</b> support@vpnbot.com\n\n"
            "⏱ <b>Время ответа:</b> до 2 часов\n"
            "🕒 <b>Режим работы:</b> 24/7\n\n"
            "Или напишите в чат — мы ответим!"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.back(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "download_config":
        user = await UserManager.get(user_id)
        if user and user.get("subscribe_until"):
            try:
                end = datetime.fromisoformat(user["subscribe_until"])
                if end > datetime.now():
                    server = SERVERS[user.get("selected_server", "netherlands")]
                    protocol = user.get("selected_protocol", "OpenVPN")
                    
                    config_text = f"""# PREMIUM VPN CONFIG
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# User ID: {user_id}
# Server: {server.name} ({server.city})
# Protocol: {protocol}

client
dev tun
proto {'udp' if protocol == 'WireGuard' else 'tcp'}
remote {server.city.lower()}.premium-vpn.com 1194
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-CBC
verb 3

# Premium VPN - Быстрый и безопасный доступ"""
                    
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=config_text.encode(),
                        filename=f"premium_{server.city.lower()}.ovpn",
                        caption=(
                            f"✅ <b>КОНФИГ ДЛЯ {server.name.upper()}</b>\n\n"
                            f"📍 Сервер: {server.city}\n"
                            f"🔌 Протокол: {protocol}\n"
                            f"📅 Действует до: {end.strftime('%d.%m.%Y')}\n\n"
                            f"Инструкция в разделе УСТРОЙСТВА"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                    await query.answer("✅ Конфиг отправлен!")
                    return
            except:
                pass
        
        await query.edit_message_text(
            "❌ <b>ПОДПИСКА НЕ АКТИВНА</b>\n\nПриобретите доступ в меню:",
            reply_markup=KeyboardBuilder.plans(),
            parse_mode=ParseMode.HTML
        )
    
    # ===== АДМИН КНОПКИ =====
    elif data == "admin_menu" and is_admin:
        text = (
            "⚙️ <b>АДМИН ПАНЕЛЬ</b>\n\n"
            "Выберите действие:"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.admin(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "admin_users" and is_admin:
        users = await UserManager.get_all()
        stats = await UserManager.get_stats()
        
        text = (
            f"👥 <b>ПОЛЬЗОВАТЕЛИ ({stats['total']})</b>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"   ✅ Активных: {stats['active']}\n"
            f"   🔒 Забанено: {stats['banned']}\n"
            f"   📈 Конверсия: {stats['conversion']}%\n\n"
            f"👤 <b>Последние 10:</b>\n"
        )
        
        for u in users[:10]:
            name = u.get('first_name', '—')[:15]
            status = "🔴" if u.get('banned') else "🟢"
            sub = "✅" if u.get('subscribe_until') and datetime.fromisoformat(u['subscribe_until']) > datetime.now() else "❌"
            text += f"{status}{sub} {name} (@{u.get('username', '—')})\n"
        
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.admin(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "admin_stats" and is_admin:
        stats = await UserManager.get_stats()
        
        text = (
            f"📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Всего пользователей:</b> {stats['total']}\n"
            f"✅ <b>Активных:</b> {stats['active']}\n"
            f"🔒 <b>Забанено:</b> {stats['banned']}\n"
            f"🎁 <b>Пробный период:</b> {stats['trial']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Конверсия:</b> {stats['conversion']}%\n"
        )
        
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.admin(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "admin_create_promo" and is_admin:
        text = (
            "🎫 <b>СОЗДАНИЕ ПРОМОКОДА</b>\n\n"
            "Отправьте в чат:\n"
            "<code>дней количество_использований</code>\n\n"
            "📌 <b>Пример:</b> <code>30 10</code>\n"
            "   • 30 дней подписки\n"
            "   • 10 использований\n\n"
            "<i>Ожидаю ввод...</i>"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.admin(),
            parse_mode=ParseMode.HTML
        )
        context.user_data['awaiting_promo_create'] = True
    
    elif data == "admin_promos" and is_admin:
        promos = await PromoManager.get_all()
        
        if not promos:
            text = "📋 <b>ПРОМОКОДОВ ПОКА НЕТ</b>"
        else:
            text = "🎫 <b>ВСЕ ПРОМОКОДЫ</b>\n\n"
            for p in promos[:10]:
                text += f"🎟 <code>{p['code']}</code> — {p['days']} дн., осталось {p['uses_left']}\n"
        
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.admin(),
            parse_mode=ParseMode.HTML
        )
    
    elif data == "admin_settings" and is_admin:
        text = (
            "⚙️ <b>НАСТРОЙКИ БОТА</b>\n\n"
            f"🔑 <b>Токен:</b> {config.BOT_TOKEN[:10]}...\n"
            f"👑 <b>Админы:</b> {config.ADMIN_IDS}\n"
            f"📢 <b>Канал:</b> {config.REQUIRED_CHANNEL}\n"
            f"🕒 <b>Аптайм:</b> {int(time.time() - startup_time)} сек\n\n"
            f"⚡ <b>Кэш:</b>\n"
            f"   • Пользователи: {user_cache._cache.__sizeof__()} байт\n"
            f"   • Промокоды: {promo_cache._cache.__sizeof__()} байт"
        )
        await query.edit_message_text(
            text,
            reply_markup=KeyboardBuilder.admin(),
            parse_mode=ParseMode.HTML
        )

# ==================== ОБРАБОТКА ТЕКСТА ====================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Обработка промокодов
    if context.user_data.get('awaiting_promo'):
        del context.user_data['awaiting_promo']
        
        # Проверяем подписку
        subscribed, channels = await subscription_checker.check_user(user_id)
        if not subscribed:
            await update.message.reply_text(
                "🔐 Сначала подпишись на канал!",
                reply_markup=KeyboardBuilder.subscribe(channels)
            )
            return
        
        # Активируем промокод
        success, msg = await PromoManager.use(user_id, text)
        await update.message.reply_text(msg)
        
        # Если успешно, показываем главное меню
        if success:
            is_admin = user_id in config.ADMIN_IDS
            await update.message.reply_text(
                "🌟 ГЛАВНОЕ МЕНЮ",
                reply_markup=KeyboardBuilder.main(is_admin)
            )
        return
    
    # Создание промокода админом
    if context.user_data.get('awaiting_promo_create') and user_id in config.ADMIN_IDS:
        del context.user_data['awaiting_promo_create']
        try:
            days, uses = map(int, text.split())
            code = await PromoManager.create(days, uses, user_id)
            if code:
                await update.message.reply_text(
                    f"✅ <b>ПРОМОКОД СОЗДАН!</b>\n\n"
                    f"🎟 <code>{code}</code>\n"
                    f"📅 Дней: {days}\n"
                    f"🎫 Использований: {uses}",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Ошибка создания промокода")
        except Exception as e:
            await update.message.reply_text(
                "❌ Неверный формат. Нужно: <code>30 10</code>",
                parse_mode=ParseMode.HTML
            )
        return

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "render_bot_pro:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )

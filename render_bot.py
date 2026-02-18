#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         🌟 PLES VPN BOT v17.0 - С БАЛАНСОМ                    ║
║     Баланс в профиле • Пополнение • Автосписание              ║
║     Без 3x-UI • Система тикетов • Рефералы                    ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import asyncio
import logging
import secrets
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from collections import defaultdict

from fastapi import FastAPI, Request
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
import aiosqlite
import requests
import aiohttp
import qrcode
from qrcode.image.pure import PyPNGImage

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    # Telegram
    BOT_TOKEN = "8514511524:AAH9_bCmQYOaB29ajeFn_vlad3BSVpcUUIA"
    ADMIN_IDS = [8443743937]
    TESTER_IDS = []
    REQUIRED_CHANNEL = "@numberbor"
    BOT_USERNAME = "Playinc_bot"
    
    # Группа для тикетов - ЗАМЕНИТЕ НА ID ВАШЕЙ ГРУППЫ
    TICKET_GROUP_ID = -1003880204174  # ID вашей группы
    
    # CryptoBot
    CRYPTOBOT_TOKEN = "533707:AAyjZJjRSCxePyVGl6WYFx3rfWqgxZLhjvi"
    CRYPTOBOT_API = "https://pay.crypt.bot/api"
    
    # База данных
    DB_PATH = "/tmp/ples_vpn.db"
    
    # Пробный период
    TRIAL_DAYS = 6
    
    # Реферальная система
    REFERRAL_BONUS_DAYS = 3
    REFERRAL_BONUS_PERCENT = 10  # 10% от пополнений рефералов
    
    # URL
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://ples-vpn.onrender.com")
    WEBHOOK_PATH = "/webhook"
    
    # Настройки автоудаления
    AUTO_DELETE_USER_MESSAGES = 60
    AUTO_DELETE_BOT_MESSAGES = 60
    AUTO_DELETE_ORDER = 1800
    
    # Настройки пинга
    PING_INTERVAL = 300
    
    # Настройки для тестеров
    TESTER_ACTION_LIMIT = 10
    TESTER_ACTION_WINDOW = 3600
    TESTER_DELETE_LIMIT = 5
    TESTER_DELETE_WINDOW = 86400
    
    # Флаги работы бота
    BOT_ENABLED = True
    MAINTENANCE_MODE = False
    MAINTENANCE_MESSAGE = "🔧 <b>Ведутся технические работы</b>\n\nБот временно недоступен. Приносим извинения за неудобства.\nОриентировочное время окончания: скоро."

config = Config()

# ==================== СИСТЕМА МОНИТОРИНГА ТЕСТЕРОВ ====================

class TesterMonitor:
    def __init__(self):
        self.actions = defaultdict(list)
        self.deletions = defaultdict(list)
        self.warnings = defaultdict(int)
    
    def log_action(self, user_id: int):
        now = time.time()
        self.actions[user_id].append(now)
        self.actions[user_id] = [t for t in self.actions[user_id] if now - t < config.TESTER_ACTION_WINDOW]
    
    def log_deletion(self, user_id: int):
        now = time.time()
        self.deletions[user_id].append(now)
        self.deletions[user_id] = [t for t in self.deletions[user_id] if now - t < config.TESTER_DELETE_WINDOW]
    
    def check_action_limit(self, user_id: int) -> Tuple[bool, str]:
        count = len(self.actions[user_id])
        if count >= config.TESTER_ACTION_LIMIT:
            return False, f"⚠️ Лимит действий ({config.TESTER_ACTION_LIMIT} в час) исчерпан"
        return True, f"✅ Осталось действий: {config.TESTER_ACTION_LIMIT - count}"
    
    def check_delete_limit(self, user_id: int) -> Tuple[bool, str]:
        count = len(self.deletions[user_id])
        if count >= config.TESTER_DELETE_LIMIT:
            return False, f"⚠️ Лимит удалений ({config.TESTER_DELETE_LIMIT} в день) исчерпан"
        return True, f"✅ Осталось удалений: {config.TESTER_DELETE_LIMIT - count}"
    
    def add_warning(self, user_id: int) -> int:
        self.warnings[user_id] += 1
        return self.warnings[user_id]
    
    def should_remove_tester(self, user_id: int) -> bool:
        if len(self.deletions[user_id]) >= config.TESTER_DELETE_LIMIT:
            return True
        if self.warnings[user_id] >= 3:
            return True
        return False
    
    def reset_tester(self, user_id: int):
        self.actions.pop(user_id, None)
        self.deletions.pop(user_id, None)
        self.warnings.pop(user_id, None)

tester_monitor = TesterMonitor()

# ==================== МЕНЕДЖЕР ПИНГА ====================

class KeepAlive:
    def __init__(self):
        self.ping_url = config.BASE_URL
        self.session = None
        self.ping_count = 0
    
    async def initialize(self):
        self.session = aiohttp.ClientSession()
        logger.info("🔄 Сессия для пинга создана")
    
    async def ping_self(self):
        while True:
            try:
                await asyncio.sleep(config.PING_INTERVAL)
                self.ping_count += 1
                async with self.session.get(f"{self.ping_url}/health") as response:
                    if response.status == 200:
                        logger.info(f"🏓 Пинг #{self.ping_count}: сервер жив")
                    else:
                        logger.warning(f"⚠️ Пинг #{self.ping_count}: ответ {response.status}")
            except Exception as e:
                logger.error(f"❌ Ошибка пинга: {e}")
    
    async def cleanup(self):
        if self.session:
            await self.session.close()
            logger.info("🔄 Сессия пинга закрыта")

keep_alive = KeepAlive()

# ==================== CRYPTOBOT КЛИЕНТ ====================

class CryptoPay:
    def __init__(self, token: str):
        self.token = token
        self.api_url = config.CRYPTOBOT_API
        self.headers = {
            "Crypto-Pay-API-Token": token,
            "Content-Type": "application/json"
        }
    
    async def check_connection(self) -> bool:
        try:
            url = f"{self.api_url}/getMe"
            response = await asyncio.to_thread(
                requests.get, url, headers=self.headers, timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    app_info = result.get("result", {})
                    logger.info(f"✅ CryptoBot доступен: {app_info.get('app_name')}")
                    return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к CryptoBot: {e}")
            return False
    
    async def create_invoice(self, amount_rub: float, payload: str) -> Optional[Dict]:
        try:
            if amount_rub <= 0:
                return None
            
            url = f"{self.api_url}/createInvoice"
            data = {
                "asset": "USDT",
                "amount": str(amount_rub),
                "currency_type": "fiat",
                "fiat": "RUB",
                "accepted_assets": "USDT,TON,BTC",
                "description": f"Пополнение баланса на {amount_rub} RUB",
                "payload": payload,
                "expires_in": 3600,
                "allow_comments": False,
                "allow_anonymous": False
            }
            
            response = await asyncio.to_thread(
                requests.post, url, headers=self.headers, json=data, timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    return result["result"]
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return None
    
    async def get_invoice_status(self, invoice_id: int) -> Optional[str]:
        try:
            url = f"{self.api_url}/getInvoices"
            params = {"invoice_ids": str(invoice_id)}
            
            response = await asyncio.to_thread(
                requests.get, url, headers=self.headers, params=params, timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok") and result.get("result", {}).get("items"):
                    items = result["result"]["items"]
                    if items:
                        return items[0].get("status")
            return None
        except Exception as e:
            logger.error(f"Ошибка получения статуса: {e}")
            return None
    
    async def check_payment(self, invoice_id: int) -> bool:
        status = await self.get_invoice_status(invoice_id)
        return status == "paid"

crypto = CryptoPay(config.CRYPTOBOT_TOKEN)

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False
    
    async def init(self):
        try:
            logger.info("📦 Создание базы данных...")
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode = WAL")
                await db.execute("PRAGMA foreign_keys = ON")
                
                # 👤 Таблица пользователей с балансом
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        subscribe_until TEXT,
                        balance INTEGER DEFAULT 0,
                        trial_used INTEGER DEFAULT 0,
                        banned INTEGER DEFAULT 0,
                        role TEXT DEFAULT 'user',
                        selected_server TEXT DEFAULT 'netherlands',
                        selected_protocol TEXT DEFAULT 'OpenVPN',
                        referred_by INTEGER,
                        referral_code TEXT UNIQUE,
                        referral_count INTEGER DEFAULT 0,
                        referral_earnings INTEGER DEFAULT 0,
                        last_active TEXT,
                        last_message_id INTEGER,
                        profile_photo TEXT,
                        reg_date TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 👥 Таблица рефералов
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS referrals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        referrer_id INTEGER,
                        referred_id INTEGER UNIQUE,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (referrer_id) REFERENCES users(user_id) ON DELETE CASCADE,
                        FOREIGN KEY (referred_id) REFERENCES users(user_id) ON DELETE CASCADE
                    )
                ''')
                
                # 💳 Таблица платежей
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS crypto_payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        invoice_id INTEGER UNIQUE,
                        amount_rub INTEGER,
                        status TEXT DEFAULT 'pending',
                        payload TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        paid_at TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                    )
                ''')
                
                # 📊 Таблица транзакций баланса
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS balance_transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        amount INTEGER,
                        type TEXT,
                        description TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                    )
                ''')
                
                # 📊 Таблица контента
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS content (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 💰 Таблица тарифов
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS plans (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        days INTEGER,
                        price INTEGER,
                        emoji TEXT,
                        enabled INTEGER DEFAULT 1,
                        description TEXT,
                        photo_id TEXT,
                        service_type TEXT DEFAULT 'vpn',
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 🏷️ Таблица типов услуг
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS service_types (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        emoji TEXT,
                        description TEXT,
                        icon TEXT,
                        enabled INTEGER DEFAULT 1,
                        sort_order INTEGER DEFAULT 0
                    )
                ''')
                
                # 📋 Таблица фото для меню
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS menu_photos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        menu_key TEXT UNIQUE,
                        photo_id TEXT,
                        description TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # 📋 Таблица тикетов
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS tickets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        username TEXT,
                        first_name TEXT,
                        subject TEXT,
                        message TEXT,
                        status TEXT DEFAULT 'open',
                        admin_id INTEGER,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        closed_at TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                    )
                ''')
                
                # 📋 Таблица ответов на тикеты
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS ticket_replies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticket_id INTEGER,
                        user_id INTEGER,
                        message TEXT,
                        is_admin BOOLEAN DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                    )
                ''')
                
                # 📋 Таблица логов
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS maintenance_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action TEXT,
                        admin_id INTEGER,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                await db.commit()
                await self._init_default_data(db)
                
                self._initialized = True
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка создания БД: {e}")
            return False
    
    async def _init_default_data(self, db):
        try:
            # Добавляем типы услуг
            services = [
                ("vpn", "VPN", "🌍", "Быстрый и безопасный VPN", "🛡️", 1, 1),
                ("proxy_tg", "Прокси для Telegram", "📱", "Обход блокировок Telegram", "🔌", 1, 2),
                ("antijammer", "Антиглушилки", "📡", "Защита от глушилок", "🛜", 1, 3),
                ("website", "Для сайтов", "🌐", "Доступ к сайтам", "🔓", 1, 4)
            ]
            
            for s in services:
                await db.execute('''
                    INSERT OR IGNORE INTO service_types (id, name, emoji, description, icon, enabled, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', s)
            
            # Добавляем тарифы
            plans = [
                ("vpn_1month", "🌱 1 месяц", 30, 299, "🌱", 1, "Базовый тариф", None, "vpn"),
                ("vpn_3month", "🌿 3 месяца", 90, 699, "🌿", 1, "Популярный тариф", None, "vpn"),
                ("vpn_6month", "🌳 6 месяцев", 180, 1199, "🌳", 1, "Выгодный тариф", None, "vpn"),
                ("vpn_12month", "🏝️ 12 месяцев", 365, 1999, "🏝️", 1, "Максимальный тариф", None, "vpn")
            ]
            
            for p in plans:
                await db.execute('''
                    INSERT OR IGNORE INTO plans (id, name, days, price, emoji, enabled, description, photo_id, service_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', p)
            
            # Добавляем приветственный текст
            welcome = ("welcome_text", "🌟 <b>Ples VPN</b>\n\nВыберите услугу:")
            await db.execute('INSERT OR IGNORE INTO content (key, value) VALUES (?, ?)', welcome)
            
            # Добавляем начальные фото для меню
            menu_items = [
                ("main_menu", None, "Главное меню"),
                ("profile", None, "Профиль"),
                ("services", None, "Услуги"),
                ("support", None, "Поддержка")
            ]
            
            for key, photo, desc in menu_items:
                await db.execute('''
                    INSERT OR IGNORE INTO menu_photos (menu_key, photo_id, description) 
                    VALUES (?, ?, ?)
                ''', (key, photo, desc))
            
            await db.commit()
            
        except Exception as e:
            logger.error(f"Ошибка инициализации данных: {e}")
    
    async def ensure_initialized(self):
        if not self._initialized:
            return await self.init()
        return True
    
    async def execute(self, query: str, params: tuple = ()):
        try:
            await self.ensure_initialized()
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(query, params)
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка execute: {e}")
    
    async def fetch_one(self, query: str, params: tuple = ()):
        try:
            await self.ensure_initialized()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(query, params)
                row = await cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            return None
    
    async def fetch_all(self, query: str, params: tuple = ()):
        try:
            await self.ensure_initialized()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(query, params)
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            return []

db = Database(config.DB_PATH)

# ==================== МЕНЕДЖЕР ПОЛЬЗОВАТЕЛЕЙ ====================

class UserManager:
    @staticmethod
    async def get(user_id: int) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    
    @staticmethod
    async def get_all_users() -> List[Dict]:
        return await db.fetch_all("SELECT * FROM users ORDER BY reg_date DESC")
    
    @staticmethod
    async def get_by_referral_code(code: str) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM users WHERE referral_code = ?", (code,))
    
    @staticmethod
    async def get_role(user_id: int) -> str:
        user = await UserManager.get(user_id)
        if user:
            return user.get("role", "user")
        if user_id in config.ADMIN_IDS:
            return "admin"
        if user_id in config.TESTER_IDS:
            return "tester"
        return "user"
    
    @staticmethod
    async def set_role(user_id: int, role: str):
        await db.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
    
    @staticmethod
    async def add_tester(user_id: int):
        if user_id not in config.TESTER_IDS:
            config.TESTER_IDS.append(user_id)
        await UserManager.set_role(user_id, "tester")
    
    @staticmethod
    async def remove_tester(user_id: int):
        if user_id in config.TESTER_IDS:
            config.TESTER_IDS.remove(user_id)
        await UserManager.set_role(user_id, "user")
        tester_monitor.reset_tester(user_id)
    
    @staticmethod
    async def create(user_id: int, username: str, first_name: str, referred_by: int = None):
        existing = await UserManager.get(user_id)
        if existing:
            return existing
        
        role = "user"
        if user_id in config.ADMIN_IDS:
            role = "admin"
        elif user_id in config.TESTER_IDS:
            role = "tester"
        
        referral_code = str(user_id)
        
        await db.execute(
            """INSERT INTO users 
               (user_id, username, first_name, referred_by, referral_code, last_active, role, balance) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (user_id, username, first_name, referred_by, referral_code, datetime.now().isoformat(), role)
        )
        
        if referred_by and referred_by != user_id:
            await db.execute(
                "INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                (referred_by, user_id)
            )
            await db.execute(
                "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?",
                (referred_by,)
            )
        
        return await UserManager.get(user_id)
    
    @staticmethod
    async def save_message_id(user_id: int, message_id: int):
        await db.execute("UPDATE users SET last_message_id = ? WHERE user_id = ?", (message_id, user_id))
    
    @staticmethod
    async def save_profile_photo(user_id: int, photo_id: str):
        await db.execute(
            "UPDATE users SET profile_photo = ? WHERE user_id = ?",
            (photo_id, user_id)
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
    async def get_balance(user_id: int) -> int:
        """Получить баланс пользователя"""
        user = await UserManager.get(user_id)
        return user.get("balance", 0) if user else 0
    
    @staticmethod
    async def add_balance(user_id: int, amount: int, description: str = "Пополнение баланса"):
        """Добавить средства на баланс"""
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.execute(
            "INSERT INTO balance_transactions (user_id, amount, type, description) VALUES (?, ?, 'deposit', ?)",
            (user_id, amount, description)
        )
        
        # Начисляем бонус рефереру
        user = await UserManager.get(user_id)
        if user and user.get("referred_by"):
            referrer_id = user["referred_by"]
            bonus = int(amount * config.REFERRAL_BONUS_PERCENT / 100)
            if bonus > 0:
                await UserManager.add_balance(referrer_id, bonus, f"Реферальный бонус от пользователя {user_id}")
                await db.execute(
                    "UPDATE users SET referral_earnings = referral_earnings + ? WHERE user_id = ?",
                    (bonus, referrer_id)
                )
        
        logger.info(f"💰 Баланс пользователя {user_id} пополнен на {amount} RUB")
        return True
    
    @staticmethod
    async def spend_balance(user_id: int, amount: int, description: str) -> bool:
        """Списать средства с баланса"""
        balance = await UserManager.get_balance(user_id)
        if balance < amount:
            return False
        
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.execute(
            "INSERT INTO balance_transactions (user_id, amount, type, description) VALUES (?, ?, 'spend', ?)",
            (user_id, -amount, description)
        )
        return True
    
    @staticmethod
    async def get_transactions(user_id: int, limit: int = 10) -> List[Dict]:
        """Получить историю транзакций"""
        return await db.fetch_all(
            "SELECT * FROM balance_transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        )
    
    @staticmethod
    async def give_subscription(user_id: int, days: int, admin_give: bool = False):
        user = await UserManager.get(user_id)
        if not user:
            return None
        
        if user.get("subscribe_until") and not admin_give:
            try:
                old = datetime.fromisoformat(user["subscribe_until"])
                new = old + timedelta(days=days)
            except:
                new = datetime.now() + timedelta(days=days)
        else:
            new = datetime.now() + timedelta(days=days)
        
        await db.execute(
            "UPDATE users SET subscribe_until = ?, last_active = ? WHERE user_id = ?",
            (new.isoformat(), datetime.now().isoformat(), user_id)
        )
        return new
    
    @staticmethod
    async def buy_subscription(user_id: int, plan_id: str, plan_price: int, plan_days: int) -> Tuple[bool, str]:
        """Покупка подписки за баланс"""
        balance = await UserManager.get_balance(user_id)
        if balance < plan_price:
            return False, f"❌ Недостаточно средств. Нужно: {plan_price}₽, у вас: {balance}₽"
        
        success = await UserManager.spend_balance(user_id, plan_price, f"Покупка подписки {plan_id}")
        if not success:
            return False, "❌ Ошибка списания средств"
        
        new_date = await UserManager.give_subscription(user_id, plan_days)
        return True, f"✅ Подписка активирована до {new_date.strftime('%d.%m.%Y')}"
    
    @staticmethod
    async def ban_user(user_id: int):
        await db.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
    
    @staticmethod
    async def unban_user(user_id: int):
        await db.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
    
    @staticmethod
    async def get_stats() -> Dict:
        users = await UserManager.get_all_users()
        total = len(users)
        active = banned = trial = testers = admins = 0
        total_balance = 0
        
        for u in users:
            if u.get("role") == "admin":
                admins += 1
            if u.get("role") == "tester":
                testers += 1
            if u.get("banned"):
                banned += 1
            if u.get("trial_used"):
                trial += 1
            if u.get("subscribe_until"):
                try:
                    if datetime.fromisoformat(u["subscribe_until"]) > datetime.now():
                        active += 1
                except:
                    pass
            total_balance += u.get("balance", 0)
        
        return {
            "total": total, "active": active, "banned": banned,
            "trial": trial, "testers": testers, "admins": admins,
            "total_balance": total_balance,
            "conversion": round(active / total * 100, 1) if total else 0
        }
    
    @staticmethod
    async def save_crypto_payment(user_id: int, invoice_id: int, amount_rub: int, payload: str):
        await db.execute(
            "INSERT INTO crypto_payments (user_id, invoice_id, amount_rub, payload) VALUES (?, ?, ?, ?)",
            (user_id, invoice_id, amount_rub, payload)
        )
    
    @staticmethod
    async def confirm_crypto_payment(invoice_id: int):
        await db.execute(
            "UPDATE crypto_payments SET status = 'paid', paid_at = ? WHERE invoice_id = ?",
            (datetime.now().isoformat(), invoice_id)
        )
    
    @staticmethod
    async def get_pending_payments():
        return await db.fetch_all(
            "SELECT * FROM crypto_payments WHERE status = 'pending' AND datetime(created_at) > datetime('now', '-1 day')"
        )
    
    @staticmethod
    async def log_maintenance(action: str, admin_id: int):
        await db.execute(
            "INSERT INTO maintenance_log (action, admin_id) VALUES (?, ?)",
            (action, admin_id)
        )
    
    @staticmethod
    async def create_ticket(user_id: int, subject: str, message: str) -> int:
        """Создание нового тикета"""
        user = await UserManager.get(user_id)
        await db.execute(
            """INSERT INTO tickets 
               (user_id, username, first_name, subject, message) 
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, user.get('username'), user.get('first_name'), subject, message)
        )
        async with aiosqlite.connect(config.DB_PATH) as conn:
            cursor = await conn.execute("SELECT last_insert_rowid()")
            row = await cursor.fetchone()
            return row[0] if row else None
    
    @staticmethod
    async def close_ticket(ticket_id: int, admin_id: int):
        """Закрыть тикет"""
        await db.execute(
            "UPDATE tickets SET status = 'closed', admin_id = ?, closed_at = ? WHERE id = ?",
            (admin_id, datetime.now().isoformat(), ticket_id)
        )
    
    @staticmethod
    async def add_ticket_reply(ticket_id: int, user_id: int, message: str, is_admin: bool = False):
        """Добавить ответ в тикет"""
        await db.execute(
            "INSERT INTO ticket_replies (ticket_id, user_id, message, is_admin) VALUES (?, ?, ?, ?)",
            (ticket_id, user_id, message, 1 if is_admin else 0)
        )
    
    @staticmethod
    async def give_service_subscription(user_id: int, service_type: str, admin_give: bool = False):
        """Выдача подписки на конкретную услугу"""
        days_map = {
            "vpn": 30,
            "proxy": 30,
            "antijammer": 30,
            "website": 30
        }
        days = days_map.get(service_type, 30)
        return await UserManager.give_subscription(user_id, days, admin_give)

# ==================== МЕНЕДЖЕР КОНТЕНТА ====================

class ContentManager:
    @staticmethod
    async def get_welcome_text() -> str:
        content = await db.fetch_one("SELECT value FROM content WHERE key = 'welcome_text'")
        return content["value"] if content else "🌟 Добро пожаловать!"
    
    @staticmethod
    async def update_welcome_text(text: str):
        await db.execute(
            "INSERT OR REPLACE INTO content (key, value, updated_at) VALUES (?, ?, ?)",
            ("welcome_text", text, datetime.now().isoformat())
        )
        return True
    
    @staticmethod
    async def get_menu_photo(menu_key: str) -> Optional[str]:
        photo = await db.fetch_one("SELECT photo_id FROM menu_photos WHERE menu_key = ?", (menu_key,))
        return photo["photo_id"] if photo else None
    
    @staticmethod
    async def update_menu_photo(menu_key: str, photo_id: str):
        await db.execute(
            "UPDATE menu_photos SET photo_id = ?, updated_at = ? WHERE menu_key = ?",
            (photo_id, datetime.now().isoformat(), menu_key)
        )
        return True
    
    @staticmethod
    async def get_all_menu_photos() -> List[Dict]:
        return await db.fetch_all("SELECT * FROM menu_photos ORDER BY id")
    
    @staticmethod
    async def get_service_types() -> Dict:
        services = await db.fetch_all("SELECT * FROM service_types WHERE enabled = 1 ORDER BY sort_order")
        return {s["id"]: {
            "name": s["name"], "emoji": s["emoji"],
            "description": s["description"], "icon": s["icon"]
        } for s in services}
    
    @staticmethod
    async def get_service_type(service_id: str) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM service_types WHERE id = ?", (service_id,))
    
    @staticmethod
    async def update_service_type(service_id: str, data: Dict):
        await db.execute(
            """UPDATE service_types SET name=?, emoji=?, description=?, icon=?, enabled=?, sort_order=? WHERE id=?""",
            (data["name"], data["emoji"], data["description"], data["icon"],
             data.get("enabled", 1), data.get("sort_order", 0), service_id)
        )
        return True
    
    @staticmethod
    async def get_plans_by_service(service_type: str) -> Dict:
        plans = await db.fetch_all(
            "SELECT * FROM plans WHERE enabled = 1 AND service_type = ? ORDER BY days",
            (service_type,)
        )
        return {p["id"]: {
            "name": p["name"], "days": p["days"], "price": p["price"],
            "emoji": p["emoji"], "description": p["description"], "photo_id": p["photo_id"]
        } for p in plans}
    
    @staticmethod
    async def get_all_plans() -> Dict:
        plans = await db.fetch_all("SELECT * FROM plans WHERE enabled = 1 ORDER BY service_type, days")
        return {p["id"]: {
            "name": p["name"], "days": p["days"], "price": p["price"],
            "emoji": p["emoji"], "description": p["description"],
            "photo_id": p["photo_id"], "service_type": p["service_type"]
        } for p in plans}
    
    @staticmethod
    async def get_plan(plan_id: str) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM plans WHERE id = ?", (plan_id,))
    
    @staticmethod
    async def update_plan(plan_id: str, data: Dict):
        await db.execute(
            """UPDATE plans SET name=?, days=?, price=?, emoji=?, description=?, photo_id=?, service_type=?, updated_at=? WHERE id=?""",
            (data["name"], data["days"], data["price"], data["emoji"], data["description"],
             data.get("photo_id"), data.get("service_type"), datetime.now().isoformat(), plan_id)
        )
        return True
    
    @staticmethod
    async def update_plan_photo(plan_id: str, photo_id: str):
        await db.execute(
            "UPDATE plans SET photo_id = ?, updated_at = ? WHERE id = ?",
            (photo_id, datetime.now().isoformat(), plan_id)
        )
        return True

# ==================== ДАННЫЕ ====================

PROTOCOLS = ["OpenVPN", "WireGuard", "IKEv2"]
PLANS = {
    "1month": {"name": "🌱 1 месяц", "days": 30, "price": 299},
    "3month": {"name": "🌿 3 месяца", "days": 90, "price": 699},
    "6month": {"name": "🌳 6 месяцев", "days": 180, "price": 1199},
    "12month": {"name": "🏝️ 12 месяцев", "days": 365, "price": 1999}
}

# ==================== ФУНКЦИИ ДЛЯ УДАЛЕНИЯ СООБЩЕНИЙ ====================

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int):
    """Запланировать удаление сообщения бота через указанное время"""
    try:
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"🗑️ Автоудаление: сообщение {message_id} удалено через {delay} сек")
    except Exception as e:
        logger.debug(f"Не удалось автоудалить сообщение {message_id}: {e}")

async def delete_user_message_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Удаление сообщения пользователя через заданное время"""
    try:
        await asyncio.sleep(config.AUTO_DELETE_USER_MESSAGES)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"🗑️ Автоудаление пользователя: сообщение {message_id} удалено через {config.AUTO_DELETE_USER_MESSAGES} сек")
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение пользователя {message_id}: {e}")

async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Принудительное удаление предыдущего сообщения бота"""
    try:
        user = await UserManager.get(chat_id)
        if user and user.get("last_message_id"):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=user["last_message_id"])
                logger.info(f"🗑️ Мгновенно удалено предыдущее сообщение бота {user['last_message_id']}")
            except Exception as e:
                logger.debug(f"Не удалось удалить предыдущее сообщение бота: {e}")
    except Exception as e:
        logger.debug(f"Ошибка при удалении предыдущего сообщения: {e}")

async def send_new_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, keyboard=None, photo=None, auto_delete: bool = True):
    """Отправить новое сообщение, удалив предыдущее"""
    try:
        await delete_previous_message(context, chat_id)
        
        if photo:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        
        await UserManager.save_message_id(chat_id, msg.message_id)
        
        if auto_delete:
            delay = config.AUTO_DELETE_ORDER if "Оплата" in text else config.AUTO_DELETE_BOT_MESSAGES
            asyncio.create_task(schedule_message_deletion(context, chat_id, msg.message_id, delay))
        
        return msg
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return None

# ==================== ПРОВЕРКА ТЕСТЕРОВ ====================

async def check_tester_action(user_id: int, context: ContextTypes.DEFAULT_TYPE, action_type: str = "action") -> Tuple[bool, str]:
    role = await UserManager.get_role(user_id)
    if role == "admin":
        return True, "✅ Админ может всё"
    
    if role == "tester":
        tester_monitor.log_action(user_id)
        ok, msg = tester_monitor.check_action_limit(user_id)
        if not ok:
            return False, msg
        
        if action_type == "delete":
            tester_monitor.log_deletion(user_id)
            ok, msg = tester_monitor.check_delete_limit(user_id)
            if not ok:
                warnings = tester_monitor.add_warning(user_id)
                if tester_monitor.should_remove_tester(user_id):
                    await UserManager.remove_tester(user_id)
                    await send_new_message(
                        context, user_id,
                        "⚠️ Вы лишены роли тестера за превышение лимитов",
                        None, auto_delete=False
                    )
                return False, f"❌ {msg}"
        return True, msg
    
    return False, "❌ Нет прав"

# ==================== ФУНКЦИЯ ПРОВЕРКИ СТАТУСА БОТА ====================

async def is_bot_enabled(user_id: int) -> bool:
    """Проверяет, включен ли бот для пользователя"""
    role = await UserManager.get_role(user_id)
    if role == "admin":
        return True
    if config.MAINTENANCE_MODE:
        return False
    return config.BOT_ENABLED

# ==================== КЛАВИАТУРЫ ====================

class KeyboardBuilder:
    @staticmethod
    async def main(role: str = "user"):
        """Главное меню в 2 колонки"""
        services = await ContentManager.get_service_types()
        
        service_buttons = []
        service_list = list(services.items())
        
        for i in range(0, len(service_list), 2):
            row = []
            sid, service = service_list[i]
            row.append(InlineKeyboardButton(
                f"{service['icon']} {service['emoji']} {service['name']}",
                callback_data=f"service_{sid}"
            ))
            if i + 1 < len(service_list):
                sid2, service2 = service_list[i + 1]
                row.append(InlineKeyboardButton(
                    f"{service2['icon']} {service2['emoji']} {service2['name']}",
                    callback_data=f"service_{sid2}"
                ))
            service_buttons.append(row)
        
        main_buttons = [
            [
                InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile"),
                InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")
            ],
            [
                InlineKeyboardButton("📞 ПОДДЕРЖКА", callback_data="support")
            ]
        ]
        
        if role == "admin":
            admin_buttons = [
                [InlineKeyboardButton("⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_menu")]
            ]
        elif role == "tester":
            admin_buttons = [
                [InlineKeyboardButton("🧪 ТЕСТЕР ПАНЕЛЬ", callback_data="tester_menu")]
            ]
        else:
            admin_buttons = []
        
        all_buttons = service_buttons + main_buttons + admin_buttons
        return InlineKeyboardMarkup(all_buttons)
    
    @staticmethod
    async def service_plans(service_type: str):
        plans = await ContentManager.get_plans_by_service(service_type)
        
        buttons = []
        plan_list = list(plans.items())
        
        for i in range(0, len(plan_list), 2):
            row = []
            pid, plan = plan_list[i]
            row.append(InlineKeyboardButton(
                f"{plan['emoji']} {plan['name']} - {plan['price']}₽",
                callback_data=f"buy_{pid}"
            ))
            if i + 1 < len(plan_list):
                pid2, plan2 = plan_list[i + 1]
                row.append(InlineKeyboardButton(
                    f"{plan2['emoji']} {plan2['name']} - {plan2['price']}₽",
                    callback_data=f"buy_{pid2}"
                ))
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton("🎁 ПРОБНЫЙ ПЕРИОД 6 ДНЕЙ", callback_data="trial")])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def balance_menu():
        """Меню баланса"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 ПОПОЛНИТЬ БАЛАНС", callback_data="deposit")],
            [InlineKeyboardButton("📊 ИСТОРИЯ", callback_data="balance_history")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="profile")]
        ])
    
    @staticmethod
    def deposit_amounts():
        """Суммы для пополнения"""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("100 ₽", callback_data="deposit_100"),
                InlineKeyboardButton("300 ₽", callback_data="deposit_300")
            ],
            [
                InlineKeyboardButton("500 ₽", callback_data="deposit_500"),
                InlineKeyboardButton("1000 ₽", callback_data="deposit_1000")
            ],
            [
                InlineKeyboardButton("2000 ₽", callback_data="deposit_2000"),
                InlineKeyboardButton("5000 ₽", callback_data="deposit_5000")
            ],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="balance_menu")]
        ])
    
    @staticmethod
    def support_menu():
        """Меню поддержки"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 ПРОБЛЕМА С ПОДКЛЮЧЕНИЕМ", callback_data="ticket_connection")],
            [InlineKeyboardButton("💰 ВОПРОС ПО ОПЛАТЕ", callback_data="ticket_payment")],
            [InlineKeyboardButton("❓ ОБЩИЙ ВОПРОС", callback_data="ticket_other")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def ticket_admin_actions(ticket_id: int, user_id: int):
        """Кнопки для админов в группе"""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ ОТВЕТИТЬ", callback_data=f"ticket_reply_{ticket_id}_{user_id}"),
                InlineKeyboardButton("🔒 ЗАБАНИТЬ", callback_data=f"ticket_ban_{user_id}")
            ],
            [
                InlineKeyboardButton("❌ ОТКЛОНИТЬ", callback_data=f"ticket_close_{ticket_id}"),
                InlineKeyboardButton("🎁 ВЫДАТЬ ПОДПИСКУ", callback_data=f"ticket_give_{user_id}")
            ]
        ])
    
    @staticmethod
    def ticket_give_menu(user_id: int):
        """Меню выдачи подписки для админов"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 VPN", callback_data=f"ticket_give_vpn_{user_id}")],
            [InlineKeyboardButton("📱 ПРОКСИ TG", callback_data=f"ticket_give_proxy_{user_id}")],
            [InlineKeyboardButton("📡 АНТИГЛУШИЛКИ", callback_data=f"ticket_give_antijammer_{user_id}")],
            [InlineKeyboardButton("🌐 ДЛЯ САЙТОВ", callback_data=f"ticket_give_website_{user_id}")],
            [InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"ticket_cancel_{user_id}")]
        ])
    
    @staticmethod
    def admin_panel():
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👥 ПОЛЬЗОВАТЕЛИ", callback_data="admin_users"),
                InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats")
            ],
            [
                InlineKeyboardButton("📢 РАССЫЛКА", callback_data="admin_mailing"),
                InlineKeyboardButton("📝 ТЕКСТ", callback_data="admin_edit_welcome")
            ],
            [
                InlineKeyboardButton("🏷️ УСЛУГИ", callback_data="admin_services"),
                InlineKeyboardButton("💰 ТАРИФЫ", callback_data="admin_plans")
            ],
            [
                InlineKeyboardButton("🧪 ТЕСТЕРЫ", callback_data="admin_testers"),
                InlineKeyboardButton("⚡ УПРАВЛЕНИЕ", callback_data="admin_bot_control")
            ],
            [
                InlineKeyboardButton("🖼️ ФОТО МЕНЮ", callback_data="admin_menu_photos")
            ],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def bot_control():
        status = "🟢 ВКЛЮЧЕН" if config.BOT_ENABLED and not config.MAINTENANCE_MODE else "🔴 ВЫКЛЮЧЕН"
        maintenance_status = "🔧 ВКЛЮЧЕН" if config.MAINTENANCE_MODE else "✅ ВЫКЛЮЧЕН"
        
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🤖 СТАТУС: {status}", callback_data="admin_bot_status")],
            [
                InlineKeyboardButton("🟢 ВКЛЮЧИТЬ", callback_data="admin_bot_enable"),
                InlineKeyboardButton("🔴 ВЫКЛЮЧИТЬ", callback_data="admin_bot_disable")
            ],
            [
                InlineKeyboardButton("🔧 ТЕХРАБОТЫ ВКЛ", callback_data="admin_maintenance_on"),
                InlineKeyboardButton("✅ ТЕХРАБОТЫ ВЫКЛ", callback_data="admin_maintenance_off")
            ],
            [InlineKeyboardButton(f"📢 СТАТУС ТЕХРАБОТ: {maintenance_status}", callback_data="admin_maintenance_status")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")]
        ])
    
    @staticmethod
    def tester_panel():
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="tester_stats"),
                InlineKeyboardButton("👥 ПОЛЬЗОВАТЕЛИ", callback_data="tester_users")
            ],
            [
                InlineKeyboardButton("🏷️ УСЛУГИ", callback_data="tester_services"),
                InlineKeyboardButton("💰 ТАРИФЫ", callback_data="tester_plans")
            ],
            [
                InlineKeyboardButton("📝 МОИ ДЕЙСТВИЯ", callback_data="tester_actions")
            ],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def admin_testers():
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👥 СПИСОК", callback_data="admin_tester_list"),
                InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data="admin_tester_add")
            ],
            [
                InlineKeyboardButton("❌ УДАЛИТЬ", callback_data="admin_tester_remove"),
                InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_tester_stats")
            ],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")]
        ])
    
    @staticmethod
    async def admin_services():
        services = await ContentManager.get_service_types()
        
        buttons = []
        service_list = list(services.items())
        
        for i in range(0, len(service_list), 2):
            row = []
            sid, service = service_list[i]
            row.append(InlineKeyboardButton(
                f"{service['icon']} {service['emoji']} {service['name']}",
                callback_data=f"admin_edit_service_{sid}"
            ))
            if i + 1 < len(service_list):
                sid2, service2 = service_list[i + 1]
                row.append(InlineKeyboardButton(
                    f"{service2['icon']} {service2['emoji']} {service2['name']}",
                    callback_data=f"admin_edit_service_{sid2}"
                ))
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton("➕ ДОБАВИТЬ УСЛУГУ", callback_data="admin_add_service")])
        buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")])
        
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_service_edit(service_id: str, service: Dict):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 НАЗВАНИЕ", callback_data=f"admin_service_name_{service_id}"),
                InlineKeyboardButton("🎨 ЭМОДЗИ", callback_data=f"admin_service_emoji_{service_id}")
            ],
            [
                InlineKeyboardButton("📋 ОПИСАНИЕ", callback_data=f"admin_service_desc_{service_id}"),
                InlineKeyboardButton("🔢 ПОРЯДОК", callback_data=f"admin_service_order_{service_id}")
            ],
            [InlineKeyboardButton("❌ УДАЛИТЬ", callback_data=f"admin_service_delete_{service_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_services")]
        ])
    
    @staticmethod
    def tester_service_edit(service_id: str, service: Dict):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 НАЗВАНИЕ", callback_data=f"tester_service_name_{service_id}"),
                InlineKeyboardButton("🎨 ЭМОДЗИ", callback_data=f"tester_service_emoji_{service_id}")
            ],
            [InlineKeyboardButton("📋 ОПИСАНИЕ", callback_data=f"tester_service_desc_{service_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_services")]
        ])
    
    @staticmethod
    async def admin_plans():
        services = await ContentManager.get_service_types()
        
        buttons = []
        service_list = list(services.items())
        
        for i in range(0, len(service_list), 2):
            row = []
            sid, service = service_list[i]
            row.append(InlineKeyboardButton(
                f"📌 {service['emoji']} {service['name']}",
                callback_data=f"admin_service_plans_{sid}"
            ))
            if i + 1 < len(service_list):
                sid2, service2 = service_list[i + 1]
                row.append(InlineKeyboardButton(
                    f"📌 {service2['emoji']} {service2['name']}",
                    callback_data=f"admin_service_plans_{sid2}"
                ))
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton("➕ ДОБАВИТЬ ТАРИФ", callback_data="admin_add_plan")])
        buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")])
        
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_plan_edit(plan_id: str, plan: Dict):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 НАЗВАНИЕ", callback_data=f"admin_plan_name_{plan_id}"),
                InlineKeyboardButton("💰 ЦЕНА", callback_data=f"admin_plan_price_{plan_id}")
            ],
            [
                InlineKeyboardButton("📅 ДНИ", callback_data=f"admin_plan_days_{plan_id}"),
                InlineKeyboardButton("🎨 ЭМОДЗИ", callback_data=f"admin_plan_emoji_{plan_id}")
            ],
            [
                InlineKeyboardButton("📋 ОПИСАНИЕ", callback_data=f"admin_plan_desc_{plan_id}"),
                InlineKeyboardButton("🖼️ ФОТО", callback_data=f"admin_plan_photo_{plan_id}")
            ],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_plans")]
        ])
    
    @staticmethod
    def tester_plan_edit(plan_id: str, plan: Dict):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 НАЗВАНИЕ", callback_data=f"tester_plan_name_{plan_id}"),
                InlineKeyboardButton("💰 ЦЕНА", callback_data=f"tester_plan_price_{plan_id}")
            ],
            [
                InlineKeyboardButton("📅 ДНИ", callback_data=f"tester_plan_days_{plan_id}"),
                InlineKeyboardButton("🎨 ЭМОДЗИ", callback_data=f"tester_plan_emoji_{plan_id}")
            ],
            [InlineKeyboardButton("📋 ОПИСАНИЕ", callback_data=f"tester_plan_desc_{plan_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_plans")]
        ])
    
    @staticmethod
    def admin_users(users: List[Dict], page: int = 0):
        buttons = []
        start = page * 6
        end = start + 6
        
        rows = []
        current_row = []
        
        for i, user in enumerate(users[start:end]):
            name = user.get('first_name', '—')[:8]
            role_emoji = {"admin": "👑", "tester": "🧪", "user": "👤"}.get(user.get('role'), "👤")
            status = "🔴" if user.get('banned') else "🟢"
            sub = "✅" if user.get('subscribe_until') and datetime.fromisoformat(user['subscribe_until']) > datetime.now() else "❌"
            balance = user.get('balance', 0)
            btn_text = f"{role_emoji}{status}{sub} {name} | {balance}₽"
            
            current_row.append(InlineKeyboardButton(btn_text, callback_data=f"admin_user_{user['user_id']}"))
            
            if len(current_row) == 2 or i == min(5, len(users[start:end]) - 1):
                rows.append(current_row)
                current_row = []
        
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}"))
        if end < len(users):
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}"))
        if nav_row:
            rows.append(nav_row)
        
        rows.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")])
        return InlineKeyboardMarkup(rows)
    
    @staticmethod
    def tester_users(users: List[Dict], page: int = 0):
        buttons = []
        start = page * 6
        end = start + 6
        
        rows = []
        current_row = []
        
        for i, user in enumerate(users[start:end]):
            name = user.get('first_name', '—')[:8]
            status = "🟢" if not user.get('banned') else "🔴"
            sub = "✅" if user.get('subscribe_until') and datetime.fromisoformat(user['subscribe_until']) > datetime.now() else "❌"
            balance = user.get('balance', 0)
            btn_text = f"{status}{sub} {name} | {balance}₽"
            
            current_row.append(InlineKeyboardButton(btn_text, callback_data=f"tester_view_user_{user['user_id']}"))
            
            if len(current_row) == 2 or i == min(5, len(users[start:end]) - 1):
                rows.append(current_row)
                current_row = []
        
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"tester_users_page_{page-1}"))
        if end < len(users):
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"tester_users_page_{page+1}"))
        if nav_row:
            rows.append(nav_row)
        
        rows.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_menu")])
        return InlineKeyboardMarkup(rows)
    
    @staticmethod
    def admin_user_actions(user_id: int, is_banned: bool):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 ВЫДАТЬ БОНУС", callback_data=f"admin_give_bonus_{user_id}")],
            [InlineKeyboardButton("📅 ВЫДАТЬ ПОДПИСКУ", callback_data=f"admin_give_{user_id}")],
            [InlineKeyboardButton("🔒 ЗАБАНИТЬ" if not is_banned else "🔓 РАЗБАНИТЬ",
                                 callback_data=f"admin_ban_{user_id}" if not is_banned else f"admin_unban_{user_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_users")]
        ])
    
    @staticmethod
    def admin_give_sub(user_id: int):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🌱 1 мес", callback_data=f"admin_give_1month_{user_id}"),
                InlineKeyboardButton("🌿 3 мес", callback_data=f"admin_give_3month_{user_id}")
            ],
            [
                InlineKeyboardButton("🌳 6 мес", callback_data=f"admin_give_6month_{user_id}"),
                InlineKeyboardButton("🏝️ 12 мес", callback_data=f"admin_give_12month_{user_id}")
            ],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data=f"admin_user_{user_id}")]
        ])
    
    @staticmethod
    def admin_confirm_mailing():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data="admin_mailing_confirm")],
            [InlineKeyboardButton("❌ ОТМЕНИТЬ", callback_data="admin_menu")]
        ])
    
    @staticmethod
    def back():
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== FASTAPI ПРИЛОЖЕНИЕ ====================

app = FastAPI()
telegram_app = None
startup_time = time.time()

# ==================== ФУНКЦИЯ РАССЫЛКИ УВЕДОМЛЕНИЙ ====================

async def notify_maintenance(context: ContextTypes.DEFAULT_TYPE, message: str):
    users = await UserManager.get_all_users()
    total = len(users)
    sent = 0
    
    logger.info(f"📢 Отправка уведомления о техработах {total} пользователям")
    
    for user in users:
        if user.get("banned"):
            continue
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=message,
                parse_mode=ParseMode.HTML
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user['user_id']}: {e}")
    
    logger.info(f"✅ Уведомление отправлено {sent} пользователям")
    return sent

# ==================== ФОНОВАЯ ПРОВЕРКА ПЛАТЕЖЕЙ ====================

async def check_pending_payments():
    while True:
        try:
            await asyncio.sleep(30)
            if not config.BOT_ENABLED or config.MAINTENANCE_MODE:
                continue
                
            pending = await UserManager.get_pending_payments()
            for payment in pending:
                if await crypto.check_payment(payment["invoice_id"]):
                    user_id = payment["user_id"]
                    amount = payment["amount_rub"]
                    
                    # Начисляем баланс
                    await UserManager.add_balance(user_id, amount, f"Пополнение через CryptoBot")
                    await UserManager.confirm_crypto_payment(payment["invoice_id"])
                    
                    await telegram_app.bot.send_message(
                        chat_id=user_id,
                        text=f"✅ <b>Пополнение подтверждено!</b>\n\n💰 Баланс пополнен на {amount}₽",
                        parse_mode=ParseMode.HTML
                    )
        except Exception as e:
            logger.error(f"Ошибка проверки платежей: {e}")
            await asyncio.sleep(60)

# ==================== ОБРАБОТЧИКИ TELEGRAM ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        args = context.args
        user_id = user.id
        chat_id = update.effective_chat.id
        
        # Автоудаление сообщения пользователя
        asyncio.create_task(delete_user_message_later(context, chat_id, update.message.message_id))
        
        # Проверяем статус бота
        if not await is_bot_enabled(user_id):
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=config.MAINTENANCE_MESSAGE,
                parse_mode=ParseMode.HTML
            )
            asyncio.create_task(schedule_message_deletion(context, chat_id, msg.message_id, config.AUTO_DELETE_BOT_MESSAGES))
            return
        
        referred_by = None
        if args and args[0].startswith("ref_"):
            try:
                ref_id = int(args[0].replace("ref_", ""))
                if ref_id != user.id and await UserManager.get(ref_id):
                    referred_by = ref_id
            except:
                pass
        
        await UserManager.create(user.id, user.username or "", user.first_name or "", referred_by)
        
        if (await UserManager.get(user.id)).get("banned"):
            await update.message.reply_text("⛔ Доступ заблокирован")
            return
        
        role = await UserManager.get_role(user.id)
        
        # Получаем фото для главного меню
        menu_photo = await ContentManager.get_menu_photo("main_menu")
        
        if menu_photo:
            await send_new_message(
                context, 
                user.id, 
                await ContentManager.get_welcome_text(), 
                await KeyboardBuilder.main(role),
                photo=menu_photo
            )
        else:
            await send_new_message(
                context, 
                user.id, 
                await ContentManager.get_welcome_text(), 
                await KeyboardBuilder.main(role)
            )
        
    except Exception as e:
        logger.error(f"Ошибка start: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        
        # Проверяем статус бота (кроме админских кнопок)
        if not data.startswith("admin_") and not data.startswith("back_main"):
            if not await is_bot_enabled(user_id):
                await query.answer("🔧 Бот временно недоступен", show_alert=True)
                return
        
        role = await UserManager.get_role(user_id)
        is_admin = role == "admin"
        is_tester = role == "tester"
        
        logger.info(f"🔘 Кнопка: {data} от {user_id} (роль: {role})")
        
        # ===== НАВИГАЦИЯ =====
        if data == "back_main":
            menu_photo = await ContentManager.get_menu_photo("main_menu")
            if menu_photo:
                await send_new_message(
                    context, 
                    user_id, 
                    "🏠 Главное меню", 
                    await KeyboardBuilder.main(role),
                    photo=menu_photo
                )
            else:
                await send_new_message(
                    context, 
                    user_id, 
                    "🏠 Главное меню", 
                    await KeyboardBuilder.main(role)
                )
        
        # ===== УСЛУГИ =====
        elif data.startswith("service_"):
            service_id = data.replace("service_", "")
            services = await ContentManager.get_service_types()
            service = services.get(service_id, {"name": "Услуга", "description": "", "icon": "🔹", "emoji": "📌"})
            
            service_photo = await ContentManager.get_menu_photo("services")
            text = f"{service.get('icon', '🔹')} {service.get('emoji', '📌')} <b>{service['name']}</b>\n\n{service.get('description', '')}\n\nВыберите тариф:"
            
            if service_photo:
                await send_new_message(
                    context, 
                    user_id, 
                    text, 
                    await KeyboardBuilder.service_plans(service_id),
                    photo=service_photo
                )
            else:
                await send_new_message(
                    context, 
                    user_id, 
                    text, 
                    await KeyboardBuilder.service_plans(service_id)
                )
        
        # ===== ПРОБНЫЙ ПЕРИОД =====
        elif data == "trial":
            ok, msg = await UserManager.activate_trial(user_id)
            await send_new_message(context, user_id, msg, await KeyboardBuilder.main(role))
        
        # ===== ПОКУПКА ЗА БАЛАНС =====
        elif data.startswith("buy_"):
            plan_id = data.replace("buy_", "")
            plans = await ContentManager.get_all_plans()
            if plan_id in plans:
                plan = plans[plan_id]
                
                # Проверяем баланс
                balance = await UserManager.get_balance(user_id)
                if balance < plan["price"]:
                    await send_new_message(
                        context, 
                        user_id, 
                        f"❌ Недостаточно средств!\n\n💰 Ваш баланс: {balance}₽\n💵 Нужно: {plan['price']}₽",
                        KeyboardBuilder.balance_menu()
                    )
                    return
                
                # Покупаем
                success, msg = await UserManager.buy_subscription(user_id, plan_id, plan["price"], plan["days"])
                await send_new_message(context, user_id, msg, await KeyboardBuilder.main(role))
            else:
                await send_new_message(context, user_id, "❌ Тариф не найден", await KeyboardBuilder.main(role))
        
        # ===== БАЛАНС =====
        elif data == "balance_menu":
            balance = await UserManager.get_balance(user_id)
            text = f"💰 <b>ВАШ БАЛАНС</b>\n\n{balance}₽"
            await send_new_message(context, user_id, text, KeyboardBuilder.balance_menu())
        
        elif data == "deposit":
            await send_new_message(context, user_id, "💰 Выберите сумму пополнения:", KeyboardBuilder.deposit_amounts())
        
        elif data.startswith("deposit_"):
            amount = int(data.replace("deposit_", ""))
            
            payload = json.dumps({"user_id": user_id, "type": "deposit", "amount": amount})
            invoice = await crypto.create_invoice(amount, payload)
            
            if invoice:
                await UserManager.save_crypto_payment(user_id, invoice["invoice_id"], amount, payload)
                text = f"💰 <b>Пополнение баланса на {amount}₽</b>\n\n1. Нажмите кнопку оплаты\n2. После оплаты нажмите «Я оплатил»"
                await send_new_message(
                    context, 
                    user_id, 
                    text, 
                    InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 ОПЛАТИТЬ", url=invoice["bot_invoice_url"])],
                        [InlineKeyboardButton("✅ Я ОПЛАТИЛ", callback_data=f"check_deposit_{invoice['invoice_id']}")],
                        [InlineKeyboardButton("◀️ НАЗАД", callback_data="balance_menu")]
                    ])
                )
            else:
                await send_new_message(context, user_id, "❌ Ошибка создания счета", KeyboardBuilder.balance_menu())
        
        elif data.startswith("check_deposit_"):
            invoice_id = int(data.replace("check_deposit_", ""))
            if await crypto.check_payment(invoice_id):
                payment = await db.fetch_one("SELECT * FROM crypto_payments WHERE invoice_id = ?", (invoice_id,))
                if payment and payment["status"] == "pending":
                    amount = payment["amount_rub"]
                    await UserManager.add_balance(user_id, amount, "Пополнение баланса")
                    await UserManager.confirm_crypto_payment(invoice_id)
                    
                    await send_new_message(
                        context,
                        user_id,
                        f"✅ <b>Пополнение подтверждено!</b>\n\n💰 Баланс пополнен на {amount}₽",
                        await KeyboardBuilder.main(role)
                    )
                await query.answer("✅ Платеж найден!", show_alert=True)
            else:
                await query.answer("❌ Платеж не найден. Если вы оплатили, подождите минуту.", show_alert=True)
        
        elif data == "balance_history":
            transactions = await UserManager.get_transactions(user_id, 10)
            text = "📊 <b>ИСТОРИЯ ОПЕРАЦИЙ</b>\n\n"
            if not transactions:
                text += "История пуста"
            else:
                for t in transactions:
                    sign = "+" if t["type"] == "deposit" else "-"
                    date = datetime.fromisoformat(t["created_at"]).strftime("%d.%m %H:%M")
                    text += f"{date} {sign}{abs(t['amount'])}₽ - {t['description']}\n"
            await send_new_message(context, user_id, text, KeyboardBuilder.back())
        
        # ===== ПРОФИЛЬ =====
        elif data == "profile":
            user = await UserManager.get(user_id)
            if user:
                end_str, days, status = "-", 0, "❌ Нет подписки"
                if user.get("subscribe_until"):
                    try:
                        end = datetime.fromisoformat(user["subscribe_until"])
                        days = (end - datetime.now()).days
                        status = "✅ Активна" if days > 0 else "❌ Истекла"
                        end_str = end.strftime("%d.%m.%Y")
                    except:
                        pass
                
                role_emoji = "👑" if role == "admin" else "🧪" if role == "tester" else "👤"
                balance = user.get("balance", 0)
                text = f"{role_emoji} <b>ПРОФИЛЬ</b>\n\n💰 Баланс: {balance}₽\n📊 Статус: {status}\n📅 До: {end_str}\n⏱ Осталось: {max(0, days)} дн.\n🆔 ID: <code>{user_id}</code>"
                
                profile_photo = user.get("profile_photo") or await ContentManager.get_menu_photo("profile")
                
                # Клавиатура с балансом
                profile_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💰 БАЛАНС", callback_data="balance_menu")],
                    [InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")],
                    [InlineKeyboardButton("🔙 НАЗАД", callback_data="back_main")]
                ])
                
                if profile_photo:
                    await send_new_message(context, user_id, text, profile_kb, photo=profile_photo)
                else:
                    await send_new_message(context, user_id, text, profile_kb)
        
        # ===== РЕФЕРАЛЫ =====
        elif data == "referrals":
            user = await UserManager.get(user_id)
            if user:
                refs = await db.fetch_all("SELECT COUNT(*) as c FROM referrals WHERE referrer_id=?", (user_id,))
                count = refs[0]["c"] if refs else 0
                earnings = user.get("referral_earnings", 0)
                text = f"👥 <b>РЕФЕРАЛЫ</b>\n\nВаш ID: <code>{user_id}</code>\nПриглашено: {count}\n💰 Заработано: {earnings}₽\n🎁 +{config.REFERRAL_BONUS_PERCENT}% от пополнений\n\n🔗 Ссылка:\n<code>https://t.me/{config.BOT_USERNAME}?start=ref_{user_id}</code>"
                await send_new_message(context, user_id, text, KeyboardBuilder.referrals(str(user_id)))
        
        # ===== СТАТИСТИКА РЕФЕРАЛОВ =====
        elif data == "referral_stats":
            user = await UserManager.get(user_id)
            if user:
                refs = await db.fetch_all("SELECT * FROM referrals WHERE referrer_id=? ORDER BY created_at DESC", (user_id,))
                text = "👥 <b>ВАШИ РЕФЕРАЛЫ</b>\n\n"
                if not refs:
                    text += "Пока нет"
                else:
                    for r in refs[:10]:
                        u = await UserManager.get(r["referred_id"])
                        text += f"• {u.get('first_name', '—') if u else '—'} - {r['created_at'][:10]}\n"
                await send_new_message(context, user_id, text, KeyboardBuilder.back())
        
        # ===== ПОДДЕРЖКА =====
        elif data == "support":
            support_photo = await ContentManager.get_menu_photo("support")
            if support_photo:
                await send_new_message(
                    context, 
                    user_id, 
                    "📞 <b>СЛУЖБА ПОДДЕРЖКИ</b>\n\nВыберите тему обращения:",
                    KeyboardBuilder.support_menu(),
                    photo=support_photo
                )
            else:
                await send_new_message(
                    context, 
                    user_id, 
                    "📞 <b>СЛУЖБА ПОДДЕРЖКИ</b>\n\nВыберите тему обращения:",
                    KeyboardBuilder.support_menu()
                )
        
        # ===== СОЗДАНИЕ ТИКЕТА =====
        elif data.startswith("ticket_"):
            subject_map = {
                "ticket_connection": "🔧 Проблема с подключением",
                "ticket_payment": "💰 Вопрос по оплате",
                "ticket_other": "❓ Общий вопрос"
            }
            
            if data in subject_map:
                context.user_data['ticket_subject'] = subject_map[data]
                await send_new_message(
                    context,
                    user_id,
                    f"📝 <b>Создание обращения</b>\n\nТема: {subject_map[data]}\n\nОпишите вашу проблему подробно:",
                    KeyboardBuilder.back()
                )
                context.user_data['awaiting_ticket_message'] = True
        
        # ===== ОБРАБОТКА ТИКЕТОВ ИЗ ГРУППЫ =====
        elif data.startswith("ticket_reply_"):
            parts = data.split("_")
            ticket_id = int(parts[2])
            target_user = int(parts[3])
            
            context.user_data['replying_to_ticket'] = ticket_id
            context.user_data['replying_to_user'] = target_user
            
            await query.edit_message_text(
                f"✏️ Введите ответ для пользователя (ID: {target_user}):\n\n"
                "Ваш ответ будет отправлен в личные сообщения.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"ticket_cancel_{target_user}")
                ]])
            )
            context.user_data['awaiting_ticket_reply'] = True

        elif data.startswith("ticket_ban_"):
            target_user = int(data.split("_")[2])
            await UserManager.ban_user(target_user)
            await query.edit_message_text(
                f"✅ Пользователь {target_user} забанен.",
                reply_markup=None
            )
            
            try:
                await context.bot.send_message(
                    chat_id=target_user,
                    text="⛔ Вы были забанены администратором."
                )
            except:
                pass

        elif data.startswith("ticket_close_"):
            ticket_id = int(data.split("_")[2])
            await UserManager.close_ticket(ticket_id, user_id)
            await query.edit_message_text(
                f"✅ Тикет #{ticket_id} закрыт.",
                reply_markup=None
            )

        elif data.startswith("ticket_give_"):
            target_user = int(data.split("_")[2])
            await query.edit_message_text(
                f"🎁 Выберите услугу для пользователя {target_user}:",
                reply_markup=KeyboardBuilder.ticket_give_menu(target_user)
            )

        elif data.startswith("ticket_give_vpn_"):
            target_user = int(data.split("_")[3])
            new_date = await UserManager.give_service_subscription(target_user, "vpn", admin_give=True)
            await query.edit_message_text(
                f"✅ Пользователю {target_user} выдана подписка VPN.\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}",
                reply_markup=None
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user,
                    text=f"🎉 Администратор выдал вам подписку VPN!\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}"
                )
            except:
                pass

        elif data.startswith("ticket_give_proxy_"):
            target_user = int(data.split("_")[3])
            new_date = await UserManager.give_service_subscription(target_user, "proxy", admin_give=True)
            await query.edit_message_text(
                f"✅ Пользователю {target_user} выдана подписка на Прокси TG.\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}",
                reply_markup=None
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user,
                    text=f"🎉 Администратор выдал вам подписку на Прокси TG!\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}"
                )
            except:
                pass

        elif data.startswith("ticket_give_antijammer_"):
            target_user = int(data.split("_")[3])
            new_date = await UserManager.give_service_subscription(target_user, "antijammer", admin_give=True)
            await query.edit_message_text(
                f"✅ Пользователю {target_user} выдана подписка на Антиглушилки.\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}",
                reply_markup=None
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user,
                    text=f"🎉 Администратор выдал вам подписку на Антиглушилки!\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}"
                )
            except:
                pass

        elif data.startswith("ticket_give_website_"):
            target_user = int(data.split("_")[3])
            new_date = await UserManager.give_service_subscription(target_user, "website", admin_give=True)
            await query.edit_message_text(
                f"✅ Пользователю {target_user} выдана подписка на Доступ к сайтам.\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}",
                reply_markup=None
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user,
                    text=f"🎉 Администратор выдал вам подписку на Доступ к сайтам!\n📅 Действует до: {new_date.strftime('%d.%m.%Y')}"
                )
            except:
                pass

        elif data.startswith("ticket_cancel_"):
            await query.edit_message_text(
                "❌ Действие отменено.",
                reply_markup=None
            )
        
        # ===== АДМИН ПАНЕЛЬ =====
        elif data == "admin_menu" and is_admin:
            await send_new_message(context, user_id, "⚙️ <b>АДМИН ПАНЕЛЬ</b>", KeyboardBuilder.admin_panel())
        
        # ===== УПРАВЛЕНИЕ ФОТО МЕНЮ =====
        elif data == "admin_menu_photos" and is_admin:
            photos = await ContentManager.get_all_menu_photos()
            text = "🖼️ <b>УПРАВЛЕНИЕ ФОТО МЕНЮ</b>\n\n"
            
            buttons = []
            for photo in photos:
                status = "✅ есть" if photo['photo_id'] else "❌ нет"
                text += f"• {photo['description']}: {status}\n"
                buttons.append([InlineKeyboardButton(
                    f"📸 {photo['description']}",
                    callback_data=f"admin_edit_menu_photo_{photo['menu_key']}"
                )])
            
            buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")])
            
            await send_new_message(context, user_id, text, InlineKeyboardMarkup(buttons))
        
        elif data.startswith("admin_edit_menu_photo_") and is_admin:
            menu_key = data.replace("admin_edit_menu_photo_", "")
            context.user_data['editing_menu_photo'] = menu_key
            await send_new_message(
                context,
                user_id,
                f"🖼️ Отправьте фото для {menu_key}:",
                KeyboardBuilder.back()
            )
        
        # ===== УПРАВЛЕНИЕ БОТОМ =====
        elif data == "admin_bot_control" and is_admin:
            await send_new_message(context, user_id, "⚡ <b>УПРАВЛЕНИЕ БОТОМ</b>", KeyboardBuilder.bot_control())
        
        elif data == "admin_bot_status" and is_admin:
            status = "ВКЛЮЧЕН" if config.BOT_ENABLED else "ВЫКЛЮЧЕН"
            maint = "ВКЛЮЧЕН" if config.MAINTENANCE_MODE else "ВЫКЛЮЧЕН"
            await query.answer(f"🤖 Бот: {status}\n🔧 Техработы: {maint}", show_alert=True)
        
        elif data == "admin_bot_enable" and is_admin:
            config.BOT_ENABLED = True
            await UserManager.log_maintenance("bot_enable", user_id)
            await send_new_message(context, user_id, "✅ Бот включен!", KeyboardBuilder.bot_control())
        
        elif data == "admin_bot_disable" and is_admin:
            config.BOT_ENABLED = False
            await UserManager.log_maintenance("bot_disable", user_id)
            await send_new_message(context, user_id, "🔴 Бот выключен!", KeyboardBuilder.bot_control())
        
        elif data == "admin_maintenance_on" and is_admin:
            config.MAINTENANCE_MODE = True
            await UserManager.log_maintenance("maintenance_on", user_id)
            sent = await notify_maintenance(context, config.MAINTENANCE_MESSAGE)
            await send_new_message(context, user_id, 
                f"🔧 Режим техработ включен!\n📢 Уведомление отправлено {sent} пользователям.", 
                KeyboardBuilder.bot_control())
        
        elif data == "admin_maintenance_off" and is_admin:
            config.MAINTENANCE_MODE = False
            await UserManager.log_maintenance("maintenance_off", user_id)
            await send_new_message(context, user_id, "✅ Режим техработ выключен!", KeyboardBuilder.bot_control())
        
        elif data == "admin_maintenance_status" and is_admin:
            status = "ВКЛЮЧЕН" if config.MAINTENANCE_MODE else "ВЫКЛЮЧЕН"
            await query.answer(f"🔧 Режим техработ: {status}", show_alert=True)
        
        # ===== ТЕСТЕР ПАНЕЛЬ =====
        elif data == "tester_menu" and is_tester:
            await send_new_message(context, user_id, "🧪 <b>ТЕСТЕР ПАНЕЛЬ</b>", KeyboardBuilder.tester_panel())
        
        # ===== СТАТИСТИКА =====
        elif data == "admin_stats" and is_admin:
            stats = await UserManager.get_stats()
            await send_new_message(context, user_id,
                f"📊 <b>СТАТИСТИКА</b>\n\n👥 Всего: {stats['total']}\n✅ Активных: {stats['active']}\n🔒 Забанено: {stats['banned']}\n🎁 Пробный: {stats['trial']}\n👑 Админы: {stats['admins']}\n🧪 Тестеры: {stats['testers']}\n💰 Всего баланс: {stats['total_balance']}₽\n📈 Конверсия: {stats['conversion']}%",
                KeyboardBuilder.admin_panel())
        
        elif data == "tester_stats" and is_tester:
            ok, _ = await check_tester_action(user_id, context)
            if ok:
                stats = await UserManager.get_stats()
                await send_new_message(context, user_id,
                    f"📊 <b>СТАТИСТИКА</b>\n\n👥 Всего: {stats['total']}\n✅ Активных: {stats['active']}\n🔒 Забанено: {stats['banned']}\n💰 Всего баланс: {stats['total_balance']}₽",
                    KeyboardBuilder.tester_panel())
        
        # ===== ПРОСМОТР ПОЛЬЗОВАТЕЛЕЙ =====
        elif data == "tester_users" and is_tester:
            ok, _ = await check_tester_action(user_id, context)
            if ok:
                users = await UserManager.get_all_users()
                await send_new_message(context, user_id, f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>", KeyboardBuilder.tester_users(users))
        
        elif data.startswith("tester_users_page_") and is_tester:
            page = int(data.split("_")[-1])
            users = await UserManager.get_all_users()
            await send_new_message(context, user_id, f"👥 Страница {page+1}:", KeyboardBuilder.tester_users(users, page))
        
        elif data.startswith("tester_view_user_") and is_tester:
            target_id = int(data.replace("tester_view_user_", ""))
            target = await UserManager.get(target_id)
            if target:
                sub = target.get("subscribe_until", "Нет")[:10] if target.get("subscribe_until") else "Нет"
                balance = target.get("balance", 0)
                text = f"👤 <b>ИНФОРМАЦИЯ</b>\n\nID: <code>{target_id}</code>\nИмя: {target.get('first_name', '—')}\nЮзернейм: @{target.get('username', '—')}\nПодписка до: {sub}\n💰 Баланс: {balance}₽\nСтатус: {'🟢' if not target.get('banned') else '🔴'}"
                await send_new_message(context, user_id, text, KeyboardBuilder.back())
        
        # ===== ДЕЙСТВИЯ ТЕСТЕРА =====
        elif data == "tester_actions" and is_tester:
            actions = len(tester_monitor.actions[user_id])
            deletions = len(tester_monitor.deletions[user_id])
            warnings = tester_monitor.warnings[user_id]
            text = f"📝 <b>ВАША АКТИВНОСТЬ</b>\n\n📊 Действий за час: {actions}/{config.TESTER_ACTION_LIMIT}\n🗑️ Удалений за день: {deletions}/{config.TESTER_DELETE_LIMIT}\n⚠️ Предупреждений: {warnings}/3"
            await send_new_message(context, user_id, text, KeyboardBuilder.tester_panel())
        
        # ===== УПРАВЛЕНИЕ УСЛУГАМИ ТЕСТЕРАМИ =====
        elif data == "tester_services" and is_tester:
            ok, _ = await check_tester_action(user_id, context)
            if ok:
                services = await ContentManager.get_service_types()
                text = "🏷️ <b>УПРАВЛЕНИЕ УСЛУГАМИ</b>"
                await send_new_message(context, user_id, text, await KeyboardBuilder.admin_services())
        
        elif data.startswith("tester_edit_service_") and is_tester:
            sid = data.replace("tester_edit_service_", "")
            s = await ContentManager.get_service_type(sid)
            if s:
                await send_new_message(context, user_id, f"🏷️ {s['name']}\n\nВыберите поле:", KeyboardBuilder.tester_service_edit(sid, s))
        
        # ===== УПРАВЛЕНИЕ ТАРИФАМИ ТЕСТЕРАМИ =====
        elif data == "tester_plans" and is_tester:
            ok, _ = await check_tester_action(user_id, context)
            if ok:
                await send_new_message(context, user_id, "💰 <b>УПРАВЛЕНИЕ ТАРИФАМИ</b>", await KeyboardBuilder.admin_plans())
        
        elif data.startswith("tester_edit_plan_") and is_tester:
            pid = data.replace("tester_edit_plan_", "")
            p = await ContentManager.get_plan(pid)
            if p:
                await send_new_message(context, user_id, f"💰 {p['name']}\n\nВыберите поле:", KeyboardBuilder.tester_plan_edit(pid, p))
        
        # ===== РЕДАКТИРОВАНИЕ ПОЛЕЙ ТЕСТЕРАМИ =====
        elif data.startswith("tester_service_") and is_tester:
            parts = data.split("_")
            if len(parts) >= 4:
                field = parts[2]
                sid = parts[3]
                context.user_data['editing_service'] = sid
                context.user_data['editing_field'] = field
                await send_new_message(context, user_id, f"📝 Введите новое значение для {field}:", KeyboardBuilder.back())
        
        elif data.startswith("tester_plan_") and is_tester:
            parts = data.split("_")
            if len(parts) >= 4:
                field = parts[2]
                pid = parts[3]
                action = "delete" if field == "delete" else "action"
                ok, msg = await check_tester_action(user_id, context, action)
                if ok:
                    context.user_data['editing_plan'] = pid
                    context.user_data['editing_field'] = field
                    await send_new_message(context, user_id, f"📝 Введите новое значение для {field}:", KeyboardBuilder.back())
                else:
                    await send_new_message(context, user_id, msg, KeyboardBuilder.tester_panel())
        
        # ===== АДМИН: УПРАВЛЕНИЕ ТЕСТЕРАМИ =====
        elif data == "admin_testers" and is_admin:
            await send_new_message(context, user_id, "🧪 <b>УПРАВЛЕНИЕ ТЕСТЕРАМИ</b>", KeyboardBuilder.admin_testers())
        
        elif data == "admin_tester_list" and is_admin:
            testers = []
            for tid in config.TESTER_IDS:
                u = await UserManager.get(tid)
                if u:
                    testers.append(f"• {u['first_name']} (@{u['username']}) - ID: {tid} | Баланс: {u.get('balance', 0)}₽")
            await send_new_message(context, user_id, "👥 <b>ТЕСТЕРЫ</b>\n\n" + ("\n".join(testers) if testers else "Нет тестеров"), KeyboardBuilder.admin_panel())
        
        elif data == "admin_tester_add" and is_admin:
            context.user_data['awaiting_tester_add'] = True
            await send_new_message(context, user_id, "➕ Введите ID пользователя:", KeyboardBuilder.back())
        
        elif data == "admin_tester_remove" and is_admin:
            context.user_data['awaiting_tester_remove'] = True
            await send_new_message(context, user_id, "❌ Введите ID пользователя:", KeyboardBuilder.back())
        
        elif data == "admin_tester_stats" and is_admin:
            await send_new_message(context, user_id, f"📊 Тестеров: {len(config.TESTER_IDS)}", KeyboardBuilder.admin_panel())
        
        # ===== АДМИН: УПРАВЛЕНИЕ УСЛУГАМИ =====
        elif data == "admin_services" and is_admin:
            await send_new_message(context, user_id, "🏷️ <b>УПРАВЛЕНИЕ УСЛУГАМИ</b>", await KeyboardBuilder.admin_services())
        
        elif data.startswith("admin_edit_service_") and is_admin:
            sid = data.replace("admin_edit_service_", "")
            s = await ContentManager.get_service_type(sid)
            if s:
                await send_new_message(context, user_id, f"🏷️ {s['name']}\n\nРедактирование:", KeyboardBuilder.admin_service_edit(sid, s))
        
        # ===== АДМИН: УПРАВЛЕНИЕ ТАРИФАМИ =====
        elif data == "admin_plans" and is_admin:
            await send_new_message(context, user_id, "💰 <b>УПРАВЛЕНИЕ ТАРИФАМИ</b>", await KeyboardBuilder.admin_plans())
        
        elif data.startswith("admin_edit_plan_") and is_admin:
            pid = data.replace("admin_edit_plan_", "")
            p = await ContentManager.get_plan(pid)
            if p:
                await send_new_message(context, user_id, f"💰 {p['name']}\n\nРедактирование:", KeyboardBuilder.admin_plan_edit(pid, p))
        
        # ===== АДМИН: ПОЛЬЗОВАТЕЛИ =====
        elif data == "admin_users" and is_admin:
            users = await UserManager.get_all_users()
            await send_new_message(context, user_id, f"👥 <b>ПОЛЬЗОВАТЕЛИ ({len(users)})</b>", KeyboardBuilder.admin_users(users))
        
        elif data.startswith("admin_users_page_") and is_admin:
            page = int(data.split("_")[-1])
            users = await UserManager.get_all_users()
            await send_new_message(context, user_id, f"👥 Страница {page+1}:", KeyboardBuilder.admin_users(users, page))
        
        elif data.startswith("admin_user_") and is_admin:
            target_id = int(data.split("_")[-1])
            target = await UserManager.get(target_id)
            if target:
                sub = target.get("subscribe_until", "Нет")[:10] if target.get("subscribe_until") else "Нет"
                balance = target.get("balance", 0)
                text = f"👤 <b>ПОЛЬЗОВАТЕЛЬ</b>\n\nID: <code>{target_id}</code>\nИмя: {target.get('first_name', '—')}\nЮзернейм: @{target.get('username', '—')}\nПодписка до: {sub}\n💰 Баланс: {balance}₽\nСтатус: {'🔴 ЗАБАНЕН' if target.get('banned') else '🟢 АКТИВЕН'}"
                await send_new_message(context, user_id, text, KeyboardBuilder.admin_user_actions(target_id, target.get('banned', False)))
        
        # ===== АДМИН: ВЫДАТЬ БОНУС =====
        elif data.startswith("admin_give_bonus_") and is_admin:
            target_id = int(data.split("_")[3])
            context.user_data['admin_give_bonus_to'] = target_id
            await send_new_message(context, user_id, f"💰 Введите сумму бонуса для пользователя {target_id}:", KeyboardBuilder.back())
            context.user_data['awaiting_admin_bonus'] = True
        
        # ===== АДМИН: БАН/РАЗБАН =====
        elif data.startswith("admin_ban_") and is_admin:
            target_id = int(data.split("_")[-1])
            await UserManager.ban_user(target_id)
            await send_new_message(context, user_id, "✅ Пользователь забанен", KeyboardBuilder.admin_panel())
        
        elif data.startswith("admin_unban_") and is_admin:
            target_id = int(data.split("_")[-1])
            await UserManager.unban_user(target_id)
            await send_new_message(context, user_id, "✅ Пользователь разбанен", KeyboardBuilder.admin_panel())
        
        # ===== АДМИН: ВЫДАТЬ ПОДПИСКУ =====
        elif data.startswith("admin_give_") and is_admin and not any(data.startswith(f"admin_give_{pid}_") for pid in ["1month", "3month", "6month", "12month"]):
            target_id = int(data.split("_")[-1])
            await send_new_message(context, user_id, "📅 Выберите срок:", KeyboardBuilder.admin_give_sub(target_id))
        
        elif data.startswith("admin_give_") and is_admin:
            parts = data.split("_")
            if len(parts) == 4:
                plan_id = parts[2]
                target_id = int(parts[3])
                if plan_id in PLANS:
                    plan = PLANS[plan_id]
                    new_date = await UserManager.give_subscription(target_id, plan["days"], admin_give=True)
                    if new_date:
                        await send_new_message(context, target_id, f"🎉 Админ выдал {plan['name']} до {new_date.strftime('%d.%m.%Y')}")
                        await send_new_message(context, user_id, f"✅ Выдано {plan['days']} дней", KeyboardBuilder.admin_panel())
        
        # ===== АДМИН: РЕДАКТИРОВАНИЕ ТЕКСТА =====
        elif data == "admin_edit_welcome" and is_admin:
            context.user_data['awaiting_welcome_edit'] = True
            current = await ContentManager.get_welcome_text()
            await send_new_message(context, user_id, f"📝 Текущий текст:\n{current}\n\nОтправьте новый текст:", KeyboardBuilder.back())
        
        # ===== АДМИН: РАССЫЛКА =====
        elif data == "admin_mailing" and is_admin:
            context.user_data['awaiting_mailing'] = True
            await send_new_message(context, user_id, "📢 Отправьте текст рассылки:", KeyboardBuilder.back())
        
        elif data == "admin_mailing_confirm" and is_admin:
            if context.user_data.get('mailing_text'):
                asyncio.create_task(start_mailing(context, user_id, context.user_data['mailing_text']))
                del context.user_data['mailing_text']
        
    except Exception as e:
        logger.error(f"Ошибка button_handler: {e}", exc_info=True)
        try:
            await query.answer("❌ Произошла ошибка", show_alert=True)
        except:
            pass

# ==================== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ====================

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        # Обработка фото
        if update.message.photo:
            # Проверяем, это загрузка фото для меню?
            if context.user_data.get('editing_menu_photo'):
                menu_key = context.user_data['editing_menu_photo']
                photo = update.message.photo[-1]
                await ContentManager.update_menu_photo(menu_key, photo.file_id)
                await send_new_message(
                    context,
                    update.effective_chat.id,
                    f"✅ Фото для {menu_key} обновлено!",
                    KeyboardBuilder.admin_panel()
                )
                context.user_data.pop('editing_menu_photo', None)
                return
            
            # Обработка фото для тарифа
            if context.user_data.get('editing_plan') and context.user_data.get('editing_field') == 'photo':
                photo = update.message.photo[-1]
                if await ContentManager.update_plan_photo(context.user_data['editing_plan'], photo.file_id):
                    await send_new_message(context, update.effective_chat.id, "✅ Фото тарифа обновлено", KeyboardBuilder.admin_panel())
                else:
                    await send_new_message(context, update.effective_chat.id, "❌ Ошибка", KeyboardBuilder.admin_panel())
                context.user_data.pop('editing_plan', None)
                context.user_data.pop('editing_field', None)
                return
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    role = await UserManager.get_role(user_id)
    
    # Автоудаление сообщения пользователя
    asyncio.create_task(delete_user_message_later(context, chat_id, update.message.message_id))
    
    user = await UserManager.get(user_id)
    if user and user.get("banned"):
        await update.message.reply_text("⛔ Доступ заблокирован")
        return
    
    # Проверяем статус бота для не-админов
    if not await is_bot_enabled(user_id) and role != "admin":
        await update.message.reply_text(config.MAINTENANCE_MESSAGE, parse_mode=ParseMode.HTML)
        return
    
    # Обработка выдачи бонуса админом
    if context.user_data.get('awaiting_admin_bonus') and role == "admin":
        target_id = context.user_data.get('admin_give_bonus_to')
        try:
            amount = int(text.strip())
            if amount > 0:
                await UserManager.add_balance(target_id, amount, f"Бонус от администратора")
                await send_new_message(context, user_id, f"✅ Бонус {amount}₽ выдан пользователю {target_id}", KeyboardBuilder.admin_panel())
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text=f"🎉 Администратор выдал вам бонус {amount}₽!",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
            else:
                await send_new_message(context, user_id, "❌ Сумма должна быть положительной", KeyboardBuilder.admin_panel())
        except ValueError:
            await send_new_message(context, user_id, "❌ Введите число", KeyboardBuilder.admin_panel())
        
        context.user_data.pop('awaiting_admin_bonus', None)
        context.user_data.pop('admin_give_bonus_to', None)
        return
    
    # Обработка создания тикета
    if context.user_data.get('awaiting_ticket_message'):
        subject = context.user_data.get('ticket_subject', 'Общий вопрос')
        del context.user_data['awaiting_ticket_message']
        del context.user_data['ticket_subject']
        
        # Создаём тикет
        ticket_id = await UserManager.create_ticket(user_id, subject, text)
        
        # Отправляем в группу
        user = await UserManager.get(user_id)
        user_info = f"👤 <b>Новый тикет #{ticket_id}</b>\n\n"
        user_info += f"🆔 ID: <code>{user_id}</code>\n"
        user_info += f"📛 Имя: {user.get('first_name', '—')}\n"
        user_info += f"📱 Юзернейм: @{user.get('username', '—')}\n"
        user_info += f"🏷️ Тема: {subject}\n\n"
        user_info += f"📝 <b>Сообщение:</b>\n{text}\n"
        
        await context.bot.send_message(
            chat_id=config.TICKET_GROUP_ID,
            text=user_info,
            reply_markup=KeyboardBuilder.ticket_admin_actions(ticket_id, user_id),
            parse_mode=ParseMode.HTML
        )
        
        await send_new_message(
            context,
            user_id,
            f"✅ Ваше обращение #{ticket_id} принято!\n\nАдминистратор ответит в ближайшее время.",
            KeyboardBuilder.back()
        )
        return
    
    # Обработка ответа админа на тикет
    if context.user_data.get('awaiting_ticket_reply'):
        ticket_id = context.user_data.get('replying_to_ticket')
        target_user = context.user_data.get('replying_to_user')
        del context.user_data['awaiting_ticket_reply']
        del context.user_data['replying_to_ticket']
        del context.user_data['replying_to_user']
        
        # Отправляем ответ пользователю
        try:
            await context.bot.send_message(
                chat_id=target_user,
                text=f"📝 <b>Ответ администратора:</b>\n\n{text}",
                parse_mode=ParseMode.HTML
            )
            await query.answer("✅ Ответ отправлен", show_alert=True)
        except Exception as e:
            await query.answer("❌ Ошибка отправки", show_alert=True)
        
        await query.edit_message_text(
            f"✅ Ответ отправлен пользователю {target_user}.",
            reply_markup=None
        )
        return
    
    # Обработка добавления тестера
    if context.user_data.get('awaiting_tester_add') and role == "admin":
        try:
            target_id = int(text.strip())
            await UserManager.add_tester(target_id)
            await send_new_message(context, user_id, f"✅ Тестер {target_id} добавлен", KeyboardBuilder.admin_panel())
        except:
            await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.admin_panel())
        context.user_data.pop('awaiting_tester_add', None)
        return
    
    # Обработка удаления тестера
    if context.user_data.get('awaiting_tester_remove') and role == "admin":
        try:
            target_id = int(text.strip())
            await UserManager.remove_tester(target_id)
            await send_new_message(context, user_id, f"✅ Тестер {target_id} удален", KeyboardBuilder.admin_panel())
        except:
            await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.admin_panel())
        context.user_data.pop('awaiting_tester_remove', None)
        return
    
    # Обработка редактирования текста приветствия
    if context.user_data.get('awaiting_welcome_edit') and role == "admin":
        if await ContentManager.update_welcome_text(text):
            await send_new_message(context, user_id, "✅ Текст обновлен", KeyboardBuilder.admin_panel())
        else:
            await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.admin_panel())
        context.user_data.pop('awaiting_welcome_edit', None)
        return
    
    # Обработка рассылки
    if context.user_data.get('awaiting_mailing') and role == "admin":
        context.user_data['mailing_text'] = text
        await send_new_message(context, user_id, "📢 Подтвердите рассылку", KeyboardBuilder.admin_confirm_mailing())
        context.user_data.pop('awaiting_mailing', None)
        return
    
    # Обработка редактирования услуги
    if context.user_data.get('editing_service') and context.user_data.get('editing_field') and (role == "admin" or role == "tester"):
        sid = context.user_data['editing_service']
        field = context.user_data['editing_field']
        service = await ContentManager.get_service_type(sid)
        if service:
            update_data = {
                "name": service["name"], "emoji": service["emoji"],
                "description": service["description"], "icon": service["icon"],
                "enabled": service["enabled"], "sort_order": service["sort_order"]
            }
            try:
                if field == "name":
                    update_data["name"] = text
                elif field == "emoji":
                    update_data["emoji"] = text
                elif field == "desc":
                    update_data["description"] = text
                elif field == "order":
                    update_data["sort_order"] = int(text)
                
                if await ContentManager.update_service_type(sid, update_data):
                    await send_new_message(context, user_id, "✅ Обновлено", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
                else:
                    await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
        context.user_data.pop('editing_service', None)
        context.user_data.pop('editing_field', None)
        return
    
    # Обработка редактирования тарифа
    if context.user_data.get('editing_plan') and context.user_data.get('editing_field') and (role == "admin" or role == "tester"):
        pid = context.user_data['editing_plan']
        field = context.user_data['editing_field']
        plan = await ContentManager.get_plan(pid)
        if plan:
            update_data = {
                "name": plan["name"], "days": plan["days"], "price": plan["price"],
                "emoji": plan["emoji"], "description": plan["description"],
                "photo_id": plan["photo_id"], "service_type": plan["service_type"]
            }
            try:
                if field == "name":
                    update_data["name"] = text
                elif field == "price":
                    update_data["price"] = int(text)
                elif field == "days":
                    update_data["days"] = int(text)
                elif field == "emoji":
                    update_data["emoji"] = text
                elif field == "desc":
                    update_data["description"] = text
                
                if await ContentManager.update_plan(pid, update_data):
                    await send_new_message(context, user_id, "✅ Обновлено", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
                else:
                    await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
            except ValueError:
                await send_new_message(context, user_id, "❌ Неверный формат", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                await send_new_message(context, user_id, "❌ Ошибка", KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel())
        context.user_data.pop('editing_plan', None)
        context.user_data.pop('editing_field', None)
        return

# ==================== ФУНКЦИЯ РАССЫЛКИ ====================

async def start_mailing(context: ContextTypes.DEFAULT_TYPE, admin_id: int, text: str):
    users = await UserManager.get_all_users()
    total = len(users)
    sent = failed = blocked = 0
    
    await send_new_message(context, admin_id, f"📢 Рассылка запущена. Всего: {total}")
    
    for user in users:
        if user.get("banned"):
            blocked += 1
            continue
        try:
            msg = await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
                parse_mode=ParseMode.HTML
            )
            sent += 1
            asyncio.create_task(schedule_message_deletion(context, user["user_id"], msg.message_id, config.AUTO_DELETE_BOT_MESSAGES))
        except:
            failed += 1
        if (sent + failed) % 10 == 0:
            await send_new_message(context, admin_id, f"✅ {sent}, ❌ {failed}, 🔒 {blocked}")
    
    await send_new_message(context, admin_id, f"✅ Готово. Отправлено: {sent}, ошибок: {failed}, забанены: {blocked}")

# ==================== FASTAPI ЭНДПОИНТЫ ====================

@app.on_event("startup")
async def startup():
    global telegram_app
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК PLES VPN BOT v17.0 (С БАЛАНСОМ)")
    logger.info("=" * 60)
    
    await keep_alive.initialize()
    asyncio.create_task(keep_alive.ping_self())
    
    if await db.init():
        logger.info("✅ База данных готова")
    else:
        logger.error("❌ Ошибка БД")
        return
    
    await crypto.check_connection()
    
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, text_message_handler))
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    webhook_url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await telegram_app.bot.set_webhook(url=webhook_url)
    
    asyncio.create_task(check_pending_payments())
    
    logger.info(f"✅ Вебхук: {webhook_url}")
    logger.info(f"✅ Админы: {config.ADMIN_IDS}")
    logger.info(f"✅ Группа тикетов: {config.TICKET_GROUP_ID}")
    logger.info(f"✅ Статус бота: {'ВКЛЮЧЕН' if config.BOT_ENABLED else 'ВЫКЛЮЧЕН'}")
    logger.info(f"✅ Режим техработ: {'ВКЛЮЧЕН' if config.MAINTENANCE_MODE else 'ВЫКЛЮЧЕН'}")
    logger.info("✅ Бот готов!")

@app.on_event("shutdown")
async def shutdown():
    if telegram_app:
        await telegram_app.stop()
    await keep_alive.cleanup()

@app.post(config.WEBHOOK_PATH)
async def webhook(request: Request):
    if not telegram_app:
        return {"ok": False}
    try:
        json_data = await request.json()
        update = Update.de_json(json_data, telegram_app.bot)
        await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")
        return {"ok": False}

@app.get("/")
async def home():
    return {
        "status": "online", 
        "version": "17.0",
        "bot_enabled": config.BOT_ENABLED,
        "maintenance_mode": config.MAINTENANCE_MODE,
        "ticket_group": config.TICKET_GROUP_ID
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy", 
        "uptime": int(time.time() - startup_time),
        "bot_enabled": config.BOT_ENABLED,
        "maintenance_mode": config.MAINTENANCE_MODE
    }

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("ples_vpn_bot_balance:app", host="0.0.0.0", port=port)

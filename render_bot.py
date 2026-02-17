#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║         🌟 PLES VPN BOT v8.0 - С РОЛЬЮ ТЕСТЕРА                ║
║     Тестеры могут управлять контентом, но с ограничениями     ║
║     Автоматическое удаление роли при злоупотреблениях         ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import asyncio
import logging
import secrets
import qrcode
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
from py3xui import AsyncApi

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
    ADMIN_IDS = [8443743937]  # Полные админы
    TESTER_IDS = []  # ID тестеров (будут добавляться через админку)
    REQUIRED_CHANNEL = "@numberbor"
    BOT_USERNAME = "Playinc_bot"
    
    # CryptoBot
    CRYPTOBOT_TOKEN = "533707:AAyjZJjRSCxePyVGl6WYFx3rfWqgxZLhjvi"
    CRYPTOBOT_API = "https://pay.crypt.bot/api"
    
    # 3x-UI Panel
    XUI_PANEL_URL = os.environ.get("XUI_PANEL_URL", "http://your-server.com:2053")
    XUI_USERNAME = os.environ.get("XUI_USERNAME", "admin")
    XUI_PASSWORD = os.environ.get("XUI_PASSWORD", "admin")
    XUI_EXTERNAL_IP = os.environ.get("XUI_EXTERNAL_IP", "your-server.com")
    XUI_SERVER_PORT = int(os.environ.get("XUI_SERVER_PORT", "443"))
    XUI_INBOUND_ID = int(os.environ.get("XUI_INBOUND_ID", "1"))
    XUI_SERVER_NAME = os.environ.get("XUI_SERVER_NAME", "Ples VPN")
    
    # База данных
    DB_PATH = "/tmp/ples_vpn.db"
    
    # Пробный период
    TRIAL_DAYS = 6
    
    # Реферальная система
    REFERRAL_BONUS_DAYS = 3
    
    # URL
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://ples-vpn.onrender.com")
    WEBHOOK_PATH = "/webhook"
    
    # Настройки автоудаления
    AUTO_DELETE_SHORT = 30
    AUTO_DELETE_ORDER = 1800
    
    # Настройки пинга
    PING_INTERVAL = 300
    
    # Настройки для тестеров
    TESTER_ACTION_LIMIT = 10  # Максимум действий за час
    TESTER_ACTION_WINDOW = 3600  # Окно в секундах (1 час)
    TESTER_DELETE_LIMIT = 5  # Максимум удалений за день
    TESTER_DELETE_WINDOW = 86400  # Окно в секундах (24 часа)

config = Config()

# ==================== СИСТЕМА МОНИТОРИНГА ТЕСТЕРОВ ====================

class TesterMonitor:
    """Класс для отслеживания действий тестеров"""
    
    def __init__(self):
        self.actions = defaultdict(list)  # user_id -> [timestamps]
        self.deletions = defaultdict(list)  # user_id -> [timestamps]
        self.warnings = defaultdict(int)  # user_id -> warning_count
    
    def log_action(self, user_id: int):
        """Логирование действия тестера"""
        now = time.time()
        self.actions[user_id].append(now)
        # Очищаем старые записи
        self.actions[user_id] = [t for t in self.actions[user_id] if now - t < config.TESTER_ACTION_WINDOW]
    
    def log_deletion(self, user_id: int):
        """Логирование удаления тестером"""
        now = time.time()
        self.deletions[user_id].append(now)
        self.deletions[user_id] = [t for t in self.deletions[user_id] if now - t < config.TESTER_DELETE_WINDOW]
    
    def check_action_limit(self, user_id: int) -> Tuple[bool, str]:
        """Проверка лимита действий"""
        action_count = len(self.actions[user_id])
        if action_count >= config.TESTER_ACTION_LIMIT:
            return False, f"⚠️ Лимит действий ({config.TESTER_ACTION_LIMIT} в час) исчерпан. Подождите."
        return True, f"✅ Осталось действий: {config.TESTER_ACTION_LIMIT - action_count}"
    
    def check_delete_limit(self, user_id: int) -> Tuple[bool, str]:
        """Проверка лимита удалений"""
        delete_count = len(self.deletions[user_id])
        if delete_count >= config.TESTER_DELETE_LIMIT:
            return False, f"⚠️ Лимит удалений ({config.TESTER_DELETE_LIMIT} в день) исчерпан."
        return True, f"✅ Осталось удалений: {config.TESTER_DELETE_LIMIT - delete_count}"
    
    def add_warning(self, user_id: int) -> int:
        """Добавление предупреждения тестеру"""
        self.warnings[user_id] += 1
        return self.warnings[user_id]
    
    def should_remove_tester(self, user_id: int) -> bool:
        """Проверка, нужно ли удалить тестера"""
        # Если много удалений за короткое время
        if len(self.deletions[user_id]) >= config.TESTER_DELETE_LIMIT:
            return True
        # Если много предупреждений
        if self.warnings[user_id] >= 3:
            return True
        return False
    
    def reset_tester(self, user_id: int):
        """Сброс статистики тестера (при удалении роли)"""
        if user_id in self.actions:
            del self.actions[user_id]
        if user_id in self.deletions:
            del self.deletions[user_id]
        if user_id in self.warnings:
            del self.warnings[user_id]

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
            logger.info("🔍 Проверка подключения к CryptoBot...")
            
            response = await asyncio.to_thread(
                requests.get, url, headers=self.headers, timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    app_info = result.get("result", {})
                    logger.info(f"✅ CryptoBot доступен: {app_info.get('app_name')}")
                    return True
                else:
                    error = result.get("error", {})
                    logger.error(f"❌ Ошибка API: {error}")
                    return False
            else:
                logger.error(f"❌ HTTP ошибка {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к CryptoBot: {e}")
            return False
    
    async def create_invoice(self, amount_rub: float, payload: str) -> Optional[Dict]:
        try:
            if amount_rub <= 0:
                logger.error(f"❌ Некорректная сумма: {amount_rub}")
                return None
            
            if not payload:
                logger.error("❌ Отсутствует payload")
                return None
            
            url = f"{self.api_url}/createInvoice"
            
            data = {
                "asset": "USDT",
                "amount": str(amount_rub),
                "currency_type": "fiat",
                "fiat": "RUB",
                "accepted_assets": "USDT,TON,BTC",
                "description": f"Оплата на {amount_rub} RUB",
                "payload": payload,
                "expires_in": 3600,
                "allow_comments": False,
                "allow_anonymous": False
            }
            
            logger.info(f"📤 Запрос в CryptoBot: {amount_rub} RUB")
            
            response = await asyncio.to_thread(
                requests.post, url, headers=self.headers, json=data, timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    invoice_data = result["result"]
                    logger.info(f"✅ Чек создан: ID={invoice_data['invoice_id']}")
                    return invoice_data
                else:
                    error = result.get("error", {})
                    logger.error(f"❌ Ошибка API: {error}")
                    return None
            else:
                logger.error(f"❌ HTTP ошибка {response.status_code}")
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
                        status = items[0].get("status")
                        logger.info(f"📊 Статус чека {invoice_id}: {status}")
                        return status
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения статуса: {e}")
            return None
    
    async def check_payment(self, invoice_id: int) -> bool:
        status = await self.get_invoice_status(invoice_id)
        return status == "paid"

crypto = CryptoPay(config.CRYPTOBOT_TOKEN)

# ==================== 3X-UI МЕНЕДЖЕР ====================

class XUIManager:
    def __init__(self):
        self.async_api = None
        self._initialized = False
    
    async def initialize(self):
        try:
            logger.info("🔌 Подключение к 3x-UI...")
            
            self.async_api = AsyncApi(
                config.XUI_PANEL_URL,
                config.XUI_USERNAME,
                config.XUI_PASSWORD
            )
            
            await self.async_api.login()
            
            logger.info("✅ Подключено к 3x-UI")
            self._initialized = True
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к 3x-UI: {e}")
            return False
    
    async def get_inbound(self):
        try:
            if not self._initialized:
                await self.initialize()
            
            inbound = await self.async_api.inbound.get_by_id(config.XUI_INBOUND_ID)
            return inbound
        except Exception as e:
            logger.error(f"❌ Ошибка получения inbound: {e}")
            return None
    
    async def create_client(self, user_id: int, days: int) -> Tuple[bool, str, Optional[str], Optional[str]]:
        try:
            if not self._initialized:
                await self.initialize()
            
            inbound = await self.get_inbound()
            if not inbound:
                return False, "❌ Ошибка конфигурации сервера", None, None
            
            email = f"user_{user_id}_{int(datetime.now().timestamp())}"
            expiry_time = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)
            
            client_data = {
                "email": email,
                "enable": True,
                "expiryTime": expiry_time,
                "totalGB": 0,
                "limitIp": 0,
                "flow": "xtls-rprx-vision"
            }
            
            await self.async_api.client.add(
                inbound_id=config.XUI_INBOUND_ID,
                client_data=client_data
            )
            
            await asyncio.sleep(1)
            inbound = await self.get_inbound()
            
            client_uuid = None
            for client in inbound.settings.clients:
                if client.email == email:
                    client_uuid = client.id
                    break
            
            if not client_uuid:
                return False, "❌ Клиент создан, но UUID не найден", None, email
            
            config_str = self._generate_config(inbound, client_uuid, email)
            
            logger.info(f"✅ Клиент {email} создан")
            return True, "✅ Клиент создан", config_str, email
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания клиента: {e}")
            return False, f"❌ Ошибка: {str(e)}", None, None
    
    def _generate_config(self, inbound, user_uuid: str, user_email: str) -> str:
        try:
            if hasattr(inbound, 'stream_settings') and inbound.stream_settings:
                reality_settings = inbound.stream_settings.reality_settings
                
                if reality_settings:
                    public_key = reality_settings.get("settings", {}).get("publicKey")
                    server_names = reality_settings.get("serverNames", [])
                    short_ids = reality_settings.get("shortIds", [])
                    
                    website_name = server_names[0] if server_names else "addons.mozilla.org"
                    short_id = short_ids[0] if short_ids else ""
                    
                    config_str = (
                        f"vless://{user_uuid}@{config.XUI_EXTERNAL_IP}:{config.XUI_SERVER_PORT}"
                        f"?type=tcp&security=reality&pbk={public_key}&fp=chrome&sni={website_name}"
                        f"&sid={short_id}&spx=%2F#{config.XUI_SERVER_NAME}-{user_email}"
                    )
                    return config_str
            
            config_str = (
                f"vless://{user_uuid}@{config.XUI_EXTERNAL_IP}:{config.XUI_SERVER_PORT}"
                f"?type=tcp&security=none#{config.XUI_SERVER_NAME}-{user_email}"
            )
            return config_str
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации конфига: {e}")
            return f"Ошибка генерации конфига: {e}"
    
    def generate_qr_code(self, config_str: str) -> Optional[BytesIO]:
        try:
            qr = qrcode.QRCode(
                version=1,
                box_size=10,
                border=5,
                error_correction=qrcode.constants.ERROR_CORRECT_L
            )
            qr.add_data(config_str)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            bio = BytesIO()
            bio.name = 'vpn_qr.png'
            img.save(bio, 'PNG')
            bio.seek(0)
            return bio
        except Exception as e:
            logger.error(f"❌ Ошибка генерации QR: {e}")
            return None

xui_manager = XUIManager()

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
                
                # 👤 Таблица пользователей с ролями
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        subscribe_until TEXT,
                        trial_used INTEGER DEFAULT 0,
                        banned INTEGER DEFAULT 0,
                        role TEXT DEFAULT 'user',
                        selected_server TEXT DEFAULT 'netherlands',
                        selected_protocol TEXT DEFAULT 'OpenVPN',
                        referred_by INTEGER,
                        referral_code TEXT UNIQUE,
                        referral_count INTEGER DEFAULT 0,
                        last_active TEXT,
                        last_message_id INTEGER,
                        vpn_email TEXT,
                        vpn_config TEXT,
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
                        plan_id TEXT,
                        amount_rub INTEGER,
                        status TEXT DEFAULT 'pending',
                        payload TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        paid_at TEXT,
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
                
                # 📋 Таблица действий тестеров (логи)
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS tester_actions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        action TEXT,
                        target TEXT,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                await db.commit()
                
                await self._init_default_data(db)
                
                cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = await cursor.fetchall()
                logger.info(f"✅ Созданы таблицы: {[t[0] for t in tables]}")
                
                self._initialized = True
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка создания БД: {e}")
            return False
    
    async def _init_default_data(self, db):
        try:
            # Добавляем типы услуг
            default_services = [
                ("vpn", "VPN", "🌍", "Быстрый и безопасный VPN", "🛡️", 1, 1),
                ("proxy_tg", "Прокси для Telegram", "📱", "Обход блокировок Telegram", "🔌", 1, 2),
                ("antijammer", "Антиглушилки", "📡", "Защита от глушилок", "🛜", 1, 3),
                ("website", "Для сайтов", "🌐", "Доступ к сайтам", "🔓", 1, 4)
            ]
            
            for s in default_services:
                await db.execute('''
                    INSERT OR IGNORE INTO service_types (id, name, emoji, description, icon, enabled, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', s)
            
            # Добавляем тарифы
            default_plans = [
                ("vpn_1month", "🌱 1 месяц", 30, 299, "🌱", 1, "Базовый тариф", None, "vpn"),
                ("vpn_3month", "🌿 3 месяца", 90, 699, "🌿", 1, "Популярный тариф", None, "vpn"),
                ("vpn_6month", "🌳 6 месяцев", 180, 1199, "🌳", 1, "Выгодный тариф", None, "vpn"),
                ("vpn_12month", "🏝️ 12 месяцев", 365, 1999, "🏝️", 1, "Максимальный тариф", None, "vpn")
            ]
            
            for p in default_plans:
                await db.execute('''
                    INSERT OR IGNORE INTO plans (id, name, days, price, emoji, enabled, description, photo_id, service_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', p)
            
            # Добавляем приветственный текст
            welcome_text = (
                f"🌟 <b>Ples VPN </b>\n\n"
                f"🌍 <b>Серверы:</b>\n"
                f"🇳🇱 Нидерланды • 🇺🇸 США • 🇩🇪 Германия\n"
                f"🇬🇧 UK • 🇸🇬 Сингапур • 🇯🇵 Япония\n\n"
                f"⚡ <b>Протоколы:</b> OpenVPN • WireGuard • IKEv2\n\n"
                f"🎁 <b>Пробный период:</b> 6 дней\n\n"
                f"👥 <b>Рефералы:</b> +3 дня за друга\n\n"
                f"💳 <b>Оплата:</b> криптовалюта (USDT/TON/BTC)"
            )
            
            await db.execute('''
                INSERT OR IGNORE INTO content (key, value) VALUES (?, ?)
            ''', ("welcome_text", welcome_text))
            
            await db.commit()
            
        except Exception as e:
            logger.error(f"Ошибка при инициализации данных: {e}")
    
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
            logger.error(f"Ошибка fetch_one: {e}")
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
    async def get_all_users() -> List[Dict]:
        try:
            return await db.fetch_all("SELECT * FROM users ORDER BY reg_date DESC")
        except Exception as e:
            logger.error(f"Ошибка get_all_users: {e}")
            return []
    
    @staticmethod
    async def get_by_referral_code(code: str) -> Optional[Dict]:
        try:
            return await db.fetch_one("SELECT * FROM users WHERE referral_code = ?", (code,))
        except Exception as e:
            logger.error(f"Ошибка get_by_referral_code: {e}")
            return None
    
    @staticmethod
    async def get_role(user_id: int) -> str:
        """Получить роль пользователя"""
        user = await UserManager.get(user_id)
        if user:
            return user.get("role", "user")
        
        # Проверяем, есть ли в списках
        if user_id in config.ADMIN_IDS:
            return "admin"
        if user_id in config.TESTER_IDS:
            return "tester"
        return "user"
    
    @staticmethod
    async def set_role(user_id: int, role: str):
        """Установить роль пользователя"""
        await db.execute(
            "UPDATE users SET role = ? WHERE user_id = ?",
            (role, user_id)
        )
        logger.info(f"👤 Роль пользователя {user_id} изменена на {role}")
    
    @staticmethod
    async def add_tester(user_id: int):
        """Добавить тестера"""
        config.TESTER_IDS.append(user_id)
        await UserManager.set_role(user_id, "tester")
        logger.info(f"✅ Тестер {user_id} добавлен")
    
    @staticmethod
    async def remove_tester(user_id: int):
        """Удалить тестера"""
        if user_id in config.TESTER_IDS:
            config.TESTER_IDS.remove(user_id)
        await UserManager.set_role(user_id, "user")
        tester_monitor.reset_tester(user_id)
        logger.info(f"❌ Тестер {user_id} удален")
    
    @staticmethod
    async def create(user_id: int, username: str, first_name: str, referred_by: int = None):
        try:
            existing = await UserManager.get(user_id)
            if existing:
                return existing
            
            # Определяем роль
            role = "user"
            if user_id in config.ADMIN_IDS:
                role = "admin"
            elif user_id in config.TESTER_IDS:
                role = "tester"
            
            referral_code = str(user_id)
            
            await db.execute(
                """INSERT INTO users 
                   (user_id, username, first_name, referred_by, referral_code, last_active, role) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, first_name, referred_by, referral_code, datetime.now().isoformat(), role)
            )
            
            if referred_by and referred_by != user_id:
                try:
                    await db.execute(
                        "INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                        (referred_by, user_id)
                    )
                    await db.execute(
                        "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?",
                        (referred_by,)
                    )
                except Exception as e:
                    logger.error(f"Ошибка при сохранении реферала: {e}")
            
            logger.info(f"✅ Создан пользователь {user_id} с ролью {role}")
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
    
    @staticmethod
    async def save_vpn_info(user_id: int, vpn_email: str, vpn_config: str):
        try:
            await db.execute(
                "UPDATE users SET vpn_email = ?, vpn_config = ? WHERE user_id = ?",
                (vpn_email, vpn_config, user_id)
            )
            logger.info(f"💾 Сохранена VPN информация для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения VPN info: {e}")
    
    @staticmethod
    async def activate_trial(user_id: int) -> Tuple[bool, str]:
        try:
            user = await UserManager.get(user_id)
            
            if not user:
                return False, "❌ Пользователь не найден"
            
            if user.get("trial_used"):
                return False, "❌ Вы уже использовали пробный период"
            
            if user.get("subscribe_until"):
                try:
                    if datetime.fromisoformat(user["subscribe_until"]) > datetime.now():
                        return False, "❌ У вас уже есть активная подписка"
                except:
                    pass
            
            trial_end = datetime.now() + timedelta(days=config.TRIAL_DAYS)
            
            await db.execute(
                "UPDATE users SET subscribe_until = ?, trial_used = 1 WHERE user_id = ?",
                (trial_end.isoformat(), user_id)
            )
            
            return True, f"✅ Пробный период {config.TRIAL_DAYS} дней активирован!\n📅 Действует до: {trial_end.strftime('%d.%m.%Y')}"
            
        except Exception as e:
            logger.error(f"Ошибка activate_trial: {e}")
            return False, "❌ Ошибка активации"
    
    @staticmethod
    async def give_subscription(user_id: int, days: int, admin_give: bool = False):
        try:
            user = await UserManager.get(user_id)
            
            if not user:
                return None
            
            if user.get("subscribe_until") and not admin_give:
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
            
            if not admin_give and user.get("referred_by"):
                referrer_id = user["referred_by"]
                await UserManager.give_referral_bonus(referrer_id)
            
            return new_date
        except Exception as e:
            logger.error(f"Ошибка give_subscription: {e}")
            return None
    
    @staticmethod
    async def give_referral_bonus(referrer_id: int):
        try:
            referrer = await UserManager.get(referrer_id)
            if not referrer:
                return
            
            if referrer.get("subscribe_until"):
                try:
                    old_date = datetime.fromisoformat(referrer["subscribe_until"])
                    new_date = old_date + timedelta(days=config.REFERRAL_BONUS_DAYS)
                except:
                    new_date = datetime.now() + timedelta(days=config.REFERRAL_BONUS_DAYS)
            else:
                new_date = datetime.now() + timedelta(days=config.REFERRAL_BONUS_DAYS)
            
            await db.execute(
                "UPDATE users SET subscribe_until = ? WHERE user_id = ?",
                (new_date.isoformat(), referrer_id)
            )
            
            logger.info(f"🎁 Реферальный бонус {config.REFERRAL_BONUS_DAYS} дней начислен пользователю {referrer_id}")
            
        except Exception as e:
            logger.error(f"Ошибка начисления реферального бонуса: {e}")
    
    @staticmethod
    async def ban_user(user_id: int):
        try:
            await db.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
            logger.info(f"🔒 Пользователь {user_id} забанен")
        except Exception as e:
            logger.error(f"Ошибка ban_user: {e}")
    
    @staticmethod
    async def unban_user(user_id: int):
        try:
            await db.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
            logger.info(f"🔓 Пользователь {user_id} разбанен")
        except Exception as e:
            logger.error(f"Ошибка unban_user: {e}")
    
    @staticmethod
    async def get_stats() -> Dict:
        users = await UserManager.get_all_users()
        
        total = len(users)
        active = 0
        banned = 0
        trial = 0
        testers = 0
        admins = 0
        
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
        
        return {
            "total": total,
            "active": active,
            "banned": banned,
            "trial": trial,
            "testers": testers,
            "admins": admins,
            "conversion": round(active / total * 100, 1) if total else 0
        }
    
    @staticmethod
    async def save_crypto_payment(user_id: int, invoice_id: int, plan_id: str, amount_rub: int, payload: str):
        try:
            await db.execute(
                "INSERT INTO crypto_payments (user_id, invoice_id, plan_id, amount_rub, payload) VALUES (?, ?, ?, ?, ?)",
                (user_id, invoice_id, plan_id, amount_rub, payload)
            )
            logger.info(f"💰 Сохранен платеж {invoice_id} для пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка save_crypto_payment: {e}")
            raise
    
    @staticmethod
    async def confirm_crypto_payment(invoice_id: int):
        try:
            await db.execute(
                "UPDATE crypto_payments SET status = 'paid', paid_at = ? WHERE invoice_id = ?",
                (datetime.now().isoformat(), invoice_id)
            )
            logger.info(f"✅ Платеж {invoice_id} подтвержден")
        except Exception as e:
            logger.error(f"Ошибка confirm_crypto_payment: {e}")
    
    @staticmethod
    async def get_pending_payments():
        try:
            return await db.fetch_all(
                "SELECT * FROM crypto_payments WHERE status = 'pending' AND datetime(created_at) > datetime('now', '-1 day')"
            )
        except Exception as e:
            logger.error(f"Ошибка get_pending_payments: {e}")
            return []

# ==================== МЕНЕДЖЕР КОНТЕНТА ====================

class ContentManager:
    @staticmethod
    async def get_welcome_text() -> str:
        try:
            content = await db.fetch_one("SELECT value FROM content WHERE key = 'welcome_text'")
            if content:
                return content["value"]
            return "🌟 Добро пожаловать!"
        except Exception as e:
            logger.error(f"Ошибка get_welcome_text: {e}")
            return "🌟 Добро пожаловать!"
    
    @staticmethod
    async def update_welcome_text(text: str):
        try:
            await db.execute(
                "INSERT OR REPLACE INTO content (key, value, updated_at) VALUES (?, ?, ?)",
                ("welcome_text", text, datetime.now().isoformat())
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка update_welcome_text: {e}")
            return False
    
    @staticmethod
    async def get_service_types() -> Dict:
        try:
            services = await db.fetch_all("SELECT * FROM service_types WHERE enabled = 1 ORDER BY sort_order")
            result = {}
            for s in services:
                result[s["id"]] = {
                    "name": s["name"],
                    "emoji": s["emoji"],
                    "description": s["description"],
                    "icon": s["icon"]
                }
            return result
        except Exception as e:
            logger.error(f"Ошибка get_service_types: {e}")
            return {}
    
    @staticmethod
    async def get_service_type(service_id: str) -> Optional[Dict]:
        try:
            return await db.fetch_one("SELECT * FROM service_types WHERE id = ?", (service_id,))
        except Exception as e:
            logger.error(f"Ошибка get_service_type: {e}")
            return None
    
    @staticmethod
    async def update_service_type(service_id: str, data: Dict):
        try:
            await db.execute(
                """UPDATE service_types SET 
                   name = ?, emoji = ?, description = ?, icon = ?, enabled = ?, sort_order = ? 
                   WHERE id = ?""",
                (data["name"], data["emoji"], data["description"], data["icon"], 
                 data.get("enabled", 1), data.get("sort_order", 0), service_id)
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка update_service_type: {e}")
            return False
    
    @staticmethod
    async def get_plans_by_service(service_type: str) -> Dict:
        try:
            plans = await db.fetch_all(
                "SELECT * FROM plans WHERE enabled = 1 AND service_type = ? ORDER BY days",
                (service_type,)
            )
            result = {}
            for p in plans:
                result[p["id"]] = {
                    "name": p["name"],
                    "days": p["days"],
                    "price": p["price"],
                    "emoji": p["emoji"],
                    "description": p["description"],
                    "photo_id": p["photo_id"]
                }
            return result
        except Exception as e:
            logger.error(f"Ошибка get_plans_by_service: {e}")
            return {}
    
    @staticmethod
    async def get_all_plans() -> Dict:
        try:
            plans = await db.fetch_all("SELECT * FROM plans WHERE enabled = 1 ORDER BY service_type, days")
            result = {}
            for p in plans:
                result[p["id"]] = {
                    "name": p["name"],
                    "days": p["days"],
                    "price": p["price"],
                    "emoji": p["emoji"],
                    "description": p["description"],
                    "photo_id": p["photo_id"],
                    "service_type": p["service_type"]
                }
            return result
        except Exception as e:
            logger.error(f"Ошибка get_all_plans: {e}")
            return {}
    
    @staticmethod
    async def get_plan(plan_id: str) -> Optional[Dict]:
        try:
            return await db.fetch_one("SELECT * FROM plans WHERE id = ?", (plan_id,))
        except Exception as e:
            logger.error(f"Ошибка get_plan: {e}")
            return None
    
    @staticmethod
    async def update_plan(plan_id: str, data: Dict):
        try:
            await db.execute(
                """UPDATE plans SET 
                   name = ?, days = ?, price = ?, emoji = ?, description = ?, photo_id = ?, service_type = ?, updated_at = ? 
                   WHERE id = ?""",
                (data["name"], data["days"], data["price"], data["emoji"], 
                 data["description"], data.get("photo_id"), data.get("service_type"), 
                 datetime.now().isoformat(), plan_id)
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка update_plan: {e}")
            return False
    
    @staticmethod
    async def update_plan_photo(plan_id: str, photo_id: str):
        try:
            await db.execute(
                "UPDATE plans SET photo_id = ?, updated_at = ? WHERE id = ?",
                (photo_id, datetime.now().isoformat(), plan_id)
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка update_plan_photo: {e}")
            return False
    
    @staticmethod
    async def get_servers() -> Dict:
        try:
            servers = await db.fetch_all("SELECT * FROM servers WHERE enabled = 1 ORDER BY id")
            result = {}
            for s in servers:
                result[s["id"]] = {
                    "name": s["name"],
                    "flag": s["flag"],
                    "city": s["city"],
                    "load": s["load"],
                    "ping": s["ping"]
                }
            return result
        except Exception as e:
            logger.error(f"Ошибка get_servers: {e}")
            return {}

# ==================== ДАННЫЕ ====================

PROTOCOLS = ["OpenVPN", "WireGuard", "IKEv2"]

# ==================== ФУНКЦИИ ДЛЯ АВТОУДАЛЕНИЯ ====================

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = config.AUTO_DELETE_SHORT):
    """Запланировать удаление сообщения"""
    try:
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"🗑️ Автоудаление: сообщение {message_id} удалено через {delay} сек")
    except Exception as e:
        logger.debug(f"Не удалось автоудалить сообщение {message_id}: {e}")

# ==================== ФУНКЦИИ ДЛЯ ПРОВЕРКИ ТЕСТЕРОВ ====================

async def check_tester_action(user_id: int, context: ContextTypes.DEFAULT_TYPE, action_type: str = "action") -> Tuple[bool, str]:
    """Проверка лимитов тестера перед действием"""
    role = await UserManager.get_role(user_id)
    
    if role == "admin":
        return True, "✅ Админ может всё"
    
    if role == "tester":
        # Логируем действие
        tester_monitor.log_action(user_id)
        
        # Проверяем лимит действий
        ok, msg = tester_monitor.check_action_limit(user_id)
        if not ok:
            return False, msg
        
        # Если это удаление, проверяем отдельно
        if action_type == "delete":
            tester_monitor.log_deletion(user_id)
            ok, msg = tester_monitor.check_delete_limit(user_id)
            if not ok:
                # Добавляем предупреждение
                warnings = tester_monitor.add_warning(user_id)
                if tester_monitor.should_remove_tester(user_id):
                    await UserManager.remove_tester(user_id)
                    await send_new_message(
                        context,
                        user_id,
                        "⚠️ <b>Вы были лишены роли тестера</b>\n\n"
                        "Причина: превышение лимитов удалений.\n"
                        "Обратитесь к администратору для восстановления доступа.",
                        None,
                        auto_delete=False
                    )
                return False, f"❌ {msg}"
        
        return True, msg
    
    return False, "❌ У вас нет прав для этого действия"

# ==================== КЛАВИАТУРЫ ====================

class KeyboardBuilder:
    @staticmethod
    async def main(role: str = "user"):
        """Главное меню с учетом роли"""
        services = await ContentManager.get_service_types()
        
        buttons = []
        for sid, service in services.items():
            buttons.append([InlineKeyboardButton(
                f"{service['icon']} {service['emoji']} {service['name']}",
                callback_data=f"service_{sid}"
            )])
        
        buttons.append([InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile")])
        buttons.append([InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")])
        buttons.append([InlineKeyboardButton("📞 ПОДДЕРЖКА", callback_data="support")])
        
        if role == "admin":
            buttons.append([InlineKeyboardButton("⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_menu")])
        elif role == "tester":
            buttons.append([InlineKeyboardButton("🧪 ТЕСТЕР ПАНЕЛЬ", callback_data="tester_menu")])
        
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    async def service_plans(service_type: str):
        """Планы для конкретной услуги"""
        plans = await ContentManager.get_plans_by_service(service_type)
        services = await ContentManager.get_service_types()
        service = services.get(service_type, {"name": "Услуга", "emoji": "🔹"})
        
        buttons = []
        for pid, plan in plans.items():
            buttons.append([InlineKeyboardButton(
                f"{plan['emoji']} {plan['name']} - {plan['price']}₽",
                callback_data=f"buy_{pid}"
            )])
        
        buttons.append([InlineKeyboardButton("🎁 ПРОБНЫЙ ПЕРИОД 6 ДНЕЙ", callback_data="trial")])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    async def servers():
        servers = await ContentManager.get_servers()
        buttons = []
        for sid, server in servers.items():
            load = "🟢" if server["load"] < 30 else "🟡" if server["load"] < 60 else "🔴"
            buttons.append([InlineKeyboardButton(
                f"{server['flag']} {server['name']} • {load} {server['load']}% • {server['ping']}ms",
                callback_data=f"server_{sid}"
            )])
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
    def referrals(referral_code: str):
        ref_link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{referral_code}"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 РЕФЕРАЛЬНАЯ ССЫЛКА", url=ref_link)],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="referral_stats")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def payment(plan_name: str, plan_price: int, invoice_url: str, invoice_id: int):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 ОПЛАТИТЬ КРИПТОВАЛЮТОЙ", url=invoice_url)],
            [InlineKeyboardButton("✅ Я ОПЛАТИЛ", callback_data=f"check_crypto_{invoice_id}")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def admin_panel():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ", callback_data="admin_users")],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 РАССЫЛКА", callback_data="admin_mailing")],
            [InlineKeyboardButton("📝 РЕДАКТИРОВАТЬ ТЕКСТ", callback_data="admin_edit_welcome")],
            [InlineKeyboardButton("🏷️ УПРАВЛЕНИЕ УСЛУГАМИ", callback_data="admin_services")],
            [InlineKeyboardButton("💰 УПРАВЛЕНИЕ ТАРИФАМИ", callback_data="admin_plans")],
            [InlineKeyboardButton("🧪 УПРАВЛЕНИЕ ТЕСТЕРАМИ", callback_data="admin_testers")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def tester_panel():
        """Панель для тестеров"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 ПРОСМОТР СТАТИСТИКИ", callback_data="tester_stats")],
            [InlineKeyboardButton("🏷️ УПРАВЛЕНИЕ УСЛУГАМИ", callback_data="tester_services")],
            [InlineKeyboardButton("💰 УПРАВЛЕНИЕ ТАРИФАМИ", callback_data="tester_plans")],
            [InlineKeyboardButton("👥 ПРОСМОТР ПОЛЬЗОВАТЕЛЕЙ", callback_data="tester_users")],
            [InlineKeyboardButton("📝 МОИ ДЕЙСТВИЯ", callback_data="tester_actions")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def admin_testers():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 СПИСОК ТЕСТЕРОВ", callback_data="admin_tester_list")],
            [InlineKeyboardButton("➕ ДОБАВИТЬ ТЕСТЕРА", callback_data="admin_tester_add")],
            [InlineKeyboardButton("❌ УДАЛИТЬ ТЕСТЕРА", callback_data="admin_tester_remove")],
            [InlineKeyboardButton("📊 СТАТИСТИКА ТЕСТЕРОВ", callback_data="admin_tester_stats")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")]
        ])
    
    @staticmethod
    async def admin_services():
        services = await ContentManager.get_service_types()
        buttons = []
        for sid, service in services.items():
            buttons.append([InlineKeyboardButton(
                f"{service['icon']} {service['emoji']} {service['name']}",
                callback_data=f"admin_edit_service_{sid}"
            )])
        buttons.append([InlineKeyboardButton("➕ ДОБАВИТЬ УСЛУГУ", callback_data="admin_add_service")])
        buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_service_edit(service_id: str, service: Dict):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 ИЗМЕНИТЬ НАЗВАНИЕ", callback_data=f"admin_service_name_{service_id}")],
            [InlineKeyboardButton("🎨 ИЗМЕНИТЬ ЭМОДЗИ", callback_data=f"admin_service_emoji_{service_id}")],
            [InlineKeyboardButton("📋 ИЗМЕНИТЬ ОПИСАНИЕ", callback_data=f"admin_service_desc_{service_id}")],
            [InlineKeyboardButton("🔢 ИЗМЕНИТЬ ПОРЯДОК", callback_data=f"admin_service_order_{service_id}")],
            [InlineKeyboardButton("❌ УДАЛИТЬ УСЛУГУ", callback_data=f"admin_service_delete_{service_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_services")]
        ])
    
    @staticmethod
    def tester_service_edit(service_id: str, service: Dict):
        """Панель редактирования для тестеров (ограниченная)"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 ИЗМЕНИТЬ НАЗВАНИЕ", callback_data=f"tester_service_name_{service_id}")],
            [InlineKeyboardButton("🎨 ИЗМЕНИТЬ ЭМОДЗИ", callback_data=f"tester_service_emoji_{service_id}")],
            [InlineKeyboardButton("📋 ИЗМЕНИТЬ ОПИСАНИЕ", callback_data=f"tester_service_desc_{service_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_services")]
        ])
    
    @staticmethod
    async def admin_plans():
        plans = await ContentManager.get_all_plans()
        services = await ContentManager.get_service_types()
        
        buttons = []
        for sid, service in services.items():
            buttons.append([InlineKeyboardButton(
                f"📌 {service['emoji']} {service['name']}",
                callback_data=f"admin_service_plans_{sid}"
            )])
        
        buttons.append([InlineKeyboardButton("➕ ДОБАВИТЬ ТАРИФ", callback_data="admin_add_plan")])
        buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_plan_edit(plan_id: str, plan: Dict):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 ИЗМЕНИТЬ НАЗВАНИЕ", callback_data=f"admin_plan_name_{plan_id}")],
            [InlineKeyboardButton("💰 ИЗМЕНИТЬ ЦЕНУ", callback_data=f"admin_plan_price_{plan_id}")],
            [InlineKeyboardButton("📅 ИЗМЕНИТЬ ДНИ", callback_data=f"admin_plan_days_{plan_id}")],
            [InlineKeyboardButton("🎨 ИЗМЕНИТЬ ЭМОДЗИ", callback_data=f"admin_plan_emoji_{plan_id}")],
            [InlineKeyboardButton("📋 ИЗМЕНИТЬ ОПИСАНИЕ", callback_data=f"admin_plan_desc_{plan_id}")],
            [InlineKeyboardButton("🖼️ ИЗМЕНИТЬ ФОТО", callback_data=f"admin_plan_photo_{plan_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="admin_plans")]
        ])
    
    @staticmethod
    def tester_plan_edit(plan_id: str, plan: Dict):
        """Панель редактирования тарифов для тестеров"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 ИЗМЕНИТЬ НАЗВАНИЕ", callback_data=f"tester_plan_name_{plan_id}")],
            [InlineKeyboardButton("💰 ИЗМЕНИТЬ ЦЕНУ", callback_data=f"tester_plan_price_{plan_id}")],
            [InlineKeyboardButton("📅 ИЗМЕНИТЬ ДНИ", callback_data=f"tester_plan_days_{plan_id}")],
            [InlineKeyboardButton("🎨 ИЗМЕНИТЬ ЭМОДЗИ", callback_data=f"tester_plan_emoji_{plan_id}")],
            [InlineKeyboardButton("📋 ИЗМЕНИТЬ ОПИСАНИЕ", callback_data=f"tester_plan_desc_{plan_id}")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_plans")]
        ])
    
    @staticmethod
    def admin_users(users: List[Dict], page: int = 0):
        buttons = []
        start = page * 5
        end = start + 5
        
        for user in users[start:end]:
            name = user.get('first_name', '—')[:10]
            role_emoji = {
                "admin": "👑",
                "tester": "🧪",
                "user": "👤"
            }.get(user.get('role', 'user'), "👤")
            status = "🔴" if user.get('banned') else "🟢"
            sub = "✅" if user.get('subscribe_until') and datetime.fromisoformat(user['subscribe_until']) > datetime.now() else "❌"
            btn_text = f"{role_emoji}{status}{sub} {name} (@{user.get('username', '—')})"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"admin_user_{user['user_id']}")])
        
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}"))
        if end < len(users):
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}"))
        if nav:
            buttons.append(nav)
        
        buttons.append([InlineKeyboardButton("🔙 В АДМИНКУ", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def tester_users(users: List[Dict], page: int = 0):
        """Просмотр пользователей для тестеров (без возможности редактирования)"""
        buttons = []
        start = page * 5
        end = start + 5
        
        for user in users[start:end]:
            name = user.get('first_name', '—')[:10]
            status = "🟢" if not user.get('banned') else "🔴"
            sub = "✅" if user.get('subscribe_until') and datetime.fromisoformat(user['subscribe_until']) > datetime.now() else "❌"
            btn_text = f"{status}{sub} {name} (@{user.get('username', '—')})"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"tester_view_user_{user['user_id']}")])
        
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"tester_users_page_{page-1}"))
        if end < len(users):
            nav.append(InlineKeyboardButton("▶️", callback_data=f"tester_users_page_{page+1}"))
        if nav:
            buttons.append(nav)
        
        buttons.append([InlineKeyboardButton("🔙 В ТЕСТЕР ПАНЕЛЬ", callback_data="tester_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_user_actions(user_id: int, is_banned: bool):
        buttons = [
            [InlineKeyboardButton("📅 ВЫДАТЬ ПОДПИСКУ", callback_data=f"admin_give_{user_id}")],
            [InlineKeyboardButton("🔒 ЗАБАНИТЬ" if not is_banned else "🔓 РАЗБАНИТЬ", 
                                 callback_data=f"admin_ban_{user_id}" if not is_banned else f"admin_unban_{user_id}")],
            [InlineKeyboardButton("🔙 К СПИСКУ", callback_data="admin_users")]
        ]
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def tester_view_user(user_id: int, user_data: Dict):
        """Просмотр пользователя для тестеров (без возможности редактирования)"""
        buttons = [
            [InlineKeyboardButton("📊 СТАТИСТИКА ПОЛЬЗОВАТЕЛЯ", callback_data=f"tester_user_stats_{user_id}")],
            [InlineKeyboardButton("🔙 К СПИСКУ", callback_data="tester_users")]
        ]
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_give_sub(user_id: int):
        buttons = []
        plans = PLANS.items()
        for pid, plan in plans:
            buttons.append([InlineKeyboardButton(
                f"{plan['name']} - {plan['days']} дней",
                callback_data=f"admin_give_{pid}_{user_id}"
            )])
        buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data=f"admin_user_{user_id}")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_confirm_mailing():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ РАССЫЛКУ", callback_data="admin_mailing_confirm")],
            [InlineKeyboardButton("❌ ОТМЕНИТЬ", callback_data="admin_menu")]
        ])
    
    @staticmethod
    def back():
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== ДАННЫЕ ====================

PLANS = {
    "1month": {"name": "🌱 1 месяц", "days": 30, "price": 299},
    "3month": {"name": "🌿 3 месяца", "days": 90, "price": 699},
    "6month": {"name": "🌳 6 месяцев", "days": 180, "price": 1199},
    "12month": {"name": "🏝️ 12 месяцев", "days": 365, "price": 1999}
}

# ==================== FASTAPI ПРИЛОЖЕНИЕ ====================

app = FastAPI()
telegram_app = None
startup_time = time.time()

# ==================== ФУНКЦИИ ДЛЯ СООБЩЕНИЙ ====================

async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        user = await UserManager.get(chat_id)
        if user and user.get("last_message_id"):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=user["last_message_id"])
            except:
                pass
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")

async def send_new_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, keyboard=None, photo=None, auto_delete: bool = True):
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
            delay = config.AUTO_DELETE_ORDER if "ЗАКАЗ" in text or "Оплата" in text else config.AUTO_DELETE_SHORT
            asyncio.create_task(schedule_message_deletion(context, chat_id, msg.message_id, delay))
        
        return msg
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return None

# ==================== ФУНКЦИИ ДЛЯ РАССЫЛКИ ====================

async def start_mailing(context: ContextTypes.DEFAULT_TYPE, admin_id: int, message_text: str):
    users = await UserManager.get_all_users()
    total = len(users)
    sent = 0
    failed = 0
    blocked = 0
    
    await send_new_message(
        context,
        admin_id,
        f"📢 <b>РАССЫЛКА ЗАПУЩЕНА</b>\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"⏳ Идет отправка..."
    )
    
    for i, user in enumerate(users):
        user_id = user["user_id"]
        
        if user.get("banned"):
            blocked += 1
            continue
        
        try:
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.HTML
            )
            sent += 1
            asyncio.create_task(schedule_message_deletion(context, user_id, msg.message_id))
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
        
        if (i + 1) % 10 == 0:
            await send_new_message(
                context,
                admin_id,
                f"📢 <b>СТАТУС РАССЫЛКИ</b>\n\n"
                f"✅ Отправлено: {sent}\n"
                f"❌ Ошибок: {failed}\n"
                f"🔒 Забанены: {blocked}\n"
                f"⏳ Осталось: {total - i - 1}"
            )
        
        await asyncio.sleep(0.05)
    
    await send_new_message(
        context,
        admin_id,
        f"📢 <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
        f"✅ Успешно: {sent}\n"
        f"❌ Ошибок: {failed}\n"
        f"🔒 Пропущено (забанены): {blocked}\n"
        f"👥 Всего: {total}"
    )

# ==================== ФОНОВАЯ ПРОВЕРКА ПЛАТЕЖЕЙ ====================

async def check_pending_payments():
    while True:
        try:
            await asyncio.sleep(30)
            
            pending = await UserManager.get_pending_payments()
            for payment in pending:
                try:
                    is_paid = await crypto.check_payment(payment["invoice_id"])
                    
                    if is_paid and payment["status"] == "pending":
                        user_id = payment["user_id"]
                        plan_id = payment["plan_id"]
                        plans = await ContentManager.get_all_plans()
                        plan = plans.get(plan_id, list(plans.values())[0])
                        
                        # Создаем клиента в 3x-UI
                        success, msg, config_str, vpn_email = await xui_manager.create_client(user_id, plan["days"])
                        
                        if success and config_str:
                            await UserManager.save_vpn_info(user_id, vpn_email, config_str)
                            new_date = await UserManager.give_subscription(user_id, plan["days"])
                            await UserManager.confirm_crypto_payment(payment["invoice_id"])
                            
                            # Отправляем уведомление с конфигом
                            try:
                                await telegram_app.bot.send_message(
                                    chat_id=user_id,
                                    text=f"✅ <b>Оплата подтверждена!</b>\n\n"
                                         f"Услуга {plan['name']} активирована!\n"
                                         f"📅 Действует до: {new_date.strftime('%d.%m.%Y')}\n\n"
                                         f"🔗 <b>Ваш конфиг:</b>\n<code>{config_str}</code>",
                                    parse_mode=ParseMode.HTML
                                )
                                
                                # Отправляем QR-код
                                qr = xui_manager.generate_qr_code(config_str)
                                if qr:
                                    await telegram_app.bot.send_photo(
                                        chat_id=user_id,
                                        photo=qr,
                                        caption="📱 Отсканируйте QR-код для подключения"
                                    )
                            except Exception as e:
                                logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")
                        else:
                            logger.error(f"❌ Ошибка создания клиента для {user_id}: {msg}")
                            
                except Exception as e:
                    logger.error(f"Ошибка при обработке платежа {payment.get('invoice_id')}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка в фоновой проверке: {e}")
            await asyncio.sleep(60)

# ==================== ФОНОВАЯ ПРОВЕРКА ТЕСТЕРОВ ====================

async def check_tester_limits():
    """Фоновая проверка лимитов тестеров"""
    while True:
        try:
            await asyncio.sleep(3600)  # Каждый час
            
            # Проверяем, не нужно ли удалить тестеров
            for user_id in list(tester_monitor.warnings.keys()):
                if tester_monitor.should_remove_tester(user_id):
                    await UserManager.remove_tester(user_id)
                    
        except Exception as e:
            logger.error(f"Ошибка проверки тестеров: {e}")

# ==================== ОБРАБОТЧИКИ TELEGRAM ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        args = context.args
        
        logger.info(f"🚀 /start от {user.id}")
        
        referred_by = None
        if args and args[0].startswith("ref_"):
            try:
                ref_user_id = int(args[0].replace("ref_", ""))
                if ref_user_id != user.id:
                    referrer = await UserManager.get(ref_user_id)
                    if referrer:
                        referred_by = ref_user_id
                        logger.info(f"👥 Реферальный переход: {referred_by} -> {user.id}")
            except Exception as e:
                logger.error(f"Ошибка обработки реферального кода: {e}")
        
        await UserManager.create(user.id, user.username or "", user.first_name or "", referred_by)
        
        db_user = await UserManager.get(user.id)
        if db_user and db_user.get("banned"):
            await update.message.reply_text("⛔ Доступ заблокирован")
            return
        
        welcome_text = await ContentManager.get_welcome_text()
        
        role = await UserManager.get_role(user.id)
        await send_new_message(context, user.id, welcome_text, await KeyboardBuilder.main(role))
        
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")
        try:
            await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
        except:
            pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        logger.info(f"🔘 Кнопка: {data} от {user_id}")
        
        role = await UserManager.get_role(user_id)
        is_admin = role == "admin"
        is_tester = role == "tester"
        
        # ===== НАВИГАЦИЯ =====
        if data == "back_main":
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
            service = services.get(service_id, {"name": "Услуга", "description": ""})
            
            text = (
                f"{service.get('icon', '🔹')} {service.get('emoji', '📌')} <b>{service['name']}</b>\n\n"
                f"{service.get('description', '')}\n\n"
                f"Выберите тариф:"
            )
            
            await send_new_message(
                context,
                user_id,
                text,
                await KeyboardBuilder.service_plans(service_id)
            )
        
        # ===== ПРОБНЫЙ ПЕРИОД =====
        elif data == "trial":
            success, msg = await UserManager.activate_trial(user_id)
            await send_new_message(
                context, 
                user_id, 
                msg, 
                await KeyboardBuilder.main(role)
            )
        
        # ===== ПОКУПКА =====
        elif data.startswith("buy_"):
            plan_id = data.replace("buy_", "")
            plans = await ContentManager.get_all_plans()
            
            if plan_id in plans:
                plan = plans[plan_id]
                
                try:
                    if plan["price"] <= 0:
                        logger.error(f"❌ Некорректная цена тарифа {plan_id}: {plan['price']}")
                        await send_new_message(
                            context, 
                            user_id, 
                            "❌ Ошибка: некорректная стоимость",
                            await KeyboardBuilder.main(role)
                        )
                        return
                    
                    payload = json.dumps({
                        "user_id": user_id,
                        "plan_id": plan_id,
                        "timestamp": datetime.now().timestamp()
                    })
                    
                    logger.info(f"💰 Попытка создания чека: {plan['price']} RUB")
                    
                    invoice = await crypto.create_invoice(plan["price"], payload)
                    
                    if invoice and invoice.get("invoice_id"):
                        await UserManager.save_crypto_payment(
                            user_id=user_id,
                            invoice_id=invoice["invoice_id"],
                            plan_id=plan_id,
                            amount_rub=plan["price"],
                            payload=payload
                        )
                        
                        text = (
                            f"💎 <b>Оплата {plan['name']}</b>\n\n"
                            f"💰 Сумма: {plan['price']} ₽\n"
                            f"📝 {plan.get('description', '')}\n"
                            f"⏱ Счет действителен 1 час\n\n"
                            f"1. Нажмите «Оплатить криптовалютой»\n"
                            f"2. Выберите USDT/TON/BTC\n"
                            f"3. После оплаты нажмите «Я оплатил»"
                        )
                        
                        if plan.get("photo_id"):
                            await send_new_message(
                                context, 
                                user_id, 
                                text, 
                                KeyboardBuilder.payment(plan['name'], plan['price'], invoice["bot_invoice_url"], invoice["invoice_id"]),
                                photo=plan["photo_id"]
                            )
                        else:
                            await send_new_message(
                                context, 
                                user_id, 
                                text, 
                                KeyboardBuilder.payment(plan['name'], plan['price'], invoice["bot_invoice_url"], invoice["invoice_id"])
                            )
                    else:
                        logger.error(f"❌ Не удалось создать чек для пользователя {user_id}")
                        await send_new_message(
                            context, 
                            user_id, 
                            "❌ Ошибка создания чека. Попробуйте позже.",
                            await KeyboardBuilder.main(role)
                        )
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка при создании чека: {e}")
                    await send_new_message(
                        context, 
                        user_id, 
                        "❌ Произошла ошибка. Попробуйте позже.",
                        await KeyboardBuilder.main(role)
                    )
            else:
                await send_new_message(context, user_id, "❌ Тариф не найден", await KeyboardBuilder.main(role))
        
        elif data.startswith("check_crypto_"):
            try:
                invoice_id = int(data.replace("check_crypto_", ""))
                is_paid = await crypto.check_payment(invoice_id)
                
                if is_paid:
                    payment = await db.fetch_one(
                        "SELECT * FROM crypto_payments WHERE invoice_id = ?", 
                        (invoice_id,)
                    )
                    
                    if payment and payment["status"] == "pending":
                        plan_id = payment["plan_id"]
                        plans = await ContentManager.get_all_plans()
                        plan = plans.get(plan_id, list(plans.values())[0])
                        
                        # Создаем клиента в 3x-UI
                        success, msg, config_str, vpn_email = await xui_manager.create_client(user_id, plan["days"])
                        
                        if success and config_str:
                            await UserManager.save_vpn_info(user_id, vpn_email, config_str)
                            new_date = await UserManager.give_subscription(user_id, plan["days"])
                            await UserManager.confirm_crypto_payment(invoice_id)
                            
                            await send_new_message(
                                context,
                                user_id,
                                f"✅ <b>Оплата подтверждена!</b>\n\n"
                                f"Услуга {plan['name']} активирована!\n"
                                f"📅 Действует до: {new_date.strftime('%d.%m.%Y')}\n\n"
                                f"🔗 <b>Ваш конфиг:</b>\n<code>{config_str}</code>",
                                await KeyboardBuilder.main(role)
                            )
                            
                            # Отправляем QR-код
                            qr = xui_manager.generate_qr_code(config_str)
                            if qr:
                                await context.bot.send_photo(
                                    chat_id=user_id,
                                    photo=qr,
                                    caption="📱 Отсканируйте QR-код для подключения"
                                )
                            
                            await query.answer("✅ Платеж найден!", show_alert=True)
                        else:
                            await send_new_message(
                                context,
                                user_id,
                                f"❌ Ошибка создания VPN: {msg}\nОбратитесь в поддержку.",
                                await KeyboardBuilder.main(role)
                            )
                    else:
                        await query.answer("❌ Платеж уже обработан", show_alert=True)
                else:
                    await query.answer("❌ Платеж не найден. Если вы оплатили, подождите минуту.", show_alert=True)
            except Exception as e:
                logger.error(f"Ошибка проверки платежа: {e}")
                await query.answer("❌ Ошибка проверки платежа", show_alert=True)
        
        # ===== ПРОФИЛЬ =====
        elif data == "profile":
            try:
                user = await UserManager.get(user_id)
                
                if not user:
                    username = query.from_user.username or ""
                    first_name = query.from_user.first_name or ""
                    user = await UserManager.create(user_id, username, first_name)
                
                if not user:
                    await send_new_message(context, user_id, "❌ Ошибка загрузки профиля", KeyboardBuilder.back())
                    return
                
                if user.get("subscribe_until"):
                    try:
                        end = datetime.fromisoformat(user["subscribe_until"])
                        days = (end - datetime.now()).days
                        status = "✅ Активна" if days > 0 else "❌ Истекла"
                        end_str = end.strftime("%d.%m.%Y")
                    except Exception as e:
                        logger.error(f"Ошибка парсинга даты: {e}")
                        days = 0
                        status = "❌ Нет подписки"
                        end_str = "-"
                else:
                    days = 0
                    status = "❌ Нет подписки"
                    end_str = "-"
                
                role_emoji = "👑" if role == "admin" else "🧪" if role == "tester" else "👤"
                
                text = (
                    f"{role_emoji} <b>ПРОФИЛЬ</b>\n\n"
                    f"📊 Статус: {status}\n"
                    f"📅 Действует до: {end_str}\n"
                    f"⏱ Осталось: {max(0, days)} дн.\n"
                    f"🆔 ID: <code>{user_id}</code>\n"
                )
                
                # Добавляем конфиг если есть активная подписка
                if user.get("vpn_config") and days > 0:
                    config_link = user["vpn_config"]
                    short_link = config_link[:50] + "..." if len(config_link) > 50 else config_link
                    text += f"\n🔗 <b>Ваш конфиг:</b>\n<code>{short_link}</code>"
                    
                    # Создаем клавиатуру с кнопкой показа конфига
                    profile_keyboard = KeyboardBuilder.back()
                    profile_keyboard.inline_keyboard.insert(0, [InlineKeyboardButton("🔗 ПОКАЗАТЬ КОНФИГ", callback_data="show_config")])
                    await send_new_message(context, user_id, text, profile_keyboard)
                else:
                    await send_new_message(context, user_id, text, KeyboardBuilder.back())
                    
            except Exception as e:
                logger.error(f"Ошибка в профиле: {e}")
                await send_new_message(context, user_id, "❌ Ошибка загрузки профиля", KeyboardBuilder.back())
        
        # ===== ПОКАЗАТЬ КОНФИГ =====
        elif data == "show_config":
            user = await UserManager.get(user_id)
            if user and user.get("vpn_config") and user.get("subscribe_until"):
                try:
                    if datetime.fromisoformat(user["subscribe_until"]) > datetime.now():
                        config_str = user["vpn_config"]
                        await send_new_message(
                            context,
                            user_id,
                            f"🔗 <b>Ваш конфиг:</b>\n<code>{config_str}</code>",
                            KeyboardBuilder.back(),
                            auto_delete=False
                        )
                        
                        # Отправляем QR-код
                        qr = xui_manager.generate_qr_code(config_str)
                        if qr:
                            await context.bot.send_photo(
                                chat_id=user_id,
                                photo=qr,
                                caption="📱 QR-код для подключения"
                            )
                        return
                except:
                    pass
            await query.answer("❌ Конфиг не найден или подписка не активна", show_alert=True)
        
        # ===== РЕФЕРАЛЫ =====
        elif data == "referrals":
            try:
                user = await UserManager.get(user_id)
                if user:
                    referrals = await db.fetch_all(
                        "SELECT COUNT(*) as count FROM referrals WHERE referrer_id = ?",
                        (user_id,)
                    )
                    ref_count = referrals[0]["count"] if referrals else 0
                    
                    text = (
                        f"👥 <b>РЕФЕРАЛЬНАЯ ПРОГРАММА</b>\n\n"
                        f"👤 <b>Ваш ID:</b> <code>{user_id}</code>\n"
                        f"📊 <b>Приглашено друзей:</b> {ref_count}\n"
                        f"🎁 <b>Бонус:</b> +{config.REFERRAL_BONUS_DAYS} дня за каждого\n\n"
                        f"🔗 <b>Ваша ссылка:</b>\n"
                        f"<code>https://t.me/{config.BOT_USERNAME}?start=ref_{user_id}</code>\n\n"
                        f"📤 <i>Отправьте эту ссылку друзьям</i>"
                    )
                    
                    await send_new_message(
                        context, 
                        user_id, 
                        text,
                        KeyboardBuilder.referrals(str(user_id))
                    )
                else:
                    await send_new_message(context, user_id, "❌ Пользователь не найден", KeyboardBuilder.back())
            except Exception as e:
                logger.error(f"Ошибка в рефералах: {e}")
                await send_new_message(context, user_id, "❌ Ошибка загрузки", KeyboardBuilder.back())
        
        elif data == "referral_stats":
            try:
                user = await UserManager.get(user_id)
                if user:
                    referrals = await db.fetch_all(
                        "SELECT * FROM referrals WHERE referrer_id = ? ORDER BY created_at DESC",
                        (user_id,)
                    )
                    
                    text = "👥 <b>ВАШИ РЕФЕРАЛЫ</b>\n\n"
                    if not referrals:
                        text += "Пока нет рефералов"
                    else:
                        for ref in referrals[:10]:
                            ref_user = await UserManager.get(ref["referred_id"])
                            ref_name = ref_user.get("first_name", "—") if ref_user else "—"
                            
                            has_sub = False
                            if ref_user and ref_user.get("subscribe_until"):
                                try:
                                    if datetime.fromisoformat(ref_user["subscribe_until"]) > datetime.now():
                                        has_sub = True
                                except:
                                    pass
                            
                            status = "✅" if has_sub else "⏳"
                            date_str = ref["created_at"][:10] if ref["created_at"] else "—"
                            text += f"{status} {ref_name} - {date_str}\n"
                    
                    await send_new_message(context, user_id, text, KeyboardBuilder.back())
                else:
                    await send_new_message(context, user_id, "❌ Пользователь не найден", KeyboardBuilder.back())
            except Exception as e:
                logger.error(f"Ошибка в статистике рефералов: {e}")
                await send_new_message(context, user_id, "❌ Ошибка загрузки", KeyboardBuilder.back())
        
        # ===== ПОДДЕРЖКА =====
        elif data == "support":
            await send_new_message(
                context, 
                user_id, 
                "📞 <b>ПОДДЕРЖКА</b>\n\n@vpn_support_bot",
                KeyboardBuilder.back()
            )
        
        # ===== ТЕСТЕР ПАНЕЛЬ =====
        elif data == "tester_menu" and is_tester:
            await send_new_message(
                context,
                user_id,
                "🧪 <b>ТЕСТЕР ПАНЕЛЬ</b>\n\n"
                "Выберите действие. У вас ограниченные права.\n"
                f"Осталось действий: {config.TESTER_ACTION_LIMIT - len(tester_monitor.actions[user_id])}",
                KeyboardBuilder.tester_panel()
            )
        
        # ===== ТЕСТЕР: ПРОСМОТР СТАТИСТИКИ =====
        elif data == "tester_stats" and is_tester:
            # Проверяем лимиты тестера
            ok, msg = await check_tester_action(user_id, context)
            if not ok:
                await query.answer(msg, show_alert=True)
                return
            
            stats = await UserManager.get_stats()
            text = (
                f"📊 <b>СТАТИСТИКА БОТА</b>\n\n"
                f"👥 Всего: {stats['total']}\n"
                f"✅ Активных: {stats['active']}\n"
                f"🔒 Забанено: {stats['banned']}\n"
                f"🎁 Пробный: {stats['trial']}\n"
                f"👑 Админы: {stats['admins']}\n"
                f"🧪 Тестеры: {stats['testers']}\n"
                f"📈 Конверсия: {stats['conversion']}%"
            )
            await send_new_message(context, user_id, text, KeyboardBuilder.tester_panel())
        
        # ===== ТЕСТЕР: УПРАВЛЕНИЕ УСЛУГАМИ =====
        elif data == "tester_services" and is_tester:
            ok, msg = await check_tester_action(user_id, context)
            if not ok:
                await query.answer(msg, show_alert=True)
                return
            
            services = await ContentManager.get_service_types()
            text = "🏷️ <b>УПРАВЛЕНИЕ УСЛУГАМИ</b>\n\nВыберите услугу для редактирования:"
            
            buttons = []
            for sid, service in services.items():
                buttons.append([InlineKeyboardButton(
                    f"{service['icon']} {service['emoji']} {service['name']}",
                    callback_data=f"tester_edit_service_{sid}"
                )])
            buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_menu")])
            
            await send_new_message(context, user_id, text, InlineKeyboardMarkup(buttons))
        
        elif data.startswith("tester_edit_service_") and is_tester:
            service_id = data.replace("tester_edit_service_", "")
            service = await ContentManager.get_service_type(service_id)
            
            if service:
                text = (
                    f"🏷️ <b>РЕДАКТИРОВАНИЕ УСЛУГИ</b>\n\n"
                    f"Название: {service['name']}\n"
                    f"Эмодзи: {service['emoji']}\n"
                    f"Описание: {service['description']}\n\n"
                    f"Выберите что изменить:"
                )
                await send_new_message(
                    context,
                    user_id,
                    text,
                    KeyboardBuilder.tester_service_edit(service_id, service)
                )
            else:
                await send_new_message(context, user_id, "❌ Услуга не найдена", KeyboardBuilder.tester_panel())
        
        # ===== ТЕСТЕР: УПРАВЛЕНИЕ ТАРИФАМИ =====
        elif data == "tester_plans" and is_tester:
            ok, msg = await check_tester_action(user_id, context)
            if not ok:
                await query.answer(msg, show_alert=True)
                return
            
            plans = await ContentManager.get_all_plans()
            services = await ContentManager.get_service_types()
            
            text = "💰 <b>УПРАВЛЕНИЕ ТАРИФАМИ</b>\n\nВыберите тариф для редактирования:"
            
            buttons = []
            for pid, plan in plans.items():
                service = services.get(plan['service_type'], {"emoji": "📌"})
                buttons.append([InlineKeyboardButton(
                    f"{service['emoji']} {plan['emoji']} {plan['name']}",
                    callback_data=f"tester_edit_plan_{pid}"
                )])
            buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data="tester_menu")])
            
            await send_new_message(context, user_id, text, InlineKeyboardMarkup(buttons))
        
        elif data.startswith("tester_edit_plan_") and is_tester:
            plan_id = data.replace("tester_edit_plan_", "")
            plan = await ContentManager.get_plan(plan_id)
            
            if plan:
                text = (
                    f"💰 <b>РЕДАКТИРОВАНИЕ ТАРИФА</b>\n\n"
                    f"Название: {plan['name']}\n"
                    f"Цена: {plan['price']}₽\n"
                    f"Дней: {plan['days']}\n"
                    f"Эмодзи: {plan['emoji']}\n"
                    f"Описание: {plan['description']}\n\n"
                    f"Выберите что изменить:"
                )
                await send_new_message(
                    context,
                    user_id,
                    text,
                    KeyboardBuilder.tester_plan_edit(plan_id, plan)
                )
            else:
                await send_new_message(context, user_id, "❌ Тариф не найден", KeyboardBuilder.tester_panel())
        
        # ===== ТЕСТЕР: ПРОСМОТР ПОЛЬЗОВАТЕЛЕЙ =====
        elif data == "tester_users" and is_tester:
            ok, msg = await check_tester_action(user_id, context)
            if not ok:
                await query.answer(msg, show_alert=True)
                return
            
            users = await UserManager.get_all_users()
            await send_new_message(
                context,
                user_id,
                f"👥 <b>ПРОСМОТР ПОЛЬЗОВАТЕЛЕЙ ({len(users)})</b>",
                KeyboardBuilder.tester_users(users)
            )
        
        elif data.startswith("tester_users_page_") and is_tester:
            page = int(data.split("_")[-1])
            users = await UserManager.get_all_users()
            await send_new_message(
                context,
                user_id,
                f"👥 Страница {page+1}:",
                KeyboardBuilder.tester_users(users, page)
            )
        
        elif data.startswith("tester_view_user_") and is_tester:
            target_id = int(data.replace("tester_view_user_", ""))
            target = await UserManager.get(target_id)
            
            if target:
                sub = "Нет"
                if target.get("subscribe_until"):
                    try:
                        sub = datetime.fromisoformat(target["subscribe_until"]).strftime("%d.%m.%Y")
                    except:
                        pass
                
                text = (
                    f"👤 <b>ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ</b>\n\n"
                    f"🆔 ID: <code>{target_id}</code>\n"
                    f"📛 Имя: {target.get('first_name', '—')}\n"
                    f"📱 Юзернейм: @{target.get('username', '—')}\n"
                    f"📅 Регистрация: {target.get('reg_date', '—')[:10]}\n"
                    f"📆 Подписка до: {sub}\n"
                    f"🔒 Статус: {'🔴 ЗАБАНЕН' if target.get('banned') else '🟢 АКТИВЕН'}"
                )
                
                await send_new_message(
                    context,
                    user_id,
                    text,
                    KeyboardBuilder.tester_view_user(target_id, target)
                )
            else:
                await send_new_message(context, user_id, "❌ Пользователь не найден", KeyboardBuilder.tester_panel())
        
        # ===== ТЕСТЕР: МОИ ДЕЙСТВИЯ =====
        elif data == "tester_actions" and is_tester:
            actions = len(tester_monitor.actions[user_id])
            deletions = len(tester_monitor.deletions[user_id])
            warnings = tester_monitor.warnings[user_id]
            
            text = (
                f"📝 <b>ВАША АКТИВНОСТЬ</b>\n\n"
                f"📊 Действий за час: {actions}/{config.TESTER_ACTION_LIMIT}\n"
                f"🗑️ Удалений за день: {deletions}/{config.TESTER_DELETE_LIMIT}\n"
                f"⚠️ Предупреждений: {warnings}/3\n\n"
            )
            
            if warnings >= 3:
                text += "❌ Вы превысили лимиты и будете лишены роли тестера!"
            elif deletions >= config.TESTER_DELETE_LIMIT:
                text += "⚠️ Вы исчерпали лимит удалений!"
            
            await send_new_message(context, user_id, text, KeyboardBuilder.tester_panel())
        
        # ===== АДМИН ПАНЕЛЬ =====
        elif data == "admin_menu" and is_admin:
            await send_new_message(context, user_id, "⚙️ <b>АДМИН ПАНЕЛЬ</b>", KeyboardBuilder.admin_panel())
        
        # ===== АДМИН: УПРАВЛЕНИЕ ТЕСТЕРАМИ =====
        elif data == "admin_testers" and is_admin:
            await send_new_message(
                context,
                user_id,
                "🧪 <b>УПРАВЛЕНИЕ ТЕСТЕРАМИ</b>",
                KeyboardBuilder.admin_testers()
            )
        
        elif data == "admin_tester_list" and is_admin:
            testers = []
            for uid in config.TESTER_IDS:
                user = await UserManager.get(uid)
                if user:
                    actions = len(tester_monitor.actions[uid])
                    deletions = len(tester_monitor.deletions[uid])
                    warnings = tester_monitor.warnings[uid]
                    testers.append(f"• {user['first_name']} (@{user['username']}) - ID: {uid}\n"
                                  f"  Действий: {actions}, Удалений: {deletions}, Предупреждений: {warnings}")
            
            text = "👥 <b>СПИСОК ТЕСТЕРОВ</b>\n\n"
            if testers:
                text += "\n".join(testers)
            else:
                text += "Тестеров пока нет"
            
            await send_new_message(context, user_id, text, KeyboardBuilder.admin_panel())
        
        elif data == "admin_tester_add" and is_admin:
            await send_new_message(
                context,
                user_id,
                "➕ <b>ДОБАВЛЕНИЕ ТЕСТЕРА</b>\n\n"
                "Отправьте ID пользователя, которого хотите сделать тестером:",
                KeyboardBuilder.back()
            )
            context.user_data['awaiting_tester_add'] = True
        
        elif data == "admin_tester_remove" and is_admin:
            await send_new_message(
                context,
                user_id,
                "❌ <b>УДАЛЕНИЕ ТЕСТЕРА</b>\n\n"
                "Отправьте ID пользователя, которого хотите удалить из тестеров:",
                KeyboardBuilder.back()
            )
            context.user_data['awaiting_tester_remove'] = True
        
        elif data == "admin_tester_stats" and is_admin:
            total_testers = len(config.TESTER_IDS)
            active_testers = 0
            total_actions = 0
            total_deletions = 0
            
            for uid in config.TESTER_IDS:
                total_actions += len(tester_monitor.actions[uid])
                total_deletions += len(tester_monitor.deletions[uid])
                if not tester_monitor.should_remove_tester(uid):
                    active_testers += 1
            
            text = (
                f"📊 <b>СТАТИСТИКА ТЕСТЕРОВ</b>\n\n"
                f"👥 Всего тестеров: {total_testers}\n"
                f"✅ Активных: {active_testers}\n"
                f"📝 Всего действий: {total_actions}\n"
                f"🗑️ Всего удалений: {total_deletions}"
            )
            await send_new_message(context, user_id, text, KeyboardBuilder.admin_panel())
        
        # ... (остальные обработчики админки остаются без изменений)
        
        # ===== РЕДАКТИРОВАНИЕ УСЛУГ ТЕСТЕРАМИ =====
        elif data.startswith("tester_service_name_") and is_tester:
            service_id = data.replace("tester_service_name_", "")
            context.user_data['editing_service'] = service_id
            context.user_data['editing_field'] = 'name'
            await send_new_message(
                context,
                user_id,
                f"📝 Введите новое название для услуги:",
                KeyboardBuilder.back()
            )
        
        elif data.startswith("tester_service_emoji_") and is_tester:
            service_id = data.replace("tester_service_emoji_", "")
            context.user_data['editing_service'] = service_id
            context.user_data['editing_field'] = 'emoji'
            await send_new_message(
                context,
                user_id,
                f"🎨 Введите новый эмодзи для услуги:",
                KeyboardBuilder.back()
            )
        
        elif data.startswith("tester_service_desc_") and is_tester:
            service_id = data.replace("tester_service_desc_", "")
            context.user_data['editing_service'] = service_id
            context.user_data['editing_field'] = 'description'
            await send_new_message(
                context,
                user_id,
                f"📋 Введите новое описание для услуги:",
                KeyboardBuilder.back()
            )
        
        # ===== РЕДАКТИРОВАНИЕ ТАРИФОВ ТЕСТЕРАМИ =====
        elif data.startswith("tester_plan_name_") and is_tester:
            plan_id = data.replace("tester_plan_name_", "")
            context.user_data['editing_plan'] = plan_id
            context.user_data['editing_field'] = 'name'
            await send_new_message(
                context,
                user_id,
                f"📝 Введите новое название для тарифа:",
                KeyboardBuilder.back()
            )
        
        elif data.startswith("tester_plan_price_") and is_tester:
            plan_id = data.replace("tester_plan_price_", "")
            context.user_data['editing_plan'] = plan_id
            context.user_data['editing_field'] = 'price'
            await send_new_message(
                context,
                user_id,
                f"💰 Введите новую цену (только цифры):",
                KeyboardBuilder.back()
            )
        
        elif data.startswith("tester_plan_days_") and is_tester:
            plan_id = data.replace("tester_plan_days_", "")
            context.user_data['editing_plan'] = plan_id
            context.user_data['editing_field'] = 'days'
            await send_new_message(
                context,
                user_id,
                f"📅 Введите новое количество дней (только цифры):",
                KeyboardBuilder.back()
            )
        
        elif data.startswith("tester_plan_emoji_") and is_tester:
            plan_id = data.replace("tester_plan_emoji_", "")
            context.user_data['editing_plan'] = plan_id
            context.user_data['editing_field'] = 'emoji'
            await send_new_message(
                context,
                user_id,
                f"🎨 Введите новый эмодзи для тарифа:",
                KeyboardBuilder.back()
            )
        
        elif data.startswith("tester_plan_desc_") and is_tester:
            plan_id = data.replace("tester_plan_desc_", "")
            context.user_data['editing_plan'] = plan_id
            context.user_data['editing_field'] = 'description'
            await send_new_message(
                context,
                user_id,
                f"📋 Введите новое описание для тарифа:",
                KeyboardBuilder.back()
            )
        
    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        import traceback
        traceback.print_exc()
        try:
            await query.answer("❌ Произошла ошибка", show_alert=True)
        except:
            pass

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        if update.message.photo and context.user_data.get('editing_field') == 'photo':
            photo = update.message.photo[-1]
            if context.user_data.get('editing_plan'):
                plan_id = context.user_data['editing_plan']
                success = await ContentManager.update_plan_photo(plan_id, photo.file_id)
                if success:
                    await send_new_message(
                        context,
                        update.effective_chat.id,
                        "✅ Фото тарифа обновлено!",
                        KeyboardBuilder.admin_panel(),
                        auto_delete=True
                    )
                else:
                    await send_new_message(
                        context,
                        update.effective_chat.id,
                        "❌ Ошибка при обновлении фото",
                        KeyboardBuilder.admin_panel(),
                        auto_delete=True
                    )
                
                context.user_data.pop('editing_plan', None)
                context.user_data.pop('editing_field', None)
            return
        
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    role = await UserManager.get_role(user_id)
    
    user = await UserManager.get(user_id)
    if user and user.get("banned"):
        await update.message.reply_text("⛔ Доступ заблокирован")
        return
    
    # Обработка добавления тестера (админ)
    if context.user_data.get('awaiting_tester_add') and role == "admin":
        del context.user_data['awaiting_tester_add']
        try:
            target_id = int(text.strip())
            await UserManager.add_tester(target_id)
            await send_new_message(
                context,
                user_id,
                f"✅ Пользователь {target_id} назначен тестером!",
                KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        except Exception as e:
            await send_new_message(
                context,
                user_id,
                f"❌ Ошибка: {e}",
                KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        return
    
    # Обработка удаления тестера (админ)
    if context.user_data.get('awaiting_tester_remove') and role == "admin":
        del context.user_data['awaiting_tester_remove']
        try:
            target_id = int(text.strip())
            await UserManager.remove_tester(target_id)
            await send_new_message(
                context,
                user_id,
                f"✅ Пользователь {target_id} удален из тестеров!",
                KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        except Exception as e:
            await send_new_message(
                context,
                user_id,
                f"❌ Ошибка: {e}",
                KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        return
    
    # Обработка редактирования услуг (тестеры)
    if context.user_data.get('editing_service') and context.user_data.get('editing_field') and (role == "admin" or role == "tester"):
        service_id = context.user_data['editing_service']
        field = context.user_data['editing_field']
        service = await ContentManager.get_service_type(service_id)
        
        if not service:
            await send_new_message(context, user_id, "❌ Услуга не найдена", KeyboardBuilder.back(), auto_delete=True)
            context.user_data.pop('editing_service', None)
            context.user_data.pop('editing_field', None)
            return
        
        # Проверяем лимиты для тестера
        if role == "tester":
            ok, msg = await check_tester_action(user_id, context)
            if not ok:
                await send_new_message(context, user_id, msg, KeyboardBuilder.tester_panel(), auto_delete=True)
                context.user_data.pop('editing_service', None)
                context.user_data.pop('editing_field', None)
                return
        
        update_data = {
            "name": service["name"],
            "emoji": service["emoji"],
            "description": service["description"],
            "icon": service["icon"],
            "enabled": service["enabled"],
            "sort_order": service["sort_order"]
        }
        
        try:
            if field == 'name':
                update_data['name'] = text
            elif field == 'emoji':
                update_data['emoji'] = text
            elif field == 'description':
                update_data['description'] = text
            
            success = await ContentManager.update_service_type(service_id, update_data)
            
            if success:
                await send_new_message(
                    context,
                    user_id,
                    f"✅ {field} услуги обновлен!",
                    KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel(),
                    auto_delete=True
                )
            else:
                await send_new_message(
                    context,
                    user_id,
                    "❌ Ошибка при обновлении",
                    KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel(),
                    auto_delete=True
                )
        except Exception as e:
            logger.error(f"Ошибка при обновлении услуги: {e}")
            await send_new_message(
                context,
                user_id,
                "❌ Ошибка при обработке данных",
                KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        
        context.user_data.pop('editing_service', None)
        context.user_data.pop('editing_field', None)
        return
    
    # Обработка редактирования тарифов (тестеры)
    if context.user_data.get('editing_plan') and context.user_data.get('editing_field') and (role == "admin" or role == "tester"):
        plan_id = context.user_data['editing_plan']
        field = context.user_data['editing_field']
        plan = await ContentManager.get_plan(plan_id)
        
        if not plan:
            await send_new_message(context, user_id, "❌ Тариф не найден", KeyboardBuilder.back(), auto_delete=True)
            context.user_data.pop('editing_plan', None)
            context.user_data.pop('editing_field', None)
            return
        
        # Проверяем лимиты для тестера
        if role == "tester":
            action_type = "delete" if field == "delete" else "action"
            ok, msg = await check_tester_action(user_id, context, action_type)
            if not ok:
                await send_new_message(context, user_id, msg, KeyboardBuilder.tester_panel(), auto_delete=True)
                context.user_data.pop('editing_plan', None)
                context.user_data.pop('editing_field', None)
                return
        
        update_data = {
            "name": plan["name"],
            "days": plan["days"],
            "price": plan["price"],
            "emoji": plan["emoji"],
            "description": plan["description"],
            "photo_id": plan["photo_id"],
            "service_type": plan["service_type"]
        }
        
        try:
            if field == 'name':
                update_data['name'] = text
            elif field == 'price':
                update_data['price'] = int(text)
            elif field == 'days':
                update_data['days'] = int(text)
            elif field == 'emoji':
                update_data['emoji'] = text
            elif field == 'description':
                update_data['description'] = text
            
            success = await ContentManager.update_plan(plan_id, update_data)
            
            if success:
                await send_new_message(
                    context,
                    user_id,
                    f"✅ {field} тарифа обновлен!",
                    KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel(),
                    auto_delete=True
                )
            else:
                await send_new_message(
                    context,
                    user_id,
                    "❌ Ошибка при обновлении",
                    KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel(),
                    auto_delete=True
                )
        except Exception as e:
            logger.error(f"Ошибка при обновлении тарифа: {e}")
            await send_new_message(
                context,
                user_id,
                "❌ Ошибка при обработке данных",
                KeyboardBuilder.tester_panel() if role == "tester" else KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        
        context.user_data.pop('editing_plan', None)
        context.user_data.pop('editing_field', None)
        return
    
    # Обработка рассылки (админ)
    if context.user_data.get('awaiting_mailing') and role == "admin":
        del context.user_data['awaiting_mailing']
        context.user_data['mailing_text'] = text
        
        await send_new_message(
            context,
            user_id,
            f"📢 <b>ПОДТВЕРДИТЕ РАССЫЛКУ</b>\n\n"
            f"Текст сообщения:\n\n{text}\n\n"
            f"Отправить всем пользователям?",
            KeyboardBuilder.admin_confirm_mailing(),
            auto_delete=True
        )
        return
    
    # Обработка редактирования текста (админ)
    if context.user_data.get('awaiting_welcome_edit') and role == "admin":
        del context.user_data['awaiting_welcome_edit']
        
        success = await ContentManager.update_welcome_text(text)
        if success:
            await send_new_message(
                context,
                user_id,
                "✅ Текст приветствия обновлен!",
                KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        else:
            await send_new_message(
                context,
                user_id,
                "❌ Ошибка при обновлении текста",
                KeyboardBuilder.admin_panel(),
                auto_delete=True
            )
        return

# ==================== FASTAPI ЭНДПОИНТЫ ====================

@app.on_event("startup")
async def startup():
    global telegram_app
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК PLES VPN BOT v8.0 (С РОЛЬЮ ТЕСТЕРА)")
    logger.info("=" * 60)
    
    # Инициализация KeepAlive
    await keep_alive.initialize()
    asyncio.create_task(keep_alive.ping_self())
    
    # Проверка CryptoBot
    crypto_ok = await crypto.check_connection()
    if crypto_ok:
        logger.info("✅ CryptoBot подключен")
    else:
        logger.warning("⚠️ CryptoBot недоступен")
    
    # Инициализация базы данных
    if await db.init():
        logger.info("✅ База данных готова")
    else:
        logger.error("❌ Ошибка базы данных")
        return
    
    # Инициализация 3x-UI
    xui_ok = await xui_manager.initialize()
    if xui_ok:
        logger.info("✅ 3x-UI подключен")
    else:
        logger.warning("⚠️ 3x-UI недоступен")
    
    # Создание Telegram приложения
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, text_message_handler))
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Установка вебхука
    webhook_url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await telegram_app.bot.set_webhook(url=webhook_url)
    
    # Запуск фоновых задач
    asyncio.create_task(check_pending_payments())
    asyncio.create_task(check_tester_limits())
    
    logger.info(f"✅ Вебхук: {webhook_url}")
    logger.info(f"✅ Админы: {config.ADMIN_IDS}")
    logger.info(f"✅ Тестеры: {config.TESTER_IDS}")
    logger.info("✅ Бот готов!")
    logger.info("=" * 60)

@app.on_event("shutdown")
async def shutdown():
    if telegram_app:
        await telegram_app.stop()
    await keep_alive.cleanup()
    logger.info("🛑 Бот остановлен")

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
        "service": "Ples VPN Bot",
        "version": "8.0",
        "admins": config.ADMIN_IDS,
        "testers": len(config.TESTER_IDS),
        "trial_days": config.TRIAL_DAYS
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime": int(time.time() - startup_time)
    }

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "ples_vpn_bot_tester_role:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
)

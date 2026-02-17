#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║                    🌟 PLES VPN BOT v3.1                       ║
║        Исправленные кнопки • Рефералы как в AtlantaVPN        ║
╚═══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import asyncio
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
import aiosqlite
import requests

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
    REQUIRED_CHANNEL = "@numberbor"
    BOT_USERNAME = "Playinc_bot"
    
    # CryptoBot
    CRYPTOBOT_TOKEN = "533707:AAyjZJjRSCxePyVGl6WYFx3rfWqgxZLhjvi"
    CRYPTOBOT_API = "https://pay.crypt.bot/api"
    
    # База данных
    DB_PATH = "/tmp/ples_vpn.db"
    
    # Пробный период
    TRIAL_DAYS = 6
    
    # Реферальная система
    REFERRAL_BONUS_DAYS = 3
    
    # URL
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://ples-vpn.onrender.com")
    WEBHOOK_PATH = "/webhook"

config = Config()

# ==================== CRYPTOBOT КЛИЕНТ ====================

class CryptoPay:
    def __init__(self, token: str):
        self.token = token
        self.api_url = config.CRYPTOBOT_API
        self.headers = {
            "Crypto-Pay-API-Token": token,
            "Content-Type": "application/json"
        }
    
    async def create_invoice(self, amount_rub: float, payload: str) -> Optional[Dict]:
        try:
            url = f"{self.api_url}/createInvoice"
            data = {
                "asset": "USDT",
                "amount": str(amount_rub),
                "currency_type": "fiat",
                "fiat": "RUB",
                "accepted_assets": ["USDT", "TON", "BTC"],
                "description": f"Оплата VPN на {amount_rub} RUB",
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
            
            logger.error(f"Ошибка CryptoBot: {response.text}")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка создания счета: {e}")
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
                    return result["result"]["items"][0].get("status")
            
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
    
    async def init(self):
        try:
            logger.info("📦 Создание базы данных...")
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode = WAL")
                
                # Пользователи
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
                
                # Рефералы
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS referrals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        referrer_id INTEGER,
                        referred_id INTEGER,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Платежи
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
                        paid_at TEXT
                    )
                ''')
                
                # Индексы
                await db.execute('CREATE INDEX IF NOT EXISTS idx_referral_code ON users(referral_code)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_crypto_invoice ON crypto_payments(invoice_id)')
                
                await db.commit()
                
                cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = await cursor.fetchall()
                logger.info(f"✅ Созданы таблицы: {[t[0] for t in tables]}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка создания БД: {e}")
            return False
    
    async def execute(self, query: str, params: tuple = ()):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(query, params)
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка execute: {e}")
    
    async def fetch_one(self, query: str, params: tuple = ()):
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
    async def get_all_users() -> List[Dict]:
        try:
            return await db.fetch_all("SELECT * FROM users ORDER BY reg_date DESC")
        except Exception as e:
            logger.error(f"Ошибка get_all_users: {e}")
            return []
    
    @staticmethod
    async def get_by_referral_code(code: str) -> Optional[Dict]:
        return await db.fetch_one("SELECT * FROM users WHERE referral_code = ?", (code,))
    
    @staticmethod
    async def get_by_user_id(user_id: int) -> Optional[Dict]:
        return await UserManager.get(user_id)
    
    @staticmethod
    async def create(user_id: int, username: str, first_name: str, referred_by: int = None):
        try:
            existing = await UserManager.get(user_id)
            if existing:
                return existing
            
            # Генерируем реферальный код (используем user_id для простоты)
            referral_code = str(user_id)
            
            await db.execute(
                """INSERT INTO users 
                   (user_id, username, first_name, referred_by, referral_code, last_active) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
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
                
                # Отправляем уведомление рефереру
                try:
                    # Здесь будет уведомление
                    pass
                except:
                    pass
            
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
    
    @staticmethod
    async def update_server(user_id: int, server_id: str):
        try:
            await db.execute(
                "UPDATE users SET selected_server = ?, last_active = ? WHERE user_id = ?",
                (server_id, datetime.now().isoformat(), user_id)
            )
        except Exception as e:
            logger.error(f"Ошибка update_server: {e}")
    
    @staticmethod
    async def update_protocol(user_id: int, protocol: str):
        try:
            await db.execute(
                "UPDATE users SET selected_protocol = ?, last_active = ? WHERE user_id = ?",
                (protocol, datetime.now().isoformat(), user_id)
            )
        except Exception as e:
            logger.error(f"Ошибка update_protocol: {e}")
    
    @staticmethod
    async def activate_trial(user_id: int) -> Tuple[bool, str]:
        try:
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
                "UPDATE users SET subscribe_until = ? WHERE user_id = ?",
                (new_date.isoformat(), user_id)
            )
            
            return new_date
        except Exception as e:
            logger.error(f"Ошибка give_subscription: {e}")
            return None
    
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
        
        for u in users:
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
            "conversion": round(active / total * 100, 1) if total else 0
        }
    
    @staticmethod
    async def save_crypto_payment(user_id: int, invoice_id: int, plan_id: str, amount_rub: int, payload: str):
        try:
            await db.execute(
                "INSERT INTO crypto_payments (user_id, invoice_id, plan_id, amount_rub, payload) VALUES (?, ?, ?, ?, ?)",
                (user_id, invoice_id, plan_id, amount_rub, payload)
            )
        except Exception as e:
            logger.error(f"Ошибка save_crypto_payment: {e}")
    
    @staticmethod
    async def confirm_crypto_payment(invoice_id: int):
        try:
            await db.execute(
                "UPDATE crypto_payments SET status = 'paid', paid_at = ? WHERE invoice_id = ?",
                (datetime.now().isoformat(), invoice_id)
            )
        except Exception as e:
            logger.error(f"Ошибка confirm_crypto_payment: {e}")
    
    @staticmethod
    async def get_pending_payments():
        try:
            return await db.fetch_all("SELECT * FROM crypto_payments WHERE status = 'pending'")
        except Exception as e:
            logger.error(f"Ошибка get_pending_payments: {e}")
            return []

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
    "1month": {"name": "🌱 1 месяц", "days": 30, "price": 299},
    "3month": {"name": "🌿 3 месяца", "days": 90, "price": 699},
    "6month": {"name": "🌳 6 месяцев", "days": 180, "price": 1199},
    "12month": {"name": "🏝️ 12 месяцев", "days": 365, "price": 1999}
}

PROTOCOLS = ["OpenVPN", "WireGuard", "IKEv2"]

# ==================== КЛАВИАТУРЫ (ИСПРАВЛЕННЫЕ) ====================

class KeyboardBuilder:
    @staticmethod
    def main(is_admin: bool = False):
        """Главное меню"""
        buttons = [
            [InlineKeyboardButton("🛡️ ПОДКЛЮЧИТЬ VPN", callback_data="get_access")],
            [InlineKeyboardButton("🌍 ВЫБРАТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("📱 УСТРОЙСТВА", callback_data="my_devices")],
            [InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile")],
            [InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")],
            [InlineKeyboardButton("📞 ПОДДЕРЖКА", callback_data="support")]
        ]
        if is_admin:
            buttons.append([InlineKeyboardButton("⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_menu")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def servers():
        """Выбор сервера"""
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
        """Тарифы"""
        buttons = [
            [InlineKeyboardButton("🌱 1 месяц - 299₽", callback_data="buy_1month")],
            [InlineKeyboardButton("🌿 3 месяца - 699₽", callback_data="buy_3month")],
            [InlineKeyboardButton("🌳 6 месяцев - 1199₽", callback_data="buy_6month")],
            [InlineKeyboardButton("🏝️ 12 месяцев - 1999₽", callback_data="buy_12month")],
            [InlineKeyboardButton("🎁 ПРОБНЫЙ ПЕРИОД 6 ДНЕЙ", callback_data="trial")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ]
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def protocols():
        """Выбор протокола"""
        buttons = []
        for protocol in PROTOCOLS:
            buttons.append([InlineKeyboardButton(f"🔒 {protocol}", callback_data=f"protocol_{protocol}")])
        buttons.append([InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def devices():
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
    def subscription():
        """Управление подпиской"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 ПРОДЛИТЬ", callback_data="get_access")],
            [InlineKeyboardButton("📥 СКАЧАТЬ КОНФИГ", callback_data="download_config")],
            [InlineKeyboardButton("🌍 СМЕНИТЬ СЕРВЕР", callback_data="select_server")],
            [InlineKeyboardButton("👥 РЕФЕРАЛЫ", callback_data="referrals")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def referrals(referral_code: str):
        """Реферальная система (исправлено - ссылка как в AtlantaVPN)"""
        ref_link = f"https://t.me/{config.BOT_USERNAME}?start=ref_{referral_code}"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 РЕФЕРАЛЬНАЯ ССЫЛКА", url=ref_link)],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="referral_stats")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def payment(plan_name: str, plan_price: int, invoice_url: str, invoice_id: int):
        """Клавиатура оплаты"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 ОПЛАТИТЬ КРИПТОВАЛЮТОЙ", url=invoice_url)],
            [InlineKeyboardButton("✅ Я ОПЛАТИЛ", callback_data=f"check_crypto_{invoice_id}")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def admin_panel():
        """Админ панель"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 ВСЕ ПОЛЬЗОВАТЕЛИ", callback_data="admin_users")],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 СДЕЛАТЬ РАССЫЛКУ", callback_data="admin_mailing")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def admin_users(users: List[Dict], page: int = 0):
        """Список пользователей для админа"""
        buttons = []
        start = page * 5
        end = start + 5
        
        for user in users[start:end]:
            name = user.get('first_name', '—')[:10]
            status = "🔴" if user.get('banned') else "🟢"
            sub = "✅" if user.get('subscribe_until') and datetime.fromisoformat(user['subscribe_until']) > datetime.now() else "❌"
            btn_text = f"{status}{sub} {name} (@{user.get('username', '—')})"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"admin_user_{user['user_id']}")])
        
        # Навигация
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
    def admin_user_actions(user_id: int, is_banned: bool):
        """Действия с пользователем"""
        buttons = [
            [InlineKeyboardButton("📅 ВЫДАТЬ ПОДПИСКУ", callback_data=f"admin_give_{user_id}")],
            [InlineKeyboardButton("🔒 ЗАБАНИТЬ" if not is_banned else "🔓 РАЗБАНИТЬ", 
                                 callback_data=f"admin_ban_{user_id}" if not is_banned else f"admin_unban_{user_id}")],
            [InlineKeyboardButton("🔙 К СПИСКУ", callback_data="admin_users")]
        ]
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_give_sub(user_id: int):
        """Выбор срока подписки"""
        buttons = []
        for pid, plan in PLANS.items():
            buttons.append([InlineKeyboardButton(
                f"{plan['name']} - {plan['days']} дней",
                callback_data=f"admin_give_{pid}_{user_id}"
            )])
        buttons.append([InlineKeyboardButton("🔙 НАЗАД", callback_data=f"admin_user_{user_id}")])
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def admin_confirm_mailing():
        """Подтверждение рассылки"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ РАССЫЛКУ", callback_data="admin_mailing_confirm")],
            [InlineKeyboardButton("❌ ОТМЕНИТЬ", callback_data="admin_menu")]
        ])
    
    @staticmethod
    def back():
        """Кнопка назад"""
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== FASTAPI ПРИЛОЖЕНИЕ ====================

app = FastAPI()
telegram_app = None
startup_time = time.time()

# ==================== ФУНКЦИИ ДЛЯ СООБЩЕНИЙ ====================

async def delete_previous_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        user = await UserManager.get(chat_id)
        if user and user.get("last_message_id"):
            await context.bot.delete_message(chat_id=chat_id, message_id=user["last_message_id"])
    except:
        pass

async def send_new_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, keyboard=None):
    try:
        await delete_previous_message(context, chat_id)
        
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
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.HTML
            )
            sent += 1
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
                is_paid = await crypto.check_payment(payment["invoice_id"])
                
                if is_paid:
                    user_id = payment["user_id"]
                    plan_id = payment["plan_id"]
                    plan = PLANS[plan_id]
                    
                    new_date = await UserManager.give_subscription(user_id, plan["days"])
                    await UserManager.confirm_crypto_payment(payment["invoice_id"])
                    
                    # Уведомляем пользователя
                    try:
                        await telegram_app.bot.send_message(
                            chat_id=user_id,
                            text=f"✅ <b>Оплата подтверждена!</b>\n\n"
                                 f"Подписка {plan['name']} активирована!\n"
                                 f"📅 Действует до: {new_date.strftime('%d.%m.%Y')}",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                    
        except Exception as e:
            logger.error(f"Ошибка в фоновой проверке: {e}")

# ==================== ОБРАБОТЧИКИ TELEGRAM ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        args = context.args
        
        logger.info(f"🚀 /start от {user.id}")
        
        # Проверяем реферальный код (исправлено!)
        referred_by = None
        if args and args[0].startswith("ref_"):
            try:
                ref_user_id = int(args[0].replace("ref_", ""))
                # Проверяем, что реферер существует и это не сам пользователь
                referrer = await UserManager.get(ref_user_id)
                if referrer and ref_user_id != user.id:
                    referred_by = ref_user_id
                    logger.info(f"👥 Реферальный переход: {referred_by} -> {user.id}")
            except:
                pass
        
        await UserManager.create(user.id, user.username or "", user.first_name or "", referred_by)
        
        # Проверка на бан
        db_user = await UserManager.get(user.id)
        if db_user and db_user.get("banned"):
            await update.message.reply_text("⛔ Доступ заблокирован")
            return
        
        welcome_text = (
            f"🌟 <b>Ples VPN </b>\n\n"
            f"🌍 <b>Серверы:</b>\n"
            f"🇳🇱 Нидерланды • 🇺🇸 США • 🇩🇪 Германия\n"
            f"🇬🇧 UK • 🇸🇬 Сингапур • 🇯🇵 Япония\n\n"
            f"⚡ <b>Протоколы:</b> OpenVPN • WireGuard • IKEv2\n\n"
            f"💰 <b>Тарифы:</b>\n"
            f"• 1 месяц — 299₽\n"
            f"• 3 месяца — 699₽\n"
            f"• 6 месяцев — 1199₽\n"
            f"• 12 месяцев — 1999₽\n\n"
            f"🎁 <b>Пробный период:</b> 6 дней\n\n"
            f"👥 <b>Рефералы:</b> +3 дня за друга\n\n"
            f"💳 <b>Оплата:</b> криптовалюта (USDT/TON/BTC)\n\n"
            f"Выберите действие в меню:"
        )
        
        is_admin = user.id in config.ADMIN_IDS
        await send_new_message(context, user.id, welcome_text, KeyboardBuilder.main(is_admin))
        
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        logger.info(f"🔘 Кнопка: {data} от {user_id}")
        
        is_admin = user_id in config.ADMIN_IDS
        
        # ===== НАВИГАЦИЯ =====
        if data == "back_main":
            await send_new_message(context, user_id, "🏠 Главное меню", KeyboardBuilder.main(is_admin))
        
        # ===== ПРОБНЫЙ ПЕРИОД =====
        elif data == "trial":
            success, msg = await UserManager.activate_trial(user_id)
            await send_new_message(context, user_id, msg, KeyboardBuilder.main(is_admin))
        
        # ===== ПОКУПКА =====
        elif data == "get_access":
            await send_new_message(context, user_id, "📦 ВЫБЕРИТЕ ТАРИФ", KeyboardBuilder.plans())
        
        elif data.startswith("buy_"):
            plan_id = data.replace("buy_", "")
            if plan_id in PLANS:
                plan = PLANS[plan_id]
                
                # Создаем payload
                payload = json.dumps({
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "timestamp": datetime.now().timestamp()
                })
                
                # Создаем счет
                invoice = await crypto.create_invoice(plan["price"], payload)
                
                if invoice:
                    await UserManager.save_crypto_payment(
                        user_id=user_id,
                        invoice_id=invoice["invoice_id"],
                        plan_id=plan_id,
                        amount_rub=plan["price"],
                        payload=payload
                    )
                    
                    text = (
                        f"💎 <b>Оплата подписки {plan['name']}</b>\n\n"
                        f"💰 Сумма: {plan['price']} ₽\n"
                        f"⏱ Счет действителен 1 час\n\n"
                        f"1. Нажмите «Оплатить криптовалютой»\n"
                        f"2. Выберите USDT/TON/BTC\n"
                        f"3. После оплаты нажмите «Я оплатил»"
                    )
                    
                    await send_new_message(
                        context, 
                        user_id, 
                        text, 
                        KeyboardBuilder.payment(plan['name'], plan['price'], invoice["bot_invoice_url"], invoice["invoice_id"])
                    )
                else:
                    await send_new_message(context, user_id, "❌ Ошибка создания счета", KeyboardBuilder.main(is_admin))
        
        elif data.startswith("check_crypto_"):
            invoice_id = int(data.replace("check_crypto_", ""))
            is_paid = await crypto.check_payment(invoice_id)
            
            if is_paid:
                payment = await db.fetch_one(
                    "SELECT * FROM crypto_payments WHERE invoice_id = ?", 
                    (invoice_id,)
                )
                
                if payment and payment["status"] == "pending":
                    plan_id = payment["plan_id"]
                    plan = PLANS[plan_id]
                    
                    new_date = await UserManager.give_subscription(user_id, plan["days"])
                    await UserManager.confirm_crypto_payment(invoice_id)
                    
                    await send_new_message(
                        context,
                        user_id,
                        f"✅ <b>Оплата подтверждена!</b>\n\n"
                        f"Подписка {plan['name']} активирована!\n"
                        f"📅 Действует до: {new_date.strftime('%d.%m.%Y')}",
                        KeyboardBuilder.main(is_admin)
                    )
                    
                    await query.answer("✅ Платеж найден!", show_alert=True)
            else:
                await query.answer("❌ Платеж не найден", show_alert=True)
        
        # ===== ВЫБОР СЕРВЕРА =====
        elif data == "select_server":
            await send_new_message(context, user_id, "🌍 ВЫБЕРИТЕ СЕРВЕР", KeyboardBuilder.servers())
        
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
            await send_new_message(context, user_id, "📱 ВЫБЕРИТЕ УСТРОЙСТВО", KeyboardBuilder.devices())
        
        elif data.startswith("device_"):
            device = data.replace("device_", "")
            instructions = {
                "android": "📱 <b>ANDROID</b>\n\n1. Установите OpenVPN Connect\n2. Скачайте конфиг\n3. Импортируйте",
                "ios": "🍏 <b>IOS</b>\n\n1. Установите OpenVPN Connect\n2. Скачайте конфиг\n3. Импортируйте",
                "windows": "💻 <b>WINDOWS</b>\n\n1. Установите OpenVPN GUI\n2. Поместите конфиг в папку config\n3. Запустите",
                "macos": "🍎 <b>MACOS</b>\n\n1. Установите Tunnelblick\n2. Откройте конфиг",
                "linux": "🐧 <b>LINUX</b>\n\n1. sudo apt install openvpn\n2. sudo openvpn --config config.ovpn"
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
            
            if not user:
                username = query.from_user.username or ""
                first_name = query.from_user.first_name or ""
                user = await UserManager.create(user_id, username, first_name)
            
            if user.get("subscribe_until"):
                try:
                    end = datetime.fromisoformat(user["subscribe_until"])
                    days = (end - datetime.now()).days
                    status = "✅ Активна" if days > 0 else "❌ Истекла"
                    end_str = end.strftime("%d.%m.%Y")
                except:
                    days = 0
                    status = "❌ Нет подписки"
                    end_str = "-"
            else:
                days = 0
                status = "❌ Нет подписки"
                end_str = "-"
            
            server_id = user.get("selected_server", "netherlands")
            server = SERVERS.get(server_id, SERVERS["netherlands"])
            protocol = user.get("selected_protocol", "OpenVPN")
            
            text = (
                f"👤 <b>ПРОФИЛЬ</b>\n\n"
                f"📊 Статус: {status}\n"
                f"📅 Действует до: {end_str}\n"
                f"⏱ Осталось: {max(0, days)} дн.\n\n"
                f"🌍 Сервер: {server['name']}\n"
                f"🔌 Протокол: {protocol}\n\n"
                f"🆔 ID: <code>{user_id}</code>"
            )
            
            await send_new_message(context, user_id, text, KeyboardBuilder.back())
        
        # ===== РЕФЕРАЛЫ (ИСПРАВЛЕНО) =====
        elif data == "referrals":
            user = await UserManager.get(user_id)
            if user:
                # Считаем количество рефералов
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
        
        elif data == "referral_stats":
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
                        # Получаем информацию о реферале
                        ref_user = await UserManager.get(ref["referred_id"])
                        ref_name = ref_user.get("first_name", "—") if ref_user else "—"
                        status = "✅" if ref_user and ref_user.get("subscribe_until") and datetime.fromisoformat(ref_user["subscribe_until"]) > datetime.now() else "⏳"
                        text += f"{status} {ref_name} - {ref['created_at'][:10]}\n"
                
                await send_new_message(context, user_id, text, KeyboardBuilder.back())
        
        # ===== ПОДДЕРЖКА =====
        elif data == "support":
            await send_new_message(
                context, 
                user_id, 
                "📞 <b>ПОДДЕРЖКА</b>\n\n@vpn_support_bot",
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
            await send_new_message(context, user_id, "⚙️ <b>АДМИН ПАНЕЛЬ</b>", KeyboardBuilder.admin_panel())
        
        elif data == "admin_users" and is_admin:
            users = await UserManager.get_all_users()
            await send_new_message(
                context,
                user_id,
                f"👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ ({len(users)})</b>",
                KeyboardBuilder.admin_users(users)
            )
        
        elif data.startswith("admin_users_page_") and is_admin:
            page = int(data.split("_")[-1])
            users = await UserManager.get_all_users()
            await send_new_message(
                context,
                user_id,
                f"👥 Страница {page+1}:",
                KeyboardBuilder.admin_users(users, page)
            )
        
        elif data.startswith("admin_user_") and is_admin:
            target_id = int(data.split("_")[-1])
            target = await UserManager.get(target_id)
            
            if target:
                sub = "Нет"
                if target.get("subscribe_until"):
                    try:
                        sub = datetime.fromisoformat(target["subscribe_until"]).strftime("%d.%m.%Y")
                    except:
                        pass
                
                text = (
                    f"👤 <b>ПОЛЬЗОВАТЕЛЬ</b>\n\n"
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
                    KeyboardBuilder.admin_user_actions(target_id, target.get('banned', False))
                )
        
        elif data.startswith("admin_ban_") and is_admin:
            target_id = int(data.split("_")[-1])
            await UserManager.ban_user(target_id)
            await send_new_message(context, user_id, "✅ Пользователь забанен", KeyboardBuilder.admin_panel())
        
        elif data.startswith("admin_unban_") and is_admin:
            target_id = int(data.split("_")[-1])
            await UserManager.unban_user(target_id)
            await send_new_message(context, user_id, "✅ Пользователь разбанен", KeyboardBuilder.admin_panel())
        
        elif data.startswith("admin_give_") and is_admin and not any(data.startswith(f"admin_give_{pid}_") for pid in ["1month", "3month", "6month", "12month"]):
            target_id = int(data.split("_")[-1])
            await send_new_message(
                context,
                user_id,
                "📅 Выберите срок подписки:",
                KeyboardBuilder.admin_give_sub(target_id)
            )
        
        elif data.startswith("admin_give_") and is_admin:
            parts = data.split("_")
            plan_id = f"{parts[2]}_{parts[3]}"
            target_id = int(parts[4])
            
            if plan_id in PLANS:
                plan = PLANS[plan_id]
                new_date = await UserManager.give_subscription(target_id, plan["days"], admin_give=True)
                
                try:
                    await send_new_message(
                        context,
                        target_id,
                        f"🎉 <b>АДМИН ВЫДАЛ ПОДПИСКУ!</b>\n\n"
                        f"{plan['name']}\n"
                        f"📅 До: {new_date.strftime('%d.%m.%Y')}"
                    )
                except:
                    pass
                
                await send_new_message(
                    context,
                    user_id,
                    f"✅ Выдано {plan['days']} дней пользователю {target_id}",
                    KeyboardBuilder.admin_panel()
                )
        
        elif data == "admin_stats" and is_admin:
            stats = await UserManager.get_stats()
            text = (
                f"📊 <b>СТАТИСТИКА БОТА</b>\n\n"
                f"👥 Всего: {stats['total']}\n"
                f"✅ Активных: {stats['active']}\n"
                f"🔒 Забанено: {stats['banned']}\n"
                f"🎁 Пробный: {stats['trial']}\n"
                f"📈 Конверсия: {stats['conversion']}%"
            )
            await send_new_message(context, user_id, text, KeyboardBuilder.admin_panel())
        
        elif data == "admin_mailing" and is_admin:
            await send_new_message(
                context,
                user_id,
                "📢 <b>СОЗДАНИЕ РАССЫЛКИ</b>\n\n"
                "Отправьте текст для рассылки.\n\n"
                "Поддерживается HTML-разметка:\n"
                "<code>&lt;b&gt;жирный&lt;/b&gt;</code>\n"
                "<code>&lt;i&gt;курсив&lt;/i&gt;</code>\n\n"
                "<i>Ожидаю сообщение...</i>",
                KeyboardBuilder.back()
            )
            context.user_data['awaiting_mailing'] = True
        
        elif data == "admin_mailing_confirm" and is_admin:
            if context.user_data.get('mailing_text'):
                mailing_text = context.user_data['mailing_text']
                del context.user_data['mailing_text']
                asyncio.create_task(start_mailing(context, user_id, mailing_text))
        
    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        import traceback
        traceback.print_exc()

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    
    user = await UserManager.get(user_id)
    if user and user.get("banned"):
        await update.message.reply_text("⛔ Доступ заблокирован")
        return
    
    if context.user_data.get('awaiting_mailing') and user_id in config.ADMIN_IDS:
        del context.user_data['awaiting_mailing']
        context.user_data['mailing_text'] = text
        
        await send_new_message(
            context,
            user_id,
            f"📢 <b>ПОДТВЕРДИТЕ РАССЫЛКУ</b>\n\n"
            f"Текст сообщения:\n\n{text}\n\n"
            f"Отправить всем пользователям?",
            KeyboardBuilder.admin_confirm_mailing()
        )
        return

# ==================== FASTAPI ЭНДПОИНТЫ ====================

@app.on_event("startup")
async def startup():
    global telegram_app
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК PLES VPN BOT v3.1")
    logger.info("=" * 60)
    
    if await db.init():
        logger.info("✅ База данных готова")
    else:
        logger.error("❌ Ошибка базы данных")
        return
    
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    
    await telegram_app.initialize()
    await telegram_app.start()
    
    webhook_url = f"{config.BASE_URL}{config.WEBHOOK_PATH}"
    await telegram_app.bot.set_webhook(url=webhook_url)
    
    asyncio.create_task(check_pending_payments())
    
    logger.info(f"✅ Вебхук: {webhook_url}")
    logger.info(f"✅ Админы: {config.ADMIN_IDS}")
    logger.info(f"✅ CryptoBot: подключен")
    logger.info("✅ Бот готов!")
    logger.info("=" * 60)

@app.on_event("shutdown")
async def shutdown():
    if telegram_app:
        await telegram_app.stop()
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
        "version": "3.1",
        "admins": config.ADMIN_IDS,
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
        "ples_vpn_bot_fixed:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
)

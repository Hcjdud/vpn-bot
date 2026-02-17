#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║        🚀 VPN BOT - ФИНАЛЬНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ             ║
║        Все ошибки исправлены • База данных работает          ║
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

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def init(self):
        """Инициализация БД"""
        try:
            logger.info("📦 Создание базы данных...")
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            async with aiosqlite.connect(self.db_path) as db:
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
                
                await db.execute('CREATE INDEX IF NOT EXISTS idx_referral_code ON users(referral_code)')
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
    async def create(user_id: int, username: str, first_name: str, referred_by: int = None):
        try:
            existing = await UserManager.get(user_id)
            if existing:
                return existing
            
            referral_code = secrets.token_hex(4).upper()
            
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
    def admin_panel():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 ВСЕ ПОЛЬЗОВАТЕЛИ", callback_data="admin_users")],
            [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]
        ])
    
    @staticmethod
    def back():
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_main")]])

# ==================== FASTAPI ПРИЛОЖЕНИЕ ====================

app = FastAPI()
telegram_app = None

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

# ==================== ОБРАБОТЧИКИ ====================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        args = context.args
        
        logger.info(f"🚀 /start от {user.id}")
        
        referred_by = None
        if args and args[0].startswith("ref_"):
            ref_code = args[0].replace("ref_", "")
            referrer = await UserManager.get_by_referral_code(ref_code)
            if referrer and referrer["user_id"] != user.id:
                referred_by = referrer["user_id"]
        
        await UserManager.create(user.id, user.username or "", user.first_name or "", referred_by)
        
        text = f"🌟 <b>Добро пожаловать, {user.first_name}!</b>"
        is_admin = user.id in config.ADMIN_IDS
        await send_new_message(context, user.id, text, KeyboardBuilder.main(is_admin))
        
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
        
        # ===== АДМИН ПАНЕЛЬ =====
        elif data == "admin_menu" and is_admin:
            await send_new_message(context, user_id, "⚙️ АДМИН ПАНЕЛЬ", KeyboardBuilder.admin_panel())
        
        # ===== АДМИН СТАТИСТИКА (ИСПРАВЛЕНО) =====
        elif data == "admin_stats" and is_admin:
            users = await UserManager.get_all_users()
            total = len(users)
            
            # Защита от деления на ноль
            if total == 0:
                text = "📊 <b>СТАТИСТИКА</b>\n\n👥 Пользователей пока нет"
            else:
                active = 0
                for u in users:
                    if u.get("subscribe_until"):
                        try:
                            if datetime.fromisoformat(u["subscribe_until"]) > datetime.now():
                                active += 1
                        except:
                            pass
                
                text = (
                    f"📊 <b>СТАТИСТИКА</b>\n\n"
                    f"👥 Всего пользователей: {total}\n"
                    f"✅ Активных подписок: {active}\n"
                    f"📈 Конверсия: {active/total*100:.1f}%"
                )
            
            await send_new_message(context, user_id, text, KeyboardBuilder.admin_panel())
        
        # ===== АДМИН ПОЛЬЗОВАТЕЛИ =====
        elif data == "admin_users" and is_admin:
            users = await UserManager.get_all_users()
            text = f"👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ ({len(users)})</b>\n\n"
            for u in users[:10]:
                name = u.get('first_name', '—')[:15]
                sub = "✅" if u.get('subscribe_until') and datetime.fromisoformat(u['subscribe_until']) > datetime.now() else "❌"
                text += f"{sub} {name} (@{u.get('username', '—')})\n"
            await send_new_message(context, user_id, text, KeyboardBuilder.admin_panel())
        
        # ===== ПРОФИЛЬ (ИСПРАВЛЕНО) =====
        elif data == "profile":
            user = await UserManager.get(user_id)
            
            # Если пользователя нет - создаем
            if not user:
                username = query.from_user.username or ""
                first_name = query.from_user.first_name or ""
                user = await UserManager.create(user_id, username, first_name)
                
                if not user:
                    await send_new_message(context, user_id, "❌ Ошибка загрузки профиля", KeyboardBuilder.back())
                    return
            
            # Теперь user точно существует
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
            
            # Безопасно получаем сервер
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
                new_date = await UserManager.give_subscription(user_id, plan["days"])
                if new_date:
                    await send_new_message(
                        context, 
                        user_id, 
                        f"✅ Подписка {plan['name']} активирована!\n📅 До: {new_date.strftime('%d.%m.%Y')}",
                        KeyboardBuilder.main(is_admin)
                    )
        
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
        
        # ===== ПОДДЕРЖКА =====
        elif data == "support":
            await send_new_message(
                context, 
                user_id, 
                "📞 <b>ПОДДЕРЖКА</b>\n\n@vpn_support_bot",
                KeyboardBuilder.back()
            )
        
    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        logger.error(traceback.format_exc())

# ==================== FASTAPI ====================

@app.on_event("startup")
async def startup():
    global telegram_app
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК БОТА")
    
    if await db.init():
        logger.info("✅ База данных готова")
    else:
        logger.error("❌ Ошибка базы данных")
        return
    
    telegram_app = Application.builder().token(config.BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    
    await telegram_app.initialize()
    await telegram_app.start()
    
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
    return {"status": "online", "version": "2.0"}

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("render_bot_final_fixed:app", host="0.0.0.0", port=port)

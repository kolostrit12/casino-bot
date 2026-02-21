import asyncio
import logging
import sqlite3
import random
import math
import time
import functools  # <- Этого не хватает!
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from contextlib import contextmanager

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, Message, FSInputFile, BufferedInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# 2. ПОТОМ настройки (BOT_TOKEN, ADMIN_IDS и т.д.)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден!")
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Правильное преобразование ADMIN_IDS
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '[1981879895]')
try:
    if ADMIN_IDS_STR.startswith('[') and ADMIN_IDS_STR.endswith(']'):
        ADMIN_IDS = json.loads(ADMIN_IDS_STR)
    else:
        ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',')]
except:
    ADMIN_IDS = [1981879895]

if not isinstance(ADMIN_IDS, list):
    ADMIN_IDS = [ADMIN_IDS]

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'casino123')

print(f"✅ Загружены настройки: ADMIN_IDS={ADMIN_IDS}")

# 3. ЗАТЕМ инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== ФУНКЦИЯ БЕЗОПАСНОГО РЕДАКТИРОВАНИЯ ====================

async def safe_edit_message(message, text, reply_markup=None, parse_mode=None):
    """Безопасное редактирование сообщения с проверкой изменений"""
    try:
        current_text = message.text or message.caption or ""
        
        if text == current_text:
            if reply_markup == message.reply_markup:
                return message
        
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "message is not modified" in str(e):
            return message
        logger.error(f"Ошибка при редактировании сообщения: {e}")
        return message

# ==================== ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ИМЕНИ ПОЛЬЗОВАТЕЛЯ ====================

def get_user_display(user_id: int, username: str = None, first_name: str = None) -> str:
    """Возвращает @username если есть, иначе ID пользователя"""
    if username:
        return f"@{username}"
    elif first_name:
        return f"{first_name} (ID: {user_id})"
    else:
        return f"ID: {user_id}"

def convert_imgur_url(url: str) -> str:
    """Конвертирует URL в рабочий формат"""
    if not url or url == "0":
        return None
    
    # Если это уже прямая ссылка с расширением
    if any(url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
        return url
    
    # Telegraph ссылки
    if 'telegra.ph' in url:
        return url
    
    # Postimages
    if 'postimg.cc' in url or 'postimages.org' in url:
        # Преобразуем в прямую ссылку если нужно
        if 'i.postimg.cc' not in url:
            # Извлекаем ID
            import re
            match = re.search(r'/([^/]+)$', url)
            if match:
                return f"https://i.postimg.cc/{match.group(1)}"
        return url
    
    # ImgBB
    if 'ibb.co' in url:
        if 'i.ibb.co' not in url:
            # Преобразуем в прямую ссылку
            return url.replace('ibb.co', 'i.ibb.co') + '.jpg'
        return url
    
    # Если это ссылка на Imgur, показываем предупреждение
    if 'imgur.com' in url:
        logger.warning(f"Попытка использовать Imgur: {url}")
        return url  # Пробуем, но скорее всего не сработает
    
    # Если это другая ссылка, просто возвращаем как есть
    return url
 # ==================== СОСТОЯНИЯ FSM ====================
class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_project_data = State()
    waiting_for_project_edit = State()
    waiting_for_promo_data = State()
    waiting_for_promo_edit = State()
    waiting_for_wheel_prize_add = State()
    waiting_for_wheel_prize_edit = State()
    waiting_for_task_add = State()
    waiting_for_task_edit = State()
    waiting_for_task_reward = State()
    waiting_for_user_id = State()
    waiting_for_spins_amount = State()
    waiting_for_points_amount = State()
    waiting_for_mailing = State()
    waiting_for_mailing_confirm = State()
    waiting_for_jackpot_promo = State()
    waiting_for_mines_count = State()
    waiting_for_promo_code_name = State()      # Название промокода
    waiting_for_promo_code_value = State()     # Количество баллов
    waiting_for_promo_code_uses = State()      # Количество активаций
    waiting_for_promo_code_edit = State()      # Редактирование промокода
    
# ==================== СОСТОЯНИЯ ДЛЯ ИГРЫ MINES ====================
class MinesStates(StatesGroup):
    waiting_for_bet = State()
    waiting_for_mines_count = State()
    playing = State()
    
# ==================== РАБОТА С БАЗОЙ ДАННЫХ ====================

def get_db():
    """Получение соединения с БД"""
    conn = sqlite3.connect('casino_bot.db', timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def retry_on_locked(max_retries=5, delay=0.1):
    """Декоратор для повторных попыток при блокировке БД"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))
                        continue
                    else:
                        raise
            return func(*args, **kwargs)
        return wrapper
    return decorator

def init_db():
    """Инициализация базы данных (с сохранением существующих данных)"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Таблица пользователей (с проверкой существования)
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY,
                      username TEXT,
                      first_name TEXT,
                      points INTEGER DEFAULT 0,
                      spins INTEGER DEFAULT 3,
                      referrer_id INTEGER DEFAULT NULL,
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Добавляем новые колонки в users, если их нет
        try:
            c.execute("ALTER TABLE users ADD COLUMN total_spins INTEGER DEFAULT 0")
            print("✅ Добавлена колонка total_spins")
        except sqlite3.OperationalError:
            pass  # колонка уже существует
        
        try:
            c.execute("ALTER TABLE users ADD COLUMN total_wins INTEGER DEFAULT 0")
            print("✅ Добавлена колонка total_wins")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE users ADD COLUMN last_daily_bonus DATE DEFAULT NULL")
            print("✅ Добавлена колонка last_daily_bonus")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE users ADD COLUMN daily_bonus_streak INTEGER DEFAULT 0")
            print("✅ Добавлена колонка daily_bonus_streak")
        except sqlite3.OperationalError:
            pass
        
        try:
            c.execute("ALTER TABLE users ADD COLUMN last_free_spin DATE DEFAULT NULL")
            print("✅ Добавлена колонка last_free_spin")
        except sqlite3.OperationalError:
            pass
        
        # Таблица заданий
        c.execute('''CREATE TABLE IF NOT EXISTS tasks
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      task_name TEXT,
                      task_description TEXT,
                      reward_spins INTEGER,
                      reward_points INTEGER,
                      task_type TEXT,
                      task_data TEXT,
                      is_active BOOLEAN DEFAULT 1,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Таблица выполненных заданий
        c.execute('''CREATE TABLE IF NOT EXISTS completed_tasks
                     (user_id INTEGER,
                      task_id INTEGER,
                      completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (user_id) REFERENCES users (user_id),
                      FOREIGN KEY (task_id) REFERENCES tasks (id),
                      PRIMARY KEY (user_id, task_id))''')
        
        # Таблица промокодов магазина
        c.execute('''CREATE TABLE IF NOT EXISTS shop_promocodes
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT,
                      price INTEGER,
                      promo_code TEXT,
                      is_used BOOLEAN DEFAULT 0,
                      buyer_id INTEGER DEFAULT NULL,
                      bought_at TIMESTAMP DEFAULT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Добавляем новые колонки в shop_promocodes
        try:
            c.execute("ALTER TABLE shop_promocodes ADD COLUMN project_id INTEGER DEFAULT NULL")
            print("✅ Добавлена колонка project_id в shop_promocodes")
        except sqlite3.OperationalError:
            pass
        
        # Таблица проектов
        c.execute('''CREATE TABLE IF NOT EXISTS projects
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      title TEXT,
                      url TEXT,
                      promo_code TEXT,
                      is_active BOOLEAN DEFAULT 1,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Таблица призов колеса фортуны
        c.execute('''CREATE TABLE IF NOT EXISTS wheel_prizes
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT,
                      type TEXT,
                      value INTEGER,
                      probability INTEGER DEFAULT 10,
                      is_active BOOLEAN DEFAULT 1,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Таблица джекпот промокодов
        c.execute('''CREATE TABLE IF NOT EXISTS jackpot_promocodes
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      promo_code TEXT,
                      is_used BOOLEAN DEFAULT 0,
                      winner_id INTEGER DEFAULT NULL,
                      won_at TIMESTAMP DEFAULT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Добавляем новые колонки в jackpot_promocodes
        try:
            c.execute("ALTER TABLE jackpot_promocodes ADD COLUMN project_id INTEGER DEFAULT NULL")
            print("✅ Добавлена колонка project_id в jackpot_promocodes")
        except sqlite3.OperationalError:
            pass
        
        # Таблица уведомлений для админов
        c.execute('''CREATE TABLE IF NOT EXISTS admin_notifications
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      type TEXT,
                      message TEXT,
                      user_id INTEGER,
                      data TEXT,
                      is_read BOOLEAN DEFAULT 0,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Таблица настроек Mines
        c.execute('''CREATE TABLE IF NOT EXISTS mines_settings
                     (id INTEGER PRIMARY KEY CHECK (id=1),
                      default_mines INTEGER DEFAULT 3)''')
        
        # Добавляем запись в mines_settings, если её нет
        c.execute("INSERT OR IGNORE INTO mines_settings (id, default_mines) VALUES (1, 3)")
        
        # Добавляем начальные данные, только если таблицы пустые
        c.execute("SELECT COUNT(*) as count FROM tasks")
        if c.fetchone()['count'] == 0:
            initial_tasks = [
                ("Подписаться на канал", "Подпишитесь на наш официальный канал", 1, 50, "channel", "@your_channel"),
                ("Вступить в чат", "Присоединитесь к нашему чату", 2, 100, "chat", "@your_chat"),
                ("Посетить сайт партнера", "Перейдите на сайт нашего партнера", 1, 0, "website", "https://example.com"),
            ]
            c.executemany("INSERT INTO tasks (task_name, task_description, reward_spins, reward_points, task_type, task_data) VALUES (?,?,?,?,?,?)", initial_tasks)
            print("✅ Добавлены начальные задания")
        
        c.execute("SELECT COUNT(*) as count FROM projects")
        if c.fetchone()['count'] == 0:
            initial_projects = [
                ("Casino X", "https://example.com/casino_x", "XBONUS"),
                ("Azino 777", "https://example.com/azino777", "777GOLD"),
                ("Joy Casino", "https://example.com/joy", "JOYSPIN"),
            ]
            c.executemany("INSERT INTO projects (title, url, promo_code) VALUES (?,?,?)", initial_projects)
            print("✅ Добавлены начальные проекты")
        
        c.execute("SELECT COUNT(*) as count FROM wheel_prizes")
        if c.fetchone()['count'] == 0:
            initial_prizes = [
                ("50 баллов", "points", 50, 25),
                ("100 баллов", "points", 100, 20),
                ("+1 попытка", "spins", 1, 15),
                ("+2 попытки", "spins", 2, 10),
                ("ДЖЕКПОТ", "jackpot", 0, 5),
                ("Пусто", "empty", 0, 25),
            ]
            c.executemany("INSERT INTO wheel_prizes (name, type, value, probability) VALUES (?,?,?,?)", initial_prizes)
            print("✅ Добавлены начальные призы для колеса")
        
        c.execute("SELECT COUNT(*) as count FROM jackpot_promocodes")
        if c.fetchone()['count'] == 0:
            c.execute("SELECT id FROM projects LIMIT 1")
            project = c.fetchone()
            project_id = project['id'] if project else None
            
            jackpot_promos = [
                ("JACKPOT100", project_id),
                ("JACKPOT200", project_id),
                ("JACKPOT500", project_id),
                ("BONUS777", project_id),
                ("LUCKY888", project_id)
            ]
            c.executemany("INSERT INTO jackpot_promocodes (promo_code, project_id) VALUES (?,?)", jackpot_promos)
            print("✅ Добавлены начальные промокоды для джекпота")
        
        conn.commit()
        print("✅ База данных инициализирована (данные сохранены)")
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации БД: {e}")
    finally:
        if conn:
            conn.close()
def migrate_db():
    """Миграция базы данных - добавление недостающих колонок"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем и добавляем колонки в users
        c.execute("PRAGMA table_info(users)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'total_spins' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN total_spins INTEGER DEFAULT 0")
            print("✅ Добавлена колонка total_spins")
        
        if 'total_wins' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN total_wins INTEGER DEFAULT 0")
            print("✅ Добавлена колонка total_wins")
        
        if 'last_daily_bonus' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN last_daily_bonus DATE DEFAULT NULL")
            print("✅ Добавлена колонка last_daily_bonus")
        
        if 'daily_bonus_streak' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN daily_bonus_streak INTEGER DEFAULT 0")
            print("✅ Добавлена колонка daily_bonus_streak")
        
        if 'last_free_spin' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN last_free_spin DATE DEFAULT NULL")
            print("✅ Добавлена колонка last_free_spin")
        
        # Проверяем shop_promocodes
        c.execute("PRAGMA table_info(shop_promocodes)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'project_id' not in columns:
            c.execute("ALTER TABLE shop_promocodes ADD COLUMN project_id INTEGER DEFAULT NULL")
            print("✅ Добавлена колонка project_id в shop_promocodes")
        
        # Проверяем jackpot_promocodes
        c.execute("PRAGMA table_info(jackpot_promocodes)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'project_id' not in columns:
            c.execute("ALTER TABLE jackpot_promocodes ADD COLUMN project_id INTEGER DEFAULT NULL")
            print("✅ Добавлена колонка project_id в jackpot_promocodes")
        
        conn.commit()
        print("✅ Миграция базы данных завершена")
        
    except Exception as e:
        print(f"❌ Ошибка при миграции: {e}")
    finally:
        if conn:
            conn.close()
def migrate_projects_table():
    """Миграция таблицы projects - добавление колонки photo_url"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, есть ли колонка photo_url
        c.execute("PRAGMA table_info(projects)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'photo_url' not in columns:
            c.execute("ALTER TABLE projects ADD COLUMN photo_url TEXT DEFAULT NULL")
            print("✅ Добавлена колонка photo_url в таблицу projects")
        
        conn.commit()
        print("✅ Миграция таблицы projects завершена")
        
    except Exception as e:
        print(f"❌ Ошибка при миграции projects: {e}")
    finally:
        if conn:
            conn.close()

def migrate_promocodes_table():
    """Миграция для создания таблицы промокодов"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Таблица промокодов
        c.execute('''CREATE TABLE IF NOT EXISTS promocodes
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      code TEXT UNIQUE,
                      name TEXT,
                      points INTEGER,
                      max_uses INTEGER,
                      used_count INTEGER DEFAULT 0,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      expires_at DATE DEFAULT NULL,
                      is_active BOOLEAN DEFAULT 1)''')
        
        # Таблица активаций промокодов пользователями
        c.execute('''CREATE TABLE IF NOT EXISTS promo_activations
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      promo_id INTEGER,
                      user_id INTEGER,
                      activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (promo_id) REFERENCES promocodes (id),
                      FOREIGN KEY (user_id) REFERENCES users (user_id),
                      UNIQUE(promo_id, user_id))''')
        
        conn.commit()
        print("✅ Таблицы для промокодов созданы")
        
    except Exception as e:
        print(f"❌ Ошибка при создании таблиц промокодов: {e}")
    finally:
        if conn:
            conn.close()

def migrate_shop_table():
    """Миграция таблицы shop_promocodes - добавление поля quantity"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Проверяем, есть ли колонка quantity
        c.execute("PRAGMA table_info(shop_promocodes)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'quantity' not in columns:
            c.execute("ALTER TABLE shop_promocodes ADD COLUMN quantity INTEGER DEFAULT 1")
            print("✅ Добавлена колонка quantity в shop_promocodes")
        
        if 'total_quantity' not in columns:
            c.execute("ALTER TABLE shop_promocodes ADD COLUMN total_quantity INTEGER DEFAULT 1")
            print("✅ Добавлена колонка total_quantity в shop_promocodes")
        
        conn.commit()
        print("✅ Миграция таблицы магазина завершена")
        
    except Exception as e:
        print(f"❌ Ошибка при миграции shop: {e}")
    finally:
        if conn:
            conn.close()
# ==================== ФУНКЦИИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ====================

@retry_on_locked()
def register_user(user_id: int, username: str = None, first_name: str = None, referrer_id: int = None):
    """Регистрация нового пользователя"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        
        if not user:
            c.execute("""
                INSERT INTO users (user_id, username, first_name, points, spins, referrer_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, username, first_name, 0, 3, referrer_id))
            
            if referrer_id:
                c.execute("UPDATE users SET spins = spins + 1 WHERE user_id = ?", (referrer_id,))
                
                # Получаем информацию о реферере для уведомления
                c.execute("SELECT username, first_name FROM users WHERE user_id = ?", (referrer_id,))
                referrer = c.fetchone()
                referrer_display = get_user_display(referrer_id, referrer['username'] if referrer else None, referrer['first_name'] if referrer else None)
                
                add_admin_notification(
                    "referral",
                    f"👥 Новый реферал!\nПригласивший: {referrer_display}\nНовый пользователь: {get_user_display(user_id, username, first_name)}",
                    user_id
                )
            
            conn.commit()
            return True
        return False
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_user_data(user_id: int):
    """Получить данные пользователя"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT points, spins, joined_at, total_spins, total_wins, username, first_name, last_daily_bonus, daily_bonus_streak, last_free_spin FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_user_spins(user_id: int) -> int:
    """Получить количество попыток"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT spins FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        return row['spins'] if row else 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_user_points(user_id: int) -> int:
    """Получить количество баллов"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        return row['points'] if row else 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_user_spins(user_id: int, change: int):
    """Изменить количество попыток"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET spins = spins + ? WHERE user_id = ?", (change, user_id))
        conn.commit()
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_user_points(user_id: int, change: int):
    """Изменить количество баллов"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (change, user_id))
        conn.commit()
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_user_stats(user_id: int, spins_change: int = 0, points_change: int = 0, add_win: bool = False):
    """Обновить статистику пользователя"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        if spins_change != 0:
            c.execute("UPDATE users SET spins = spins + ? WHERE user_id = ?", (spins_change, user_id))
        
        if points_change != 0:
            c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points_change, user_id))
        
        if add_win:
            c.execute("UPDATE users SET total_wins = total_wins + 1 WHERE user_id = ?", (user_id,))
        
        c.execute("UPDATE users SET total_spins = total_spins + 1 WHERE user_id = ?", (user_id,))
        
        conn.commit()
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_referral_count(user_id: int) -> int:
    """Получить количество рефералов"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM users WHERE referrer_id = ?", (user_id,))
        row = c.fetchone()
        return row['count'] if row else 0
    finally:
        if conn:
            conn.close()

def get_referral_link(user_id: int, bot_username: str) -> str:
    """Получить реферальную ссылку"""
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

# ==================== ФУНКЦИИ ДЛЯ ЕЖЕДНЕВНЫХ БОНУСОВ ====================

@retry_on_locked()
def check_daily_bonus(user_id: int) -> dict:
    """Проверить доступность ежедневного бонуса"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT last_daily_bonus, daily_bonus_streak FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        
        if not row or not row['last_daily_bonus']:
            return {"available": True, "streak": 0, "next": 50}
        
        last_bonus = row['last_daily_bonus']
        streak = row['daily_bonus_streak'] or 0
        
        last_bonus_date = datetime.strptime(last_bonus, "%Y-%m-%d").date()
        today = datetime.now().date()
        
        if last_bonus_date == today:
            tomorrow = today + timedelta(days=1)
            return {"available": False, "streak": streak, "next": get_next_bonus_amount(streak), "next_date": tomorrow.strftime("%Y-%m-%d")}
        elif last_bonus_date == today - timedelta(days=1):
            return {"available": True, "streak": streak, "next": get_next_bonus_amount(streak)}
        else:
            return {"available": True, "streak": 0, "next": 50}
            
    finally:
        if conn:
            conn.close()

def get_next_bonus_amount(streak: int) -> int:
    """Получить сумму следующего бонуса"""
    bonus_amounts = [50, 100, 200, 400, 800]
    if streak >= len(bonus_amounts):
        return 800
    return bonus_amounts[streak]

@retry_on_locked()
def claim_daily_bonus(user_id: int) -> dict:
    """Получить ежедневный бонус"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT daily_bonus_streak FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        current_streak = row['daily_bonus_streak'] or 0 if row else 0
        
        bonus_amount = get_next_bonus_amount(current_streak)
        
        new_streak = current_streak + 1
        if new_streak > 5:
            new_streak = 5
        
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            UPDATE users 
            SET points = points + ?, 
                daily_bonus_streak = ?,
                last_daily_bonus = ? 
            WHERE user_id = ?
        """, (bonus_amount, new_streak, today, user_id))
        
        conn.commit()
        
        return {"success": True, "amount": bonus_amount, "new_streak": new_streak}
    except Exception as e:
        logger.error(f"Ошибка при получении бонуса: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def check_free_spin(user_id: int) -> dict:
    """Проверить доступность бесплатной попытки"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT last_free_spin FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        
        if not row or not row['last_free_spin']:
            return {"available": True}
        
        last_spin = datetime.strptime(row['last_free_spin'], "%Y-%m-%d").date()
        today = datetime.now().date()
        
        if last_spin == today:
            tomorrow = today + timedelta(days=1)
            return {"available": False, "next_date": tomorrow.strftime("%Y-%m-%d")}
        else:
            return {"available": True}
            
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def claim_free_spin(user_id: int) -> bool:
    """Получить бесплатную попытку"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            UPDATE users 
            SET spins = spins + 1,
                last_free_spin = ? 
            WHERE user_id = ?
        """, (today, user_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка при получении бесплатной попытки: {e}")
        return False
    finally:
        if conn:
            conn.close()

# ==================== ФУНКЦИИ ДЛЯ УВЕДОМЛЕНИЙ ====================

@retry_on_locked()
def add_admin_notification(notify_type: str, message: str, user_id: int = None, data: str = None):
    """Добавить уведомление для админов"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO admin_notifications (type, message, user_id, data) VALUES (?, ?, ?, ?)",
            (notify_type, message, user_id, data)
        )
        conn.commit()
    except Exception as e:
        print(f"Ошибка при добавлении уведомления: {e}")
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_unread_notifications_count() -> int:
    """Получить количество непрочитанных уведомлений"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM admin_notifications WHERE is_read = 0")
        row = c.fetchone()
        return row['count'] if row else 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_notifications(limit: int = 50) -> List:
    """Получить уведомления"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "SELECT * FROM admin_notifications ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def mark_notifications_as_read():
    """Отметить все уведомления как прочитанные"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE admin_notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
    finally:
        if conn:
            conn.close()

# ==================== ФУНКЦИИ ДЛЯ КОЛЕСА ФОРТУНЫ ====================

@retry_on_locked()
def get_wheel_prizes() -> List:
    """Получить список призов для колеса фортуны"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM wheel_prizes WHERE is_active = 1 ORDER BY id")
        results = c.fetchall()
        prizes = [dict(row) for row in results]
        print(f"🔍 Загружено призов: {len(prizes)}")  # Временный лог
        return prizes
    except Exception as e:
        print(f"❌ Ошибка в get_wheel_prizes: {e}")
        return []
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_wheel_prize_by_id(prize_id: int):
    """Получить приз по ID"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM wheel_prizes WHERE id = ?", (prize_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def add_wheel_prize(name: str, prize_type: str, value: int, probability: int):
    """Добавить приз в колесо"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO wheel_prizes (name, type, value, probability) VALUES (?, ?, ?, ?)",
            (name, prize_type, value, probability)
        )
        conn.commit()
        return c.lastrowid
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_wheel_prize(prize_id: int, **kwargs):
    """Обновить приз в колесе"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        allowed_fields = ['name', 'type', 'value', 'probability', 'is_active']
        updates = []
        params = []
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return False
        
        params.append(prize_id)
        c.execute(f"UPDATE wheel_prizes SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def delete_wheel_prize(prize_id: int):
    """Удалить приз из колеса"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM wheel_prizes WHERE id = ?", (prize_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

def spin_wheel() -> dict:
    """Вращение колеса с учетом вероятностей"""
    try:
        prizes = get_wheel_prizes()
        print(f"🔍 Призы для колеса: {prizes}")
        
        if not prizes:
            print("⚠️ Призы не найдены, возвращаем пусто")
            return {"name": "Пусто", "type": "empty", "value": 0}
        
        total_probability = sum(prize['probability'] for prize in prizes)
        print(f"🔍 Сумма вероятностей: {total_probability}")
        
        if total_probability != 100:
            factor = 100 / total_probability
            for prize in prizes:
                prize['probability'] = int(prize['probability'] * factor)
        
        weights = [p['probability'] for p in prizes]
        print(f"🔍 Веса: {weights}")
        
        chosen_index = random.choices(range(len(prizes)), weights=weights)[0]
        result = prizes[chosen_index]
        print(f"🔍 Выбран приз: {result}")
        
        return result
    except Exception as e:
        print(f"❌ Ошибка в spin_wheel: {e}")
        return {"name": "Пусто", "type": "empty", "value": 0}

# ==================== ФУНКЦИИ ДЛЯ ЗАДАНИЙ ====================

@retry_on_locked()
def get_tasks(include_inactive: bool = False) -> List:
    """Получить список заданий"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if include_inactive:
            c.execute("SELECT * FROM tasks ORDER BY id")
        else:
            c.execute("SELECT * FROM tasks WHERE is_active = 1 ORDER BY id")
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_task_by_id(task_id: int):
    """Получить задание по ID"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def add_task(name: str, description: str, reward_spins: int, reward_points: int, 
             task_type: str, task_data: str):
    """Добавить задание"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO tasks (task_name, task_description, reward_spins, reward_points, task_type, task_data) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, reward_spins, reward_points, task_type, task_data)
        )
        conn.commit()
        return c.lastrowid
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_task(task_id: int, **kwargs):
    """Обновить задание"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        allowed_fields = ['task_name', 'task_description', 'reward_spins', 
                         'reward_points', 'task_type', 'task_data', 'is_active']
        
        updates = []
        params = []
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return False
        
        params.append(task_id)
        c.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def delete_task(task_id: int):
    """Удалить задание"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM completed_tasks WHERE task_id = ?", (task_id,))
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_user_tasks(user_id: int) -> Tuple[List, List]:
    """Получить список заданий для пользователя"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE is_active = 1 ORDER BY id")
        all_tasks = [dict(row) for row in c.fetchall()]
        
        c.execute("SELECT task_id FROM completed_tasks WHERE user_id = ?", (user_id,))
        completed_tasks = [row['task_id'] for row in c.fetchall()]
        
        return all_tasks, completed_tasks
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def complete_task(user_id: int, task_id: int) -> bool:
    """Отметить задание как выполненное и начислить награду"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("SELECT * FROM completed_tasks WHERE user_id = ? AND task_id = ?", (user_id, task_id))
        if c.fetchone():
            return False
        
        c.execute("SELECT reward_spins, reward_points FROM tasks WHERE id = ?", (task_id,))
        task_row = c.fetchone()
        
        if not task_row:
            return False
        
        task = dict(task_row)
        
        c.execute("UPDATE users SET spins = spins + ?, points = points + ? WHERE user_id = ?",
                  (task['reward_spins'], task['reward_points'], user_id))
        c.execute("INSERT INTO completed_tasks (user_id, task_id) VALUES (?, ?)", (user_id, task_id))
        
        # Получаем информацию о пользователе для уведомления
        c.execute("SELECT username, first_name FROM users WHERE user_id = ?", (user_id,))
        user_row = c.fetchone()
        user_display = get_user_display(user_id, user_row['username'] if user_row else None, user_row['first_name'] if user_row else None)
        
        conn.commit()
        
        task_info = get_task_by_id(task_id)
        if task_info:
            add_admin_notification(
                "task",
                f"✅ Задание выполнено!\nПользователь: {user_display}\nЗадание: {task_info['task_name']}",
                user_id
            )
        
        return True
    finally:
        if conn:
            conn.close()

# ==================== ФУНКЦИИ ДЛЯ МАГАЗИНА ====================
@retry_on_locked()
def update_shop_item(item_id: int, **kwargs):
    """Обновить товар в магазине"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        allowed_fields = ['name', 'price', 'promo_code', 'project_id', 'is_used', 'buyer_id', 'bought_at']
        updates = []
        params = []
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return False
        
        params.append(item_id)
        c.execute(f"UPDATE shop_promocodes SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def delete_shop_item(item_id: int):
    """Удалить товар из магазина"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM shop_promocodes WHERE id = ?", (item_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()
            
@retry_on_locked()
def get_shop_items(include_used: bool = False) -> List:
    """Получить список товаров в магазине (группировка по названию)"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Получаем уникальные товары с суммой доступного количества
        c.execute("""
            SELECT 
                MIN(id) as id,
                name, 
                price, 
                MIN(promo_code) as promo_code,
                project_id,
                MIN(project_title) as project_title,
                MIN(project_url) as project_url,
                COUNT(*) as quantity,
                SUM(CASE WHEN is_used = 0 THEN 1 ELSE 0 END) as available
            FROM (
                SELECT s.*, p.title as project_title, p.url as project_url 
                FROM shop_promocodes s
                LEFT JOIN projects p ON s.project_id = p.id
            )
            GROUP BY name, price, project_id
            HAVING available > 0
            ORDER BY price
        """)
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_all_shop_items_detailed(include_used: bool = False) -> List:
    """Получить детальный список всех товаров (для админки)"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if include_used:
            c.execute("""
                SELECT s.*, p.title as project_title, p.url as project_url 
                FROM shop_promocodes s
                LEFT JOIN projects p ON s.project_id = p.id
                ORDER BY s.name, s.id
            """)
        else:
            c.execute("""
                SELECT s.*, p.title as project_title, p.url as project_url 
                FROM shop_promocodes s
                LEFT JOIN projects p ON s.project_id = p.id
                WHERE s.is_used = 0 
                ORDER BY s.name, s.price
            """)
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_shop_item_by_id(item_id: int):
    """Получить конкретный экземпляр товара по ID"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT s.*, p.title as project_title, p.url as project_url 
            FROM shop_promocodes s
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE s.id = ?
        """, (item_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def add_shop_items(name: str, price: int, promo_codes: List[str], project_id: int = None):
    """Добавить несколько экземпляров товара"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        added = 0
        
        for promo_code in promo_codes:
            c.execute(
                "INSERT INTO shop_promocodes (name, price, promo_code, project_id) VALUES (?, ?, ?, ?)",
                (name, price, promo_code.strip(), project_id)
            )
            added += 1
        
        conn.commit()
        return added
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def buy_shop_item(user_id: int, item_name: str, price: int) -> Tuple[bool, str, dict]:
    """Купить один экземпляр товара"""
    print(f"💰 buy_shop_item: user={user_id}, name={item_name}, price={price}")  # ОТЛАДКА
    
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Находим первый доступный экземпляр такого товара
        c.execute("""
            SELECT s.*, p.title as project_title, p.url as project_url 
            FROM shop_promocodes s
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE s.name = ? AND s.price = ? AND s.is_used = 0
            ORDER BY s.id
            LIMIT 1
        """, (item_name, price))
        item_row = c.fetchone()
        
        if not item_row:
            print(f"❌ Товар не найден: {item_name}, {price}")  # ОТЛАДКА
            return False, "Товар не найден или закончился", None
        
        item = dict(item_row)
        print(f"✅ Найден товар: ID={item['id']}, код={item['promo_code']}")  # ОТЛАДКА
        
        c.execute("SELECT points, username, first_name FROM users WHERE user_id = ?", (user_id,))
        user_row = c.fetchone()
        
        if not user_row:
            print(f"❌ Пользователь не найден: {user_id}")  # ОТЛАДКА
            return False, "Пользователь не найден", None
        
        user = dict(user_row)
        print(f"✅ Баланс пользователя: {user['points']}")  # ОТЛАДКА
        
        if user['points'] < item['price']:
            print(f"❌ Недостаточно баллов: {user['points']} < {item['price']}")  # ОТЛАДКА
            return False, f"Недостаточно баллов! Нужно {item['price']}", None
        
        # Списываем баллы
        c.execute("UPDATE users SET points = points - ? WHERE user_id = ?", (item['price'], user_id))
        c.execute(
            "UPDATE shop_promocodes SET is_used = 1, buyer_id = ?, bought_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id, item['id'])
        )
        
        conn.commit()
        print(f"✅ Покупка совершена!")  # ОТЛАДКА
        
        user_display = get_user_display(user_id, user['username'], user['first_name'])
        
        add_admin_notification(
            "purchase",
            f"💰 Покупка в магазине!\nПользователь: {user_display}\nТовар: {item['name']}\nЦена: {item['price']}💰",
            user_id
        )
        
        return True, item['promo_code'], item
    except Exception as e:
        print(f"❌ Ошибка в buy_shop_item: {e}")  # ОТЛАДКА
        return False, f"Ошибка: {e}", None
    finally:
        if conn:
            conn.close()
# ==================== ФУНКЦИИ ДЛЯ ПРОЕКТОВ ====================

@retry_on_locked()
def get_projects(include_inactive: bool = False) -> List:
    """Получить список проектов"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if include_inactive:
            c.execute("SELECT * FROM projects ORDER BY id")
        else:
            c.execute("SELECT * FROM projects WHERE is_active = 1 ORDER BY id")
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_project_by_id(project_id: int):
    """Получить проект по ID"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def add_project(title: str, url: str, promo_code: str, photo_url: str = None):
    """Добавить проект с фото"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO projects (title, url, promo_code, photo_url) VALUES (?, ?, ?, ?)",
            (title, url, promo_code, photo_url)
        )
        conn.commit()
        return c.lastrowid
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_project(project_id: int, **kwargs):
    """Обновить проект"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        allowed_fields = ['title', 'url', 'promo_code', 'photo_url', 'is_active']
        updates = []
        params = []
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return False
        
        params.append(project_id)
        c.execute(f"UPDATE projects SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def delete_project(project_id: int):
    """Удалить проект"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

# ==================== ФУНКЦИИ ДЛЯ ДЖЕКПОТА ====================

@retry_on_locked()
def get_jackpot_promocodes(include_used: bool = False) -> List:
    """Получить список промокодов для джекпота"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if include_used:
            c.execute("""
                SELECT j.*, p.title as project_title, p.url as project_url 
                FROM jackpot_promocodes j
                LEFT JOIN projects p ON j.project_id = p.id
                ORDER BY j.id
            """)
        else:
            c.execute("""
                SELECT j.*, p.title as project_title, p.url as project_url 
                FROM jackpot_promocodes j
                LEFT JOIN projects p ON j.project_id = p.id
                WHERE j.is_used = 0 
                ORDER BY RANDOM()
            """)
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_jackpot_promocode_by_id(promo_id: int):
    """Получить промокод джекпота по ID"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT j.*, p.title as project_title, p.url as project_url 
            FROM jackpot_promocodes j
            LEFT JOIN projects p ON j.project_id = p.id
            WHERE j.id = ?
        """, (promo_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def add_jackpot_promocode(promo_code: str, project_id: int = None):
    """Добавить промокод для джекпота"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO jackpot_promocodes (promo_code, project_id) VALUES (?, ?)", (promo_code, project_id))
        conn.commit()
        return c.lastrowid
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_jackpot_promocode(promo_id: int, **kwargs):
    """Обновить промокод джекпота"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        allowed_fields = ['promo_code', 'project_id', 'is_used', 'winner_id', 'won_at']
        updates = []
        params = []
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return False
        
        params.append(promo_id)
        c.execute(f"UPDATE jackpot_promocodes SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def delete_jackpot_promocode(promo_id: int):
    """Удалить промокод джекпота"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM jackpot_promocodes WHERE id = ?", (promo_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_random_jackpot_promocode():
    """Получить случайный промокод для джекпота"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT j.*, p.title as project_title, p.url as project_url 
            FROM jackpot_promocodes j
            LEFT JOIN projects p ON j.project_id = p.id
            WHERE j.is_used = 0 
            ORDER BY RANDOM() 
            LIMIT 1
        """)
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def use_jackpot_promocode(promo_id: int, user_id: int) -> bool:
    """Использовать промокод джекпота"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "UPDATE jackpot_promocodes SET is_used = 1, winner_id = ?, won_at = CURRENT_TIMESTAMP WHERE id = ? AND is_used = 0",
            (user_id, promo_id)
        )
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()
# ==================== ФУНКЦИИ ДЛЯ НАСТРОЕК MINES ====================

@retry_on_locked()
def get_mines_settings():
    """Получить настройки игры Mines"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Создаем таблицу для настроек, если её нет
        c.execute('''CREATE TABLE IF NOT EXISTS mines_settings
                     (id INTEGER PRIMARY KEY CHECK (id=1),
                      default_mines INTEGER DEFAULT 3)''')
        
        # Добавляем запись по умолчанию, если её нет
        c.execute("SELECT * FROM mines_settings WHERE id = 1")
        if not c.fetchone():
            c.execute("INSERT INTO mines_settings (id, default_mines) VALUES (1, ?)", (3,))
            conn.commit()
        
        c.execute("SELECT default_mines FROM mines_settings WHERE id = 1")
        row = c.fetchone()
        return row['default_mines'] if row else 3
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_mines_settings(default_mines: int):
    """Обновить настройки игры Mines"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE mines_settings SET default_mines = ? WHERE id = 1", (default_mines,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении настроек Mines: {e}")
        return False
    finally:
        if conn:
            conn.close()

# ==================== ФУНКЦИИ ДЛЯ ПРОМОКОДОВ ====================

@retry_on_locked()
def add_promocode(code: str, name: str, points: int, max_uses: int, expires_at: str = None):
    """Добавить новый промокод"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO promocodes (code, name, points, max_uses, expires_at) VALUES (?, ?, ?, ?, ?)",
            (code.upper(), name, points, max_uses, expires_at)
        )
        conn.commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None  # Промокод с таким кодом уже существует
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_all_promocodes(include_inactive: bool = False) -> List:
    """Получить все промокоды"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if include_inactive:
            c.execute("SELECT * FROM promocodes ORDER BY created_at DESC")
        else:
            c.execute("SELECT * FROM promocodes WHERE is_active = 1 ORDER BY created_at DESC")
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_promocode_by_id(promo_id: int):
    """Получить промокод по ID"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM promocodes WHERE id = ?", (promo_id,))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_promocode_by_code(code: str):
    """Получить промокод по коду"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM promocodes WHERE code = ?", (code.upper(),))
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def update_promocode(promo_id: int, **kwargs):
    """Обновить промокод"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        allowed_fields = ['code', 'name', 'points', 'max_uses', 'expires_at', 'is_active']
        updates = []
        params = []
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                updates.append(f"{field} = ?")
                params.append(value)
        
        if not updates:
            return False
        
        params.append(promo_id)
        c.execute(f"UPDATE promocodes SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def delete_promocode(promo_id: int):
    """Удалить промокод"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM promo_activations WHERE promo_id = ?", (promo_id,))
        c.execute("DELETE FROM promocodes WHERE id = ?", (promo_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def check_promocode_available(code: str, user_id: int) -> dict:
    """Проверить доступность промокода для пользователя"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Получаем промокод
        c.execute("SELECT * FROM promocodes WHERE code = ?", (code.upper(),))
        promo = c.fetchone()
        
        if not promo:
            return {"success": False, "message": "❌ Данный промокод не существует"}
        
        promo = dict(promo)
        
        # Проверяем, активен ли промокод
        if not promo['is_active']:
            return {"success": False, "message": "❌ Этот промокод деактивирован"}
        
        # Проверяем срок действия
        if promo['expires_at']:
            expires = datetime.strptime(promo['expires_at'], "%Y-%m-%d").date()
            if expires < datetime.now().date():
                return {"success": False, "message": "❌ Срок действия промокода истек"}
        
        # Проверяем количество активаций
        if promo['used_count'] >= promo['max_uses']:
            return {"success": False, "message": "❌ Лимит активаций этого промокода исчерпан"}
        
        # Проверял ли пользователь уже этот промокод
        c.execute(
            "SELECT * FROM promo_activations WHERE promo_id = ? AND user_id = ?",
            (promo['id'], user_id)
        )
        if c.fetchone():
            return {"success": False, "message": "❌ Вы уже активировали этот промокод"}
        
        return {"success": True, "promo": promo}
        
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def activate_promocode(promo_id: int, user_id: int) -> bool:
    """Активировать промокод для пользователя"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Начисляем баллы пользователю
        c.execute("SELECT points FROM promocodes WHERE id = ?", (promo_id,))
        promo = c.fetchone()
        points = promo['points']
        
        c.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points, user_id))
        
        # Записываем активацию
        c.execute(
            "INSERT INTO promo_activations (promo_id, user_id) VALUES (?, ?)",
            (promo_id, user_id)
        )
        
        # Увеличиваем счетчик использований
        c.execute(
            "UPDATE promocodes SET used_count = used_count + 1 WHERE id = ?",
            (promo_id,)
        )
        
        conn.commit()
        return True
    except:
        return False
    finally:
        if conn:
            conn.close()
# ==================== КОНСТАНТЫ ДЛЯ ИГРЫ MINES ====================

MINES_FIELD_SIZE = 5
MINES_MAX_BET = 1000

# Фиксированные коэффициенты для разного количества мин
MINES_MULTIPLIERS = {
    2: {
        1: 1.03, 2: 1.12, 3: 1.23, 4: 1.35, 5: 1.49,
        6: 1.64, 7: 1.80, 8: 1.98, 9: 2.18, 10: 2.40,
        11: 2.64, 12: 2.90, 13: 3.19, 14: 3.51, 15: 3.86,
        16: 4.25, 17: 4.68, 18: 5.15, 19: 5.67, 20: 6.24,
        21: 6.86, 22: 7.55, 23: 285.0
    },
    3: {
        1: 1.07, 2: 1.23, 3: 1.41, 4: 1.62, 5: 1.86,
        6: 2.14, 7: 2.46, 8: 2.83, 9: 3.25, 10: 3.74,
        11: 4.30, 12: 4.95, 13: 5.69, 14: 6.54, 15: 7.52,
        16: 8.65, 17: 9.95, 18: 11.44, 19: 13.16, 20: 15.13,
        21: 546.25, 22: 2185.0
    },
    5: {
        1: 1.18, 2: 1.50, 3: 1.91, 4: 2.43, 5: 3.09,
        6: 3.93, 7: 5.00, 8: 6.36, 9: 8.09, 10: 10.29,
        11: 13.09, 12: 16.65, 13: 21.18, 14: 26.94, 15: 34.27,
        16: 43.59, 17: 55.45, 18: 70.53, 19: 8412.24, 20: 50473.49
    },
    10: {
        1: 1.58, 2: 2.71, 3: 4.80, 4: 8.50, 5: 15.05,
        6: 26.64, 7: 47.15, 8: 83.46, 9: 147.72, 10: 261.46,
        11: 462.78, 12: 819.12, 13: 1449.84, 14: 282302.0, 15: 3105322.0
    },
    24: {
        1: 23.75  # Только один шаг с коэффициентом 23.75
    }
}

# Максимальное количество шагов для каждого уровня мин
MAX_STEPS = {
    2: 23,
    3: 22,
    5: 20,
    10: 15,
    24: 1  # Исправлено: только 1 шаг для 24 мин
}

# Доступные варианты количества мин для выбора игроком
AVAILABLE_MINES_COUNTS = [2, 3, 5, 10, 24]
# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ MINES ====================

def create_mines_field(mines_count: int) -> Tuple[List[str], List[int]]:
    """Создание игрового поля с заданным количеством мин"""
    field = ['⬜'] * (MINES_FIELD_SIZE * MINES_FIELD_SIZE)
    mine_positions = random.sample(range(len(field)), mines_count)
    return field, mine_positions

def format_mines_field(field: List[str], opened: List[int] = None, mines_count: int = None) -> str:
    """Форматирование поля для отображения"""
    if opened is None:
        opened = []
    
    result = "💣 MINES 💣\n\n"
    if mines_count:
        result += f"💣 Мин на поле: {mines_count}\n"
    result += "\n"
    
    for i in range(0, len(field), MINES_FIELD_SIZE):
        row = ""
        for j in range(MINES_FIELD_SIZE):
            idx = i + j
            if idx in opened:
                row += "✅ "
            else:
                row += "⬜ "
        result += row + "\n"
    
    return result

def get_multiplier(mines_count: int, step: int) -> float:
    """Получить множитель для конкретного шага"""
    if mines_count in MINES_MULTIPLIERS and step in MINES_MULTIPLIERS[mines_count]:
        return MINES_MULTIPLIERS[mines_count][step]
    return 1.0

def get_max_steps(mines_count: int) -> int:
    """Получить максимальное количество шагов для данного количества мин"""
    return MAX_STEPS.get(mines_count, 0)

def calculate_potential_win(bet: int, mines_count: int, current_step: int) -> int:
    """Рассчитать потенциальный выигрыш на следующем шаге"""
    next_step = current_step + 1
    max_steps = get_max_steps(mines_count)
    
    # Если следующий шаг больше максимального, возвращаем 0
    if next_step > max_steps:
        return 0
        
    multiplier = get_multiplier(mines_count, next_step)
    return int(bet * multiplier)

    # ==================== ФУНКЦИИ СТАТИСТИКИ ====================

@retry_on_locked()
def get_statistics() -> dict:
    """Получить общую статистику"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        stats = {}
        
        c.execute("SELECT COUNT(*) as count FROM users")
        stats['total_users'] = c.fetchone()['count']
        
        c.execute("SELECT COUNT(*) as count FROM users WHERE date(joined_at) = date('now')")
        stats['new_users_today'] = c.fetchone()['count']
        
        c.execute("SELECT SUM(spins) as total FROM users")
        row = c.fetchone()
        stats['total_spins'] = row['total'] if row['total'] else 0
        
        c.execute("SELECT SUM(points) as total FROM users")
        row = c.fetchone()
        stats['total_points'] = row['total'] if row['total'] else 0
        
        c.execute("SELECT COUNT(*) as count FROM completed_tasks")
        stats['total_tasks'] = c.fetchone()['count']
        
        c.execute("SELECT COUNT(*) as count FROM shop_promocodes WHERE is_used = 1")
        stats['total_purchases'] = c.fetchone()['count']
        
        c.execute("SELECT COUNT(*) as count FROM jackpot_promocodes WHERE is_used = 1")
        stats['total_jackpots'] = c.fetchone()['count']
        
        c.execute("SELECT AVG(points) as avg FROM users")
        row = c.fetchone()
        stats['avg_points'] = round(row['avg'] if row['avg'] else 0, 2)
        
        c.execute("SELECT user_id, username, points FROM users ORDER BY points DESC LIMIT 5")
        stats['top_users'] = [dict(row) for row in c.fetchall()]
        
        return stats
    finally:
        if conn:
            conn.close()

# ==================== КЛАВИАТУРЫ ====================

def get_main_keyboard(user_id: int = None):
    """Главная клавиатура"""
    keyboard = [
        [KeyboardButton(text="📂 ПРОЕКТЫ"), KeyboardButton(text="📋 ЗАДАНИЯ")],
        [KeyboardButton(text="👥 РЕФЕРАЛЫ"), KeyboardButton(text="🎡 КОЛЕСО")],
        [KeyboardButton(text="🏪 МАГАЗИН"), KeyboardButton(text="👤 ПРОФИЛЬ")],
        [KeyboardButton(text="🎁 БОНУСЫ"), KeyboardButton(text="💣 MINES")],  # Добавлена кнопка MINES
    ]
    
    if user_id and user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton(text="⚙️ АДМИН")])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


    # ==================== ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЬСКОЙ ЧАСТИ ====================

@dp.message(CommandStart())
async def command_start(message: Message, command: CommandStart):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    referrer_id = None
    
    if command.args and command.args.startswith('ref_'):
        try:
            referrer_id = int(command.args.split('_')[1])
            if referrer_id == user_id:
                referrer_id = None
        except:
            pass
    
    is_new = register_user(user_id, username, first_name, referrer_id)
    
    if is_new:
        welcome_text = (
            "🎰 ДОБРО ПОЖАЛОВАТЬ В VIP КАЗИНО! 🎰\n\n"
            "🔥 Здесь ты можешь крутить колесо фортуны, получать промокоды "
            "и выигрывать реальные призы!\n\n"
            "👇 Твой баланс:\n"
            f"🎡 Попыток вращения: {get_user_spins(user_id)}\n"
            f"💰 Баллов: {get_user_points(user_id)}\n\n"
            "Используй кнопки меню чтобы начать игру!"
        )
    else:
        welcome_text = (
            "🎰 С ВОЗВРАЩЕНИЕМ В VIP КАЗИНО! 🎰\n\n"
            f"👇 Твой баланс:\n"
            f"🎡 Попыток вращения: {get_user_spins(user_id)}\n"
            f"💰 Баллов: {get_user_points(user_id)}\n\n"
            "Используй кнопки меню чтобы продолжить игру!"
        )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id))

@dp.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    """Вход в админ панель по команде /admin"""
    user_id = message.from_user.id
    
    if user_id in ADMIN_IDS:
        await show_admin_menu(message)
        await message.answer("⚙️ Кнопка админ панели добавлена в меню", 
                           reply_markup=get_main_keyboard(user_id))
    else:
        await message.answer("🔐 Введите пароль для доступа к админ панели:")
        await state.set_state(AdminStates.waiting_for_password)

@dp.message(AdminStates.waiting_for_password)
async def check_admin_password(message: Message, state: FSMContext):
    """Проверка пароля админа"""
    if message.text == ADMIN_PASSWORD:
        await state.clear()
        user_id = message.from_user.id
        if user_id not in ADMIN_IDS:
            ADMIN_IDS.append(user_id)
        await show_admin_menu(message)
        await message.answer("✅ Пароль верный! Кнопка админ панели добавлена в меню",
                           reply_markup=get_main_keyboard(user_id))
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()

@dp.message(F.text == "👤 ПРОФИЛЬ")
async def profile(message: Message):
    """Профиль пользователя"""
    user_id = message.from_user.id
    user_data = get_user_data(user_id)
    referral_count = get_referral_count(user_id)
    
    if user_data:
        profile_text = (
            f"👤 ТВОЙ ПРОФИЛЬ\n\n"
            f"🆔 ID: {user_id}\n"
            f"📅 В игре с: {user_data['joined_at'][:10] if user_data['joined_at'] else 'Неизвестно'}\n\n"
            f"💰 Баллы: {user_data['points']}\n"
            f"🎡 Попытки вращения: {user_data['spins']}\n"
            f"👥 Рефералов: {referral_count}\n"
            f"📊 Статистика:\n"
            f"  • Всего вращений: {user_data['total_spins']}\n"
            f"  • Всего выигрышей: {user_data['total_wins']}"
        )
        await message.answer(profile_text, reply_markup=get_main_keyboard(user_id))

@dp.message(F.text == "👥 РЕФЕРАЛЫ")
async def referrals(message: Message):
    """Реферальная система"""
    user_id = message.from_user.id
    bot_username = (await bot.get_me()).username
    referral_link = get_referral_link(user_id, bot_username)
    referral_count = get_referral_count(user_id)
    
    text = (
        "👥 РЕФЕРАЛЬНАЯ СИСТЕМА\n\n"
        f"🎁 За каждого приглашенного друга ты получаешь 1 попытку вращения!\n\n"
        f"📊 Твои рефералы: {referral_count}\n\n"
        f"🔗 Твоя реферальная ссылка:\n{referral_link}\n\n"
        "💡 Отправь эту ссылку друзьям и получай бонусы!"
    )
    
    await message.answer(text, reply_markup=get_main_keyboard(user_id))

@dp.message(F.text == "📂 ПРОЕКТЫ")
async def projects(message: Message, state: FSMContext):
    """Список проектов с навигацией (в одном сообщении)"""
    user_id = message.from_user.id
    projects_list = get_projects()
    
    if not projects_list:
        await message.answer("📂 ПРОЕКТЫ\n\nК сожалению, сейчас нет активных проектов.", 
                           reply_markup=get_main_keyboard(user_id))
        return
    
    # Сохраняем список проектов в состоянии
    await state.update_data(projects=projects_list, current_index=0)
    
    # Показываем первый проект
    await show_project(message, state, 0, is_new_message=True)

async def show_project(message: Message, state: FSMContext, index: int, is_new_message: bool = False):
    """Показать проект по индексу (с редактированием существующего сообщения)"""
    data = await state.get_data()
    projects = data.get('projects', [])
    
    if not projects or index < 0 or index >= len(projects):
        await message.answer("❌ Проект не найден")
        return
    
    project = projects[index]
    
    # Формируем текст сообщения
    text = (
        f"🎰 <b>{project['title']}</b>\n\n"
        f"📌 <b>Промокод:</b> <code>{project['promo_code']}</code>\n\n"
        f"🔗 <a href='{project['url']}'>Перейти на сайт</a>"
    )
    
    # Создаем клавиатуру для навигации
    keyboard = InlineKeyboardBuilder()
    
    # Кнопки навигации
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data="proj_prev"))
    if index < len(projects) - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data="proj_next"))
    if nav_row:
        keyboard.row(*nav_row)
    
    # Кнопка перехода на сайт
    keyboard.row(InlineKeyboardButton(text="🎰 Перейти на сайт", url=project['url']))
    
    # Информация о текущем проекте
    keyboard.row(InlineKeyboardButton(
        text=f"📊 Проект {index + 1} из {len(projects)}", 
        callback_data="proj_info"
    ))
    
    # Кнопка назад в меню
    keyboard.row(InlineKeyboardButton(text="◀️ В меню", callback_data="back_to_menu"))
    
    # Обновляем индекс в состоянии
    await state.update_data(current_index=index)
    
    # Проверяем, есть ли фото
    photo_url = project.get('photo_url')
    
    # Отправляем или редактируем сообщение
    if is_new_message:
        # Первый проект - отправляем новое сообщение
        if photo_url:
            try:
                await message.answer_photo(
                    photo=photo_url,
                    caption=text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке фото: {e}")
                await message.answer(
                    text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
        else:
            await message.answer(
                text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                disable_web_page_preview=True
            )
    else:
        # Редактируем существующее сообщение
        if photo_url:
            try:
                # Проверяем, есть ли у сообщения caption (значит это сообщение с фото)
                if hasattr(message, 'caption') and message.caption is not None:
                    await message.edit_caption(
                        caption=text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                else:
                    # Если это текстовое сообщение, но нужно показать фото
                    await message.delete()
                    await message.answer_photo(
                        photo=photo_url,
                        caption=text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML',
                        disable_web_page_preview=True
                    )
            except Exception as e:
                logger.error(f"Ошибка при редактировании фото: {e}")
                try:
                    await message.edit_text(
                        text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML',
                        disable_web_page_preview=True
                    )
                except Exception as e2:
                    logger.error(f"Ошибка при редактировании текста: {e2}")
        else:
            # Просто текстовое сообщение без фото
            try:
                await message.edit_text(
                    text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            except Exception as e:
                logger.error(f"Ошибка при редактировании текста: {e}")
    
    # Обновляем индекс в состоянии
    await state.update_data(current_index=index)

@dp.callback_query(F.data == "proj_prev")
async def project_prev(callback: CallbackQuery, state: FSMContext):
    """Предыдущий проект (редактируем текущее сообщение)"""
    await callback.answer()
    
    data = await state.get_data()
    current_index = data.get('current_index', 0)
    
    if current_index > 0:
        new_index = current_index - 1
        # Передаем callback.message, а не callback
        await show_project(callback.message, state, new_index, is_new_message=False)
    else:
        await callback.answer("Это первый проект", show_alert=True)

@dp.callback_query(F.data == "proj_next")
async def project_next(callback: CallbackQuery, state: FSMContext):
    """Следующий проект (редактируем текущее сообщение)"""
    await callback.answer()
    
    data = await state.get_data()
    projects = data.get('projects', [])
    current_index = data.get('current_index', 0)
    
    if current_index < len(projects) - 1:
        new_index = current_index + 1
        # Передаем callback.message, а не callback
        await show_project(callback.message, state, new_index, is_new_message=False)
    else:
        await callback.answer("Это последний проект", show_alert=True)

@dp.callback_query(F.data == "proj_info")
async def project_info(callback: CallbackQuery):
    """Информация о навигации"""
    await callback.answer("Используйте кнопки ◀️ и ▶️ для переключения", show_alert=False)

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await callback.answer()
    await state.clear()
    await callback.message.delete()
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_keyboard(callback.from_user.id)
    )

@dp.message(F.text == "📋 ЗАДАНИЯ")
async def tasks_menu(message: Message):
    """Список заданий"""
    user_id = message.from_user.id
    all_tasks, completed_tasks = get_user_tasks(user_id)
    
    if not all_tasks:
        await message.answer("📋 ЗАДАНИЯ\n\nК сожалению, сейчас нет доступных заданий.",
                           reply_markup=get_main_keyboard(user_id))
        return
    
    text = "📋 ЗАДАНИЯ\n\nВыполняй задания и получай бонусы!\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for task in all_tasks:
        task_id = task['id']
        if task_id not in completed_tasks:
            button_text = f"{task['task_name']} (+{task['reward_spins']}🎡 +{task['reward_points']}💰)"
            keyboard.row(InlineKeyboardButton(
                text=button_text,
                callback_data=f"task_{task_id}"
            ))
        else:
            keyboard.row(InlineKeyboardButton(
                text=f"✅ {task['task_name']} (Выполнено)",
                callback_data="done"
            ))
    
    await message.answer(text, reply_markup=keyboard.as_markup())
    await message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

# ==================== ОБРАБОТЧИКИ ЗАДАНИЙ ====================
# ВАЖНО: порядок имеет значение - от более специфичных к более общим

@dp.callback_query(F.data == "done")
async def done_callback(callback: CallbackQuery):
    """Обработчик для уже выполненных заданий"""
    await callback.answer("Это задание уже выполнено!", show_alert=True)

@dp.callback_query(F.data.startswith("task_complete_"))
async def task_complete_callback(callback: CallbackQuery):
    """Подтверждение выполнения задания (для сайтов)"""
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) >= 3:
            task_id = int(parts[2])
        else:
            await safe_edit_message(callback.message, "❌ Неверный формат данных")
            return
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка в task_complete_callback: {e}")
        await safe_edit_message(callback.message, "❌ Неверный формат данных")
        return
    
    user_id = callback.from_user.id
    
    task = get_task_by_id(task_id)
    
    if not task:
        await safe_edit_message(callback.message, "❌ Задание не найдено!")
        return
    
    if task['task_type'] != "website":
        await safe_edit_message(callback.message, "❌ Неверный тип задания!")
        return
    
    if complete_task(user_id, task_id):
        await safe_edit_message(
            callback.message,
            f"✅ Задание выполнено!\n\n"
            f"Получено: +{task['reward_spins']}🎡 +{task['reward_points']}💰"
        )
        await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))
    else:
        await safe_edit_message(callback.message, "❌ Задание уже было выполнено ранее!")

@dp.callback_query(F.data.startswith("check_"))
async def check_callback(callback: CallbackQuery):
    """Проверка подписки (для каналов/чатов)"""
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) >= 2:
            task_id = int(parts[1])
        else:
            await safe_edit_message(callback.message, "❌ Неверный формат данных")
            return
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка в check_callback: {e}")
        await safe_edit_message(callback.message, "❌ Неверный формат данных")
        return
    
    user_id = callback.from_user.id
    
    if complete_task(user_id, task_id):
        task = get_task_by_id(task_id)
        await safe_edit_message(
            callback.message,
            f"✅ Задание выполнено!\n\n"
            f"Получено: +{task['reward_spins']}🎡 +{task['reward_points']}💰"
        )
        await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))
    else:
        await safe_edit_message(callback.message, "❌ Уже выполнено или ошибка")

@dp.callback_query(F.data.startswith("complete_"))
async def complete_callback(callback: CallbackQuery):
    """Завершение задания (для сайтов) - альтернативный обработчик"""
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        if len(parts) >= 2:
            task_id = int(parts[1])
        else:
            await safe_edit_message(callback.message, "❌ Неверный формат данных")
            return
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка в complete_callback: {e}")
        await safe_edit_message(callback.message, "❌ Неверный формат данных")
        return
    
    user_id = callback.from_user.id
    
    if complete_task(user_id, task_id):
        task = get_task_by_id(task_id)
        await safe_edit_message(
            callback.message,
            f"✅ Задание выполнено!\n\n"
            f"Получено: +{task['reward_spins']}🎡 +{task['reward_points']}💰"
        )
        await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))
    else:
        await safe_edit_message(callback.message, "❌ Уже выполнено или ошибка")

@dp.callback_query(F.data.startswith("task_"))
async def task_callback(callback: CallbackQuery):
    """Выполнение задания (основной)"""
    await callback.answer()
    
    # Явно проверяем, что это не task_complete_
    if callback.data.startswith("task_complete_"):
        # Этот обработчик не для task_complete_, пропускаем
        return
    
    try:
        parts = callback.data.split("_")
        if len(parts) >= 2:
            task_id = int(parts[1])
        else:
            await safe_edit_message(callback.message, "❌ Неверный формат данных")
            return
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка в task_callback: {e}, data: {callback.data}")
        return
    
    user_id = callback.from_user.id
    
    task = get_task_by_id(task_id)
    
    if not task:
        await safe_edit_message(callback.message, "❌ Задание не найдено!")
        return
    
    if task['task_type'] in ["channel", "chat"]:
        chat_id = task['task_data']
        chat_name = chat_id
        chat_link = f"https://t.me/{chat_id.replace('@', '')}" if chat_id.startswith('@') else chat_id
        
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            is_subscribed = member.status in ['member', 'administrator', 'creator']
            
            if is_subscribed:
                if complete_task(user_id, task_id):
                    await safe_edit_message(
                        callback.message,
                        f"✅ Задание выполнено!\n\n"
                        f"Вы подписаны на {chat_name}\n"
                        f"Получено: +{task['reward_spins']}🎡 +{task['reward_points']}💰"
                    )
                    await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))
                else:
                    await safe_edit_message(callback.message, "❌ Задание уже было выполнено ранее!")
            else:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"📢 Подписаться на {chat_name}", url=chat_link)],
                    [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data=f"check_{task_id}")]
                ])
                
                await safe_edit_message(
                    callback.message,
                    f"📋 ЗАДАНИЕ: {task['task_name']}\n\n"
                    f"{task['task_description']}\n\n"
                    f"❌ Вы не подписаны на {chat_name}!\n\n"
                    f"1. Нажмите кнопку ниже, чтобы подписаться\n"
                    f"2. Затем нажмите 'Проверить подписку'\n\n"
                    f"Награда: +{task['reward_spins']}🎡 +{task['reward_points']}💰",
                    reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Ошибка при проверке подписки: {e}")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📢 Подписаться на {chat_name}", url=chat_link)],
                [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data=f"check_{task_id}")]
            ])
            
            await safe_edit_message(
                callback.message,
                f"📋 ЗАДАНИЕ: {task['task_name']}\n\n"
                f"{task['task_description']}\n\n"
                f"❌ Не удалось проверить подписку.\n"
                f"Убедитесь, что вы подписались и нажмите кнопку проверки.\n\n"
                f"Ссылка для подписки: {chat_link}\n\n"
                f"Награда: +{task['reward_spins']}🎡 +{task['reward_points']}💰",
                reply_markup=keyboard
            )
    
    elif task['task_type'] == "website":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Перейти на сайт", url=task['task_data'])],
            [InlineKeyboardButton(text="✅ Я посетил сайт", callback_data=f"complete_{task_id}")]
        ])
        
        await safe_edit_message(
            callback.message,
            f"📋 ЗАДАНИЕ: {task['task_name']}\n\n"
            f"{task['task_description']}\n\n"
            f"1. Перейдите на сайт по ссылке ниже\n"
            f"2. Затем нажмите 'Я посетил сайт'\n\n"
            f"Награда: +{task['reward_spins']}🎡 +{task['reward_points']}💰",
            reply_markup=keyboard
        )

@dp.message(F.text == "🎡 КОЛЕСО")
async def wheel_of_fortune(message: Message):
    """Колесо фортуны"""
    user_id = message.from_user.id
    spins = get_user_spins(user_id)
    
    if spins <= 0:
        await message.answer(
            "❌ У тебя закончились попытки!\n\n"
            "Забери бесплатную попытку в разделе БОНУСЫ или выполняй задания!",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎡 КРУТИТЬ КОЛЕСО (осталось {spins})", callback_data="spin_wheel")]
    ])
    
    await message.answer(
        f"🎡 КОЛЕСО ФОРТУНЫ\n\n"
        f"У тебя {spins} попыток вращения\n\n"
        f"Нажми кнопку ниже, чтобы крутить колесо!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "spin_wheel")
async def spin_wheel_callback(callback: CallbackQuery):
    """Вращение колеса"""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    try:
        spins = get_user_spins(user_id)
        print(f"🔍 Попыток у пользователя {user_id}: {spins}")
        
        if spins <= 0:
            await safe_edit_message(callback.message, "❌ У тебя нет попыток! Забери бесплатную попытку в разделе БОНУСЫ.")
            return
        
        # Списываем попытку
        update_user_spins(user_id, -1)
        print(f"✅ Попытка списана")
        
        # Крутим колесо
        result = spin_wheel()
        print(f"✅ Результат вращения: {result}")
        
        await safe_edit_message(callback.message, "🎡 Колесо вращается... 🎡")
        await asyncio.sleep(2)
        
        result_text = ""
        
        if result["type"] == "points":
            update_user_points(user_id, result["value"])
            update_user_stats(user_id, add_win=True)
            result_text = f"💰 +{result['value']} баллов!"
        
        elif result["type"] == "spins":
            update_user_spins(user_id, result["value"])
            update_user_stats(user_id, add_win=True)
            result_text = f"🎡 +{result['value']} попытка!"
        
        elif result["type"] == "jackpot":
            promo = get_random_jackpot_promocode()
            
            if promo:
                use_jackpot_promocode(promo['id'], user_id)
                update_user_stats(user_id, add_win=True)
                
                user_display = get_user_display(user_id, callback.from_user.username, callback.from_user.first_name)
                
                result_text = f"🎰 ДЖЕКПОТ!\n\nПромокод: {promo['promo_code']}"
                
                if promo.get('project_url'):
                    result_text += f"\n\n🔗 Ссылка на проект: {promo['project_url']}"
                
                add_admin_notification(
                    "jackpot",
                    f"🎰 ДЖЕКПОТ!\nПользователь: {user_display}\nПромокод: {promo['promo_code']}",
                    user_id
                )
            else:
                result_text = "🎰 ДЖЕКПОТ!\nНо все промокоды закончились :("
        
        else:
            result_text = "💔 Пусто...\nПовезет в следующий раз!"
        
        new_spins = get_user_spins(user_id)
        new_points = get_user_points(user_id)
        
        final_text = (
            f"🎡 РЕЗУЛЬТАТ ВРАЩЕНИЯ\n\n"
            f"{result_text}\n\n"
            f"Текущий баланс:\n"
            f"🎡 Попыток: {new_spins}\n"
            f"💰 Баллов: {new_points}"
        )
        
        await safe_edit_message(callback.message, final_text)
        
        if new_spins > 0:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🎡 КРУТИТЬ ЕЩЕ (осталось {new_spins})", callback_data="spin_wheel")]
            ])
            await callback.message.answer("Нажми кнопку, чтобы крутить еще!", reply_markup=keyboard)
        else:
            await callback.message.answer("Попытки закончились! Забери бесплатную попытку в разделе БОНУСЫ.", 
                                        reply_markup=get_main_keyboard(user_id))
    
    except Exception as e:
        print(f"❌ Ошибка в spin_wheel_callback: {e}")
        import traceback
        traceback.print_exc()
        await safe_edit_message(callback.message, "❌ Произошла ошибка при вращении колеса. Попробуйте еще раз.")

@dp.message(F.text == "🏪 МАГАЗИН")
async def shop(message: Message, state: FSMContext):
    """Магазин промокодов (сгруппированный)"""
    user_id = message.from_user.id
    items = get_shop_items()
    
    if not items:
        await message.answer("🏪 МАГАЗИН\n\nК сожалению, сейчас нет доступных товаров.",
                           reply_markup=get_main_keyboard(user_id))
        return
    
    text = "🏪 МАГАЗИН ПРОМОКОДОВ\n\n"
    text += "Обменивай баллы на реальные промокоды!\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for idx, item in enumerate(items):
        available = item.get('available', item.get('quantity', 1))
        text += f"📦 <b>{item['name']}</b>\n"
        text += f"💰 Цена: {item['price']} баллов\n"
        text += f"📊 В наличии: {available} шт.\n"
        if item.get('project_title'):
            text += f"🏢 Проект: {item['project_title']}\n"
        text += "\n"
        
        keyboard.row(InlineKeyboardButton(
            text=f"🛒 Купить {item['name'][:20]}... | {item['price']}💰",
            callback_data=f"buy_{idx}_{item['price']}"
        ))
    
    await state.update_data(shop_items=items)
    
    await message.answer(text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
    await message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

# ==================== ВАЖНО! РАЗНЫЕ ФИЛЬТРЫ ====================

# ТОЛЬКО ОДИН ОБРАБОТЧИК!
@dp.callback_query(F.data.startswith("buy_"))
async def buy_group_callback(callback: CallbackQuery, state: FSMContext):
    """Единый обработчик для всех покупок"""
    print(f"✅ buy_callback сработал! Data: {callback.data}")
    
    await callback.answer()
    
    try:
        parts = callback.data.split("_")
        
        # Проверяем формат (должен быть buy_ИНДЕКС_ЦЕНА)
        if len(parts) != 3:
            print(f"❌ Неправильный формат: {parts}")
            await callback.message.edit_text("❌ Неверный формат данных")
            return
        
        idx = int(parts[1])
        price = int(parts[2])
        
        # Получаем сохраненный список товаров
        data = await state.get_data()
        items = data.get('shop_items', [])
        
        if not items:
            items = get_shop_items()
            if not items:
                await callback.message.edit_text("❌ В магазине нет товаров")
                return
        
        if idx >= len(items):
            await callback.message.edit_text("❌ Товар не найден")
            return
        
        item = items[idx]
        name = item['name']
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await callback.message.edit_text("❌ Ошибка при обработке запроса")
        return
    
    user_id = callback.from_user.id
    success, result, purchased_item = buy_shop_item(user_id, name, price)
    
    if success:
        items_left = get_shop_items()
        remaining = 0
        for it in items_left:
            if it['name'] == name and it['price'] == price:
                remaining = it.get('available', it.get('quantity', 0))
                break
        
        text = f"✅ Покупка успешна!\n\n🎫 Твой промокод:\n{result}"
        
        if purchased_item and purchased_item.get('project_url'):
            text += f"\n\n🔗 Ссылка на проект: {purchased_item['project_url']}"
        
        if remaining > 0:
            text += f"\n\n📊 Осталось товара: {remaining} шт."
        else:
            text += f"\n\n⚠️ Это был последний экземпляр!"
        
        await callback.message.edit_text(text)
    else:
        await callback.message.edit_text(f"❌ {result}")
    
    await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))


 # ==================== БОНУСЫ ====================

@dp.message(F.text == "🎁 БОНУСЫ")
async def bonuses_menu(message: Message, state: FSMContext):
    """Меню бонусов"""
    user_id = message.from_user.id
    
    # Проверяем доступность ежедневного бонуса
    bonus_status = check_daily_bonus(user_id)
    
    # Проверяем доступность бесплатной попытки
    spin_status = check_free_spin(user_id)
    
    text = "🎁 ЕЖЕДНЕВНЫЕ БОНУСЫ\n\n"
    
    # Информация о ежедневном бонусе
    if bonus_status["available"]:
        text += f"✅ Ежедневный бонус доступен!\n"
        text += f"💰 Сумма: {bonus_status['next']} баллов\n"
        if bonus_status["streak"] > 0:
            text += f"🔥 Текущая серия: {bonus_status['streak']} дней\n"
        text += f"🎯 Следующая награда: {get_next_bonus_amount(bonus_status['streak'])} баллов\n\n"
    else:
        text += f"❌ Ежедневный бонус уже получен сегодня\n"
        text += f"🔥 Текущая серия: {bonus_status['streak']} дней\n"
        text += f"📅 Следующий бонус будет доступен: {bonus_status['next_date']}\n\n"
    
    # Информация о бесплатной попытке
    if spin_status["available"]:
        text += f"✅ Бесплатная попытка доступна!\n"
        text += f"🎡 +1 попытка вращения колеса\n\n"
    else:
        text += f"❌ Бесплатная попытка уже получена сегодня\n"
        text += f"📅 Следующая попытка будет доступна: {spin_status['next_date']}\n\n"
    
    # Создаем клавиатуру с доступными бонусами
    kb = InlineKeyboardBuilder()
    
    if bonus_status["available"]:
        kb.row(InlineKeyboardButton(
            text=f"💰 Забрать {bonus_status['next']} баллов", 
            callback_data="claim_daily_bonus"
        ))
    
    if spin_status["available"]:
        kb.row(InlineKeyboardButton(
            text="🎡 Забрать бесплатную попытку", 
            callback_data="claim_free_spin"
        ))
    
    # Новая кнопка для активации промокода
    kb.row(InlineKeyboardButton(
        text="🎫 Активировать промокод",
        callback_data="activate_promo"
    ))
    
    if not bonus_status["available"] and not spin_status["available"]:
        text += "✨ Все бонусы на сегодня получены! Возвращайся завтра!\n\n"
    
    await message.answer(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "claim_daily_bonus")
async def claim_daily_bonus_callback(callback: CallbackQuery):
    """Получение ежедневного бонуса"""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    result = claim_daily_bonus(user_id)
    
    if result["success"]:
        await safe_edit_message(
            callback.message,
            f"✅ Вы получили ежедневный бонус!\n\n"
            f"💰 +{result['amount']} баллов\n"
            f"🔥 Текущая серия: {result['new_streak']} дней\n\n"
            f"Завтра вас ждет {get_next_bonus_amount(result['new_streak'])} баллов!"
        )
    else:
        await safe_edit_message(callback.message, f"❌ Ошибка при получении бонуса: {result.get('error', 'Неизвестная ошибка')}")
    
    await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

@dp.callback_query(F.data == "claim_free_spin")
async def claim_free_spin_callback(callback: CallbackQuery):
    """Получение бесплатной попытки"""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    if claim_free_spin(user_id):
        new_spins = get_user_spins(user_id)
        await safe_edit_message(
            callback.message,
            f"✅ Вы получили бесплатную попытку!\n\n"
            f"🎡 Теперь у вас {new_spins} попыток вращения\n\n"
            f"Используйте их в разделе КОЛЕСО ФОРТУНЫ!"
        )
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при получении бесплатной попытки")
    
    await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

@dp.callback_query(F.data == "activate_promo")
async def activate_promo_start(callback: CallbackQuery, state: FSMContext):
    """Начало активации промокода"""
    await callback.answer()
    
    await callback.message.edit_text(
        "🎫 Введите промокод:\n\n"
        "Пример: BONUS100"
    )
    await state.set_state("waiting_for_promo_code")

@dp.message(F.text, StateFilter("waiting_for_promo_code"))
async def activate_promo_process(message: Message, state: FSMContext):
    """Обработка введенного промокода"""
    user_id = message.from_user.id
    promo_code = message.text.strip()
    
    # Проверяем промокод
    result = check_promocode_available(promo_code, user_id)
    
    if not result["success"]:
        await message.answer(result["message"])
        await state.clear()
        return
    
    promo = result["promo"]
    
    # Спрашиваем подтверждение
    text = (
        f"🎫 Найден промокод!\n\n"
        f"📌 Название: {promo['name']}\n"
        f"💰 Награда: {promo['points']} баллов\n"
        f"📊 Осталось активаций: {promo['max_uses'] - promo['used_count']}\n\n"
        f"Активировать?"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_promo_{promo['id']}"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_promo")
        ]
    ])
    
    await message.answer(text, reply_markup=keyboard)
    await state.clear()

@dp.callback_query(F.data.startswith("confirm_promo_"))
async def confirm_promo(callback: CallbackQuery, state: FSMContext):
    """Подтверждение активации промокода"""
    await callback.answer()
    
    promo_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    # Проверяем еще раз (на случай, если кто-то успел активировать)
    promo = get_promocode_by_id(promo_id)
    if not promo:
        await callback.message.edit_text("❌ Промокод не найден")
        return
    
    result = check_promocode_available(promo['code'], user_id)
    if not result["success"]:
        await callback.message.edit_text(result["message"])
        return
    
    # Активируем
    if activate_promocode(promo_id, user_id):
        text = (
            f"✅ Вы успешно активировали промокод!\n\n"
            f"📌 {promo['name']}\n"
            f"💰 +{promo['points']} баллов\n\n"
            f"Новый баланс: {get_user_points(user_id)} баллов"
        )
        await callback.message.edit_text(text)
    else:
        await callback.message.edit_text("❌ Ошибка при активации промокода")

@dp.callback_query(F.data == "cancel_promo")
async def cancel_promo(callback: CallbackQuery):
    """Отмена активации промокода"""
    await callback.answer()
    await callback.message.edit_text("❌ Активация отменена")
# ==================== АДМИН ПАНЕЛЬ ====================

@dp.message(F.text == "⚙️ АДМИН ПАНЕЛЬ")
async def show_admin_menu(message: Message):
    """Показать меню администратора"""
    notifications_count = get_unread_notifications_count()
    promocodes = get_all_promocodes(include_inactive=True)
    active_promos = sum(1 for p in promocodes if p['is_active'])
    
    text = "⚙️ АДМИН ПАНЕЛЬ\n\n"
    if notifications_count > 0:
        text += f"🔔 У вас {notifications_count} новых уведомлений!\n\n"
    text += f"🎫 Активных промокодов: {active_promos}\n\n"
    text += "Выберите раздел для управления:"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text=f"🔔 Уведомления ({notifications_count})", callback_data="admin_notifications")],
        [InlineKeyboardButton(text="📂 Управление проектами", callback_data="admin_projects")],
        [InlineKeyboardButton(text="🏪 Управление магазином", callback_data="admin_shop")],
        [InlineKeyboardButton(text="🎡 Управление колесом", callback_data="admin_wheel")],
        [InlineKeyboardButton(text="📋 Управление заданиями", callback_data="admin_tasks")],
        [InlineKeyboardButton(text="🎰 Управление джекпотом", callback_data="admin_jackpot")],
        [InlineKeyboardButton(text="🎫 Управление промокодами", callback_data="admin_promocodes")],  # Новая кнопка
        [InlineKeyboardButton(text="💣 Настройка Mines", callback_data="admin_mines")],
        [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="💰 Начислить бонусы", callback_data="admin_add_bonus")],
    ])
    
    await message.answer(text, reply_markup=keyboard)

@dp.message(F.text == "⚙️ АДМИН")
async def admin_panel(message: Message, state: FSMContext):
    """Вход в админ панель"""
    user_id = message.from_user.id
    
    if user_id in ADMIN_IDS:
        await show_admin_menu(message)
    else:
        await message.delete()
        msg = await message.answer("⛔ Доступ запрещен!")
        await asyncio.sleep(2)
        await msg.delete()
# ==================== УВЕДОМЛЕНИЯ ====================

@dp.callback_query(F.data == "admin_notifications")
async def admin_notifications(callback: CallbackQuery):
    """Просмотр уведомлений"""
    await callback.answer()
    
    notifications = get_notifications(20)
    
    if not notifications:
        await safe_edit_message(
            callback.message,
            "🔔 УВЕДОМЛЕНИЯ\n\nНет новых уведомлений.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin")]
            ])
        )
        return
    
    mark_notifications_as_read()
    
    text = "🔔 ПОСЛЕДНИЕ УВЕДОМЛЕНИЯ\n\n"
    
    for notif in notifications[:10]:
        time_str = notif['created_at'][:16] if notif['created_at'] else "Неизвестно"
        text += f"[{time_str}] {notif['type']}: {notif['message']}\n\n"
    
    if len(notifications) > 10:
        text += f"... и еще {len(notifications) - 10} уведомлений\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_notifications")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)

# ==================== СТАТИСТИКА ====================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    """Статистика"""
    await callback.answer()
    
    stats = get_statistics()
    
    text = (
        f"📊 СТАТИСТИКА\n\n"
        f"👥 ПОЛЬЗОВАТЕЛИ:\n"
        f"   • Всего: {stats['total_users']}\n"
        f"   • Новых за 24ч: {stats['new_users_today']}\n"
        f"   • Средний балл: {stats['avg_points']}\n\n"
        f"🎡 АКТИВНОСТЬ:\n"
        f"   • Всего вращений: {stats['total_spins']}\n"
        f"   • Всего баллов: {stats['total_points']}\n"
        f"   • Выполнено заданий: {stats['total_tasks']}\n\n"
        f"💰 ЭКОНОМИКА:\n"
        f"   • Продаж в магазине: {stats['total_purchases']}\n"
        f"   • Джекпотов: {stats['total_jackpots']}\n\n"
        f"🏆 ТОП-5 ПО БАЛЛАМ:\n"
    )
    
    for user in stats['top_users']:
        user_display = get_user_display(user['user_id'], user['username'], None)
        text += f"   • {user_display}: {user['points']}💰\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)

# ==================== УПРАВЛЕНИЕ ПРОЕКТАМИ ====================

@dp.callback_query(F.data == "admin_projects")
async def admin_projects_menu(callback: CallbackQuery):
    """Управление проектами"""
    await callback.answer()
    
    projects = get_projects(include_inactive=True)
    
    text = "📂 УПРАВЛЕНИЕ ПРОЕКТАМИ\n\n"
    
    if not projects:
        text += "Нет добавленных проектов."
    else:
        for proj in projects:
            status = "✅" if proj['is_active'] else "❌"
            text += f"{status} {proj['title']}\n"
            text += f"   URL: {proj['url'][:30]}...\n"
            text += f"   Промо: {proj['promo_code']}\n\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить проект", callback_data="add_project"))
    keyboard.row(InlineKeyboardButton(text="✏️ Редактировать проекты", callback_data="edit_projects_list"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "edit_projects_list")
async def edit_projects_list(callback: CallbackQuery):
    """Список проектов для редактирования"""
    await callback.answer()
    
    projects = get_projects(include_inactive=True)
    
    text = "📂 ВЫБЕРИТЕ ПРОЕКТ ДЛЯ РЕДАКТИРОВАНИЯ\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for proj in projects:
        status = "✅" if proj['is_active'] else "❌"
        keyboard.row(InlineKeyboardButton(
            text=f"{status} {proj['title']}",
            callback_data=f"edit_project_{proj['id']}"
        ))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_projects"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "add_project")
async def add_project_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления проекта"""
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "Введите данные проекта в формате:\n"
        "Название | Ссылка | Промокод | Ссылка на фото (необязательно)\n\n"
        "Пример: Casino X | https://example.com | XBONUS | https://telegra.ph/file/xxx.jpg\n\n"
        "✅ <b>Рекомендуемые хостинги (работают в РФ):</b>\n"
        "• <b>Telegraph</b> - через @Telegraph_bot\n"
        "• <b>Postimages</b> - https://postimages.org (берите Direct link)\n"
        "• <b>ImgBB</b> - https://imgbb.com (берите Direct link)\n\n"
        "❌ <b>Imgur НЕ РАБОТАЕТ</b> в России",
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.waiting_for_project_data)

@dp.message(AdminStates.waiting_for_project_data)
async def add_project_finish(message: Message, state: FSMContext):
    """Сохранение проекта"""
    try:
        parts = [x.strip() for x in message.text.split(" | ")]
        
        if len(parts) < 3:
            await message.answer("❌ Ошибка! Нужно минимум 3 части: Название | Ссылка | Промокод")
            return
        
        title = parts[0]
        url = parts[1]
        promo_code = parts[2]
        photo_url = parts[3] if len(parts) > 3 else None
        
        project_id = add_project(title, url, promo_code, photo_url)
        
        if project_id:
            await message.answer(f"✅ Проект успешно добавлен! ID: {project_id}")
        else:
            await message.answer("❌ Ошибка при добавлении проекта")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка! {str(e)}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("edit_project_") & ~F.data.startswith("edit_project_title_") & ~F.data.startswith("edit_project_url_") & ~F.data.startswith("edit_project_promo_"))
async def edit_project_handler(callback: CallbackQuery):
    """Меню редактирования проекта"""
    await callback.answer()
    
    try:
        project_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")
        return
    
    project = get_project_by_id(project_id)
    
    if not project:
        await safe_edit_message(callback.message, "❌ Проект не найден!")
        return
    
    status = "Активен" if project['is_active'] else "Неактивен"
    photo_info = "✅ Есть фото" if project.get('photo_url') else "❌ Нет фото"
    
    text = (
        f"📂 РЕДАКТИРОВАНИЕ ПРОЕКТА\n\n"
        f"ID: {project['id']}\n"
        f"Название: {project['title']}\n"
        f"URL: {project['url']}\n"
        f"Промокод: {project['promo_code']}\n"
        f"Фото: {photo_info}\n"
        f"Статус: {status}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_project_title_{project_id}")],
        [InlineKeyboardButton(text="🔗 Изменить URL", callback_data=f"edit_project_url_{project_id}")],
        [InlineKeyboardButton(text="🎫 Изменить промокод", callback_data=f"edit_project_promo_{project_id}")],
        [InlineKeyboardButton(text="🖼 Изменить фото", callback_data=f"edit_project_photo_{project_id}")],
        [InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"toggle_project_{project_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_project_{project_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_projects_list")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("edit_project_title_"))
async def edit_project_title_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения названия проекта"""
    await callback.answer()
    try:
        project_id = int(callback.data.split("_")[3])
        await state.update_data(project_id=project_id, edit_field="title")
        await safe_edit_message(callback.message, "Введите новое название проекта:")
        await state.set_state(AdminStates.waiting_for_project_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")

@dp.callback_query(F.data.startswith("edit_project_url_"))
async def edit_project_url_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения URL проекта"""
    await callback.answer()
    try:
        project_id = int(callback.data.split("_")[3])
        await state.update_data(project_id=project_id, edit_field="url")
        await safe_edit_message(callback.message, "Введите новый URL проекта:")
        await state.set_state(AdminStates.waiting_for_project_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")

@dp.callback_query(F.data.startswith("edit_project_promo_"))
async def edit_project_promo_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения промокода проекта"""
    await callback.answer()
    try:
        project_id = int(callback.data.split("_")[3])
        await state.update_data(project_id=project_id, edit_field="promo")
        await safe_edit_message(callback.message, "Введите новый промокод:")
        await state.set_state(AdminStates.waiting_for_project_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")

@dp.callback_query(F.data.startswith("edit_project_photo_"))
async def edit_project_photo_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения фото проекта"""
    await callback.answer()
    try:
        # Разбираем callback_data: edit_project_photo_ID
        data_parts = callback.data.split("_")
        
        # Выводим отладку в логи
        logger.info(f"edit_project_photo_start: data={callback.data}, parts={data_parts}")
        
        # Проверяем, что у нас достаточно частей
        if len(data_parts) < 4:
            logger.error(f"Недостаточно частей в callback_data: {callback.data}")
            await safe_edit_message(callback.message, "❌ Неверный формат данных (мало частей)")
            return
        
        # ID проекта находится на последней позиции
        # Формат: edit_project_photo_18 -> части: ['edit', 'project', 'photo', '18']
        try:
            project_id = int(data_parts[-1])  # Берем последний элемент
        except ValueError as e:
            logger.error(f"Не удалось преобразовать ID: {data_parts[-1]}, ошибка: {e}")
            await safe_edit_message(callback.message, "❌ Неверный ID проекта")
            return
        
        # Сохраняем данные в состоянии
        await state.update_data(project_id=project_id, edit_field="photo")
        
        # Отправляем сообщение с инструкцией
        await safe_edit_message(
            callback.message, 
            "📸 <b>Изменение фото проекта</b>\n\n"
            "Введите новую ссылку на фото (или 0 чтобы удалить):\n\n"
            "✅ <b>Рекомендуемые хостинги (работают в РФ):</b>\n"
            "• <b>Telegra.ph</b> - https://telegra.ph/file/xxx.jpg\n"
            "• <b>Postimages</b> - https://i.postimg.cc/xxx.jpg\n"
            "• <b>ImgBB</b> - https://ibb.co/xxx\n"
            "• <b>Ваш собственный хостинг</b>\n\n"
            "❌ <b>Imgur НЕ РАБОТАЕТ</b> в России и многих странах\n\n"
            "Для Telegra.ph: загрузите фото в @Telegraph_bot и скопируйте ссылку",
            parse_mode='HTML'
        )
        await state.set_state(AdminStates.waiting_for_project_edit)
        
    except Exception as e:
        logger.error(f"Неизвестная ошибка в edit_project_photo_start: {e}")
        await safe_edit_message(callback.message, f"❌ Ошибка: {str(e)}")

@dp.message(AdminStates.waiting_for_project_edit)
async def edit_project_finish(message: Message, state: FSMContext):
    """Сохранение изменений проекта"""
    data = await state.get_data()
    project_id = data['project_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    logger.info(f"Редактирование проекта {project_id}, поле {edit_field}, значение: {new_value}")
    print(f"DEBUG: edit_field={edit_field}, project_id={project_id}")  # Отладка
    
    update_data = {}
    if edit_field == "title":
        update_data['title'] = new_value
    elif edit_field == "url":
        update_data['url'] = new_value
    elif edit_field == "promo":
        update_data['promo_code'] = new_value
    elif edit_field == "photo":
        # Если пользователь ввел 0, удаляем фото
        if new_value == "0":
            update_data['photo_url'] = None
            await message.answer("✅ Фото удалено")
        else:
            # Конвертируем ссылку Imgur в прямой формат
            converted_url = convert_imgur_url(new_value)
            logger.info(f"Ссылка на фото: исходная={new_value}, конвертированная={converted_url}")
            print(f"DEBUG: converted_url={converted_url}")  # Отладка
            
            # Проверяем, что ссылка не пустая
            if converted_url:
                update_data['photo_url'] = converted_url
                await message.answer(f"✅ Фото добавлено: {converted_url}")
            else:
                await message.answer("❌ Неверная ссылка на фото")
                return
    else:
        await message.answer(f"❌ Неизвестное поле: {edit_field}")
        await state.clear()
        return
    
    if update_data:
        if update_project(project_id, **update_data):
            await message.answer(f"✅ Проект обновлен!")
            await asyncio.sleep(1)
            
            # Создаем фейковый callback для возврата в меню проекта
            fake_callback = type('obj', (object,), {
                'message': message,
                'answer': lambda: None,
                'data': f"edit_project_{project_id}"
            })
            await edit_project_handler(fake_callback)
        else:
            await message.answer("❌ Ошибка при обновлении проекта")
    else:
        await message.answer("❌ Нет данных для обновления")
    
    await state.clear()

@dp.callback_query(F.data.startswith("toggle_project_"))
async def toggle_project_handler(callback: CallbackQuery):
    """Изменение статуса проекта"""
    await callback.answer()
    
    try:
        project_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")
        return
    
    project = get_project_by_id(project_id)
    
    if project:
        update_project(project_id, is_active=not project['is_active'])
        new_status = "активирован" if not project['is_active'] else "деактивирован"
        await safe_edit_message(callback.message, f"✅ Проект {new_status}!")
        await asyncio.sleep(1)
        await edit_projects_list(callback)
    else:
        await safe_edit_message(callback.message, "❌ Проект не найден!")

@dp.callback_query(F.data.startswith("delete_project_"))
async def delete_project_confirm(callback: CallbackQuery):
    """Подтверждение удаления проекта"""
    await callback.answer()
    
    try:
        project_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_project_{project_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_project_{project_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        "⚠️ Вы уверены, что хотите удалить этот проект?\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_project_"))
async def confirm_delete_project(callback: CallbackQuery):
    """Удаление проекта"""
    await callback.answer()
    
    try:
        project_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных проекта")
        return
    
    if delete_project(project_id):
        await safe_edit_message(callback.message, "✅ Проект успешно удален!")
        await asyncio.sleep(1)
        await admin_projects_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении проекта")

# ==================== УПРАВЛЕНИЕ МАГАЗИНОМ ====================

@dp.callback_query(F.data == "admin_shop")
async def admin_shop_menu(callback: CallbackQuery):
    """Управление магазином"""
    await callback.answer()
    
    items = get_all_shop_items_detailed(include_used=True)
    
    # Группируем для статистики
    from collections import defaultdict
    stats = defaultdict(lambda: {"total": 0, "sold": 0, "available": 0})
    
    for item in items:
        key = (item['name'], item['price'])
        stats[key]["total"] += 1
        if item['is_used']:
            stats[key]["sold"] += 1
        else:
            stats[key]["available"] += 1
    
    text = "🏪 УПРАВЛЕНИЕ МАГАЗИНОМ\n\n"
    
    if not items:
        text += "Нет товаров в магазине."
    else:
        text += f"📊 Общая статистика:\n"
        text += f"   • Всего экземпляров: {len(items)}\n"
        text += f"   • Уникальных товаров: {len(stats)}\n\n"
        
        text += "📋 Товары:\n"
        for (name, price), data in stats.items():
            text += f"   • {name} - {price}💰\n"
            text += f"     Всего: {data['total']}, "
            text += f"Доступно: {data['available']}, "
            text += f"Продано: {data['sold']}\n\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить товары", callback_data="add_shop_items"))
    keyboard.row(InlineKeyboardButton(text="✏️ Управление экземплярами", callback_data="edit_shop_list"))
    keyboard.row(InlineKeyboardButton(text="📊 История покупок", callback_data="shop_purchase_history"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())
@dp.callback_query(F.data == "edit_shop_list")
async def edit_shop_list(callback: CallbackQuery):
    """Список всех экземпляров товаров для редактирования"""
    await callback.answer()
    
    items = get_all_shop_items_detailed(include_used=True)
    
    if not items:
        await safe_edit_message(callback.message, "🏪 В магазине нет товаров.")
        return
    
    text = "📋 ВСЕ ЭКЗЕМПЛЯРЫ ТОВАРОВ\n\n"
    
    # Группируем по товарам для удобства
    current_item = ""
    for item in items:
        item_key = f"{item['name']} - {item['price']}💰"
        if item_key != current_item:
            current_item = item_key
            text += f"\n📦 <b>{item['name']}</b> - {item['price']}💰\n"
        
        status = "✅" if not item['is_used'] else "💰"
        buyer_info = f" (куплен ID: {item['buyer_id']})" if item['buyer_id'] else ""
        text += f"   {status} ID: {item['id']} - {item['promo_code']}{buyer_info}\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="🔍 Поиск по ID", callback_data="search_shop_item"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_shop"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup(), parse_mode='HTML')

@dp.callback_query(F.data == "search_shop_item")
async def search_shop_item_start(callback: CallbackQuery, state: FSMContext):
    """Начало поиска товара по ID"""
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "🔍 Введите ID товара для редактирования:"
    )
    await state.set_state("waiting_for_shop_item_id")

@dp.message(F.text, StateFilter("waiting_for_shop_item_id"))
async def search_shop_item_by_id(message: Message, state: FSMContext):
    """Поиск товара по ID"""
    try:
        item_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число!")
        await state.clear()
        return
    
    item = get_shop_item_by_id(item_id)
    
    if not item:
        await message.answer(f"❌ Товар с ID {item_id} не найден!")
        await state.clear()
        return
    
    # Показываем меню редактирования
    await show_shop_item_edit_menu(message, item)
    await state.clear()

async def show_shop_item_edit_menu(message: Message, item: dict):
    """Показать меню редактирования товара"""
    status = "Доступен" if not item['is_used'] else "Продан"
    buyer_info = f"\n👤 Покупатель: {item['buyer_id']}" if item['buyer_id'] else ""
    time_info = f"\n⏰ Время покупки: {item['bought_at'][:16]}" if item['bought_at'] else ""
    project_info = f"\n🏢 Проект: {item['project_title']}" if item.get('project_title') else ""
    
    text = (
        f"📦 РЕДАКТИРОВАНИЕ ТОВАРА\n\n"
        f"ID: {item['id']}\n"
        f"Название: {item['name']}\n"
        f"Цена: {item['price']}💰\n"
        f"Промокод: {item['promo_code']}\n"
        f"Статус: {status}{buyer_info}{time_info}{project_info}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardBuilder()
    
    if not item['is_used']:
        keyboard.row(InlineKeyboardButton(
            text="✏️ Изменить название", 
            callback_data=f"edit_shop_name_{item['id']}"
        ))
        keyboard.row(InlineKeyboardButton(
            text="💰 Изменить цену", 
            callback_data=f"edit_shop_price_{item['id']}"
        ))
        keyboard.row(InlineKeyboardButton(
            text="🎫 Изменить промокод", 
            callback_data=f"edit_shop_code_{item['id']}"
        ))
        if item.get('project_id'):
            keyboard.row(InlineKeyboardButton(
                text="🏢 Изменить проект", 
                callback_data=f"edit_shop_project_{item['id']}"
            ))
    
    keyboard.row(InlineKeyboardButton(
        text="🗑 Удалить", 
        callback_data=f"delete_shop_item_{item['id']}"
    ))
    keyboard.row(InlineKeyboardButton(
        text="◀️ Назад к списку", 
        callback_data="edit_shop_list"
    ))
    
    await message.answer(text, reply_markup=keyboard.as_markup())
@dp.callback_query(F.data.startswith("edit_shop_name_"))
async def edit_shop_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения названия товара"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        await state.update_data(item_id=item_id, edit_field="name")
        await safe_edit_message(callback.message, "Введите новое название товара:")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.callback_query(F.data.startswith("edit_shop_price_"))
async def edit_shop_price_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения цены товара"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        await state.update_data(item_id=item_id, edit_field="price")
        await safe_edit_message(callback.message, "Введите новую цену (только число):")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.callback_query(F.data.startswith("edit_shop_code_"))
async def edit_shop_code_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения промокода"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        await state.update_data(item_id=item_id, edit_field="code")
        await safe_edit_message(callback.message, "Введите новый промокод:")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.callback_query(F.data.startswith("edit_shop_project_"))
async def edit_shop_project_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения проекта товара"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        
        # Показываем список проектов
        projects = get_projects()
        if not projects:
            await safe_edit_message(callback.message, "❌ Нет доступных проектов!")
            return
        
        text = "Выберите проект:\n\n"
        keyboard = InlineKeyboardBuilder()
        
        for proj in projects:
            text += f"ID {proj['id']}: {proj['title']}\n"
            keyboard.row(InlineKeyboardButton(
                text=f"{proj['title']}",
                callback_data=f"set_shop_project_{item_id}_{proj['id']}"
            ))
        
        keyboard.row(InlineKeyboardButton(text="❌ Без проекта", callback_data=f"set_shop_project_{item_id}_0"))
        
        await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.callback_query(F.data.startswith("set_shop_project_"))
async def set_shop_project(callback: CallbackQuery):
    """Установка проекта для товара"""
    await callback.answer()
    try:
        parts = callback.data.split("_")
        item_id = int(parts[3])
        project_id = int(parts[4]) if parts[4] != "0" else None
        
        update_shop_item(item_id, project_id=project_id)
        
        project_name = "без проекта" if not project_id else f"ID {project_id}"
        await safe_edit_message(callback.message, f"✅ Проект изменен на {project_name}")
        
        # Показываем обновленный товар
        item = get_shop_item_by_id(item_id)
        if item:
            await show_shop_item_edit_menu(callback.message, item)
        
    except:
        await safe_edit_message(callback.message, "❌ Ошибка при изменении проекта")

@dp.message(AdminStates.waiting_for_promo_edit)
async def edit_shop_item_finish(message: Message, state: FSMContext):
    """Сохранение изменений товара"""
    data = await state.get_data()
    item_id = data['item_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    update_data = {}
    if edit_field == "name":
        update_data['name'] = new_value
    elif edit_field == "price":
        try:
            update_data['price'] = int(new_value)
        except:
            await message.answer("❌ Цена должна быть числом!")
            return
    elif edit_field == "code":
        update_data['promo_code'] = new_value
    
    if update_shop_item(item_id, **update_data):
        await message.answer(f"✅ Товар обновлен!")
        
        # Показываем обновленный товар
        item = get_shop_item_by_id(item_id)
        if item:
            await show_shop_item_edit_menu(message, item)
    else:
        await message.answer("❌ Ошибка при обновлении товара")
    
    await state.clear()
    
@dp.callback_query(F.data == "add_shop_items")
async def add_shop_items_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления нескольких товаров"""
    await callback.answer()
    
    projects = get_projects()
    projects_text = "\n".join([f"{p['id']}. {p['title']}" for p in projects])
    
    await safe_edit_message(
        callback.message,
        f"Введите данные товаров в формате:\n"
        f"Название | Цена | ID проекта (необязательно)\n"
        f"Затем на новой строке список промокодов (каждый с новой строки)\n\n"
        f"Доступные проекты:\n{projects_text if projects else 'Нет проектов'}\n\n"
        f"Пример:\n"
        f"Промокод Starda на 500₽ | 1000 | 1\n"
        f"STARDA500\n"
        f"STARDA501\n"
        f"STARDA502"
    )
    await state.set_state(AdminStates.waiting_for_promo_data)
@dp.callback_query(F.data.startswith("delete_shop_item_"))
async def delete_shop_item_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления товара"""
    await callback.answer()
    
    try:
        item_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")
        return
    
    item = get_shop_item_by_id(item_id)
    if not item:
        await safe_edit_message(callback.message, "❌ Товар не найден!")
        return
    
    warning = ""
    if item['is_used']:
        warning = f"\n\n⚠️ Товар был куплен пользователем {item['buyer_id']}!"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_shop_{item_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_shop_item_{item_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        f"⚠️ Вы уверены, что хотите удалить этот товар?{warning}\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_shop_"))
async def confirm_delete_shop_item(callback: CallbackQuery):
    """Подтвержденное удаление товара"""
    await callback.answer()
    
    try:
        item_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")
        return
    
    if delete_shop_item(item_id):
        await safe_edit_message(callback.message, "✅ Товар удален из магазина!")
        await asyncio.sleep(1)
        await admin_shop_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении товара")

@dp.callback_query(F.data.startswith("edit_shop_item_"))
async def edit_shop_item_by_id(callback: CallbackQuery):
    """Редактирование товара по прямому ID из списка"""
    await callback.answer()
    
    try:
        item_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")
        return
    
    item = get_shop_item_by_id(item_id)
    
    if not item:
        await safe_edit_message(callback.message, "❌ Товар не найден!")
        return
    
    await show_shop_item_edit_menu(callback.message, item)
    
@dp.message(AdminStates.waiting_for_promo_data)
async def add_shop_items_finish(message: Message, state: FSMContext):
    """Сохранение нескольких товаров"""
    try:
        lines = message.text.strip().split('\n')
        if len(lines) < 2:
            await message.answer("❌ Ошибка! Нужно указать параметры товара и хотя бы один промокод")
            return
        
        # Первая строка: Название | Цена | ID проекта
        first_line = lines[0].strip()
        parts = [x.strip() for x in first_line.split(" | ")]
        
        if len(parts) < 2:
            await message.answer("❌ Ошибка! Первая строка должна содержать: Название | Цена | ID проекта (необязательно)")
            return
        
        name = parts[0]
        price = int(parts[1])
        project_id = int(parts[2]) if len(parts) > 2 and parts[2] != '0' else None
        
        # Остальные строки - промокоды
        promo_codes = [line.strip() for line in lines[1:] if line.strip()]
        
        if not promo_codes:
            await message.answer("❌ Ошибка! Нужно указать хотя бы один промокод")
            return
        
        added = add_shop_items(name, price, promo_codes, project_id)
        
        await message.answer(
            f"✅ Успешно добавлено!\n\n"
            f"📦 Товар: {name}\n"
            f"💰 Цена: {price} баллов\n"
            f"📊 Добавлено экземпляров: {added}"
        )
        
    except ValueError as e:
        await message.answer(f"❌ Ошибка в формате числа: {e}")
    except Exception as e:
        await message.answer(f"❌ Ошибка! {str(e)}")
    
    await state.clear()

@dp.callback_query(F.data == "shop_purchase_history")
async def shop_purchase_history(callback: CallbackQuery):
    """История покупок"""
    await callback.answer()
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT s.*, u.username, u.first_name 
            FROM shop_promocodes s
            LEFT JOIN users u ON s.buyer_id = u.user_id
            WHERE s.is_used = 1
            ORDER BY s.bought_at DESC
            LIMIT 50
        """)
        purchases = [dict(row) for row in c.fetchall()]
    
    text = "💰 ИСТОРИЯ ПОКУПОК\n\n"
    
    if not purchases:
        text += "Пока нет совершенных покупок."
    else:
        # Группируем по датам для удобства
        current_date = ""
        for p in purchases:
            date_str = p['bought_at'][:10] if p['bought_at'] else "Неизвестно"
            if date_str != current_date:
                current_date = date_str
                text += f"\n📅 {current_date}\n"
            
            buyer_display = get_user_display(p['buyer_id'], p['username'], p['first_name'])
            text += f"   • {p['name']} - {p['price']}💰\n"
            text += f"     Покупатель: {buyer_display}\n"
            text += f"     Время: {p['bought_at'][11:16] if p['bought_at'] else '--:--'}\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_shop")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("edit_shop_item_"))
async def edit_shop_item_menu(callback: CallbackQuery, state: FSMContext):
    """Меню редактирования товара"""
    await callback.answer()
    
    try:
        item_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")
        return
    
    item = get_shop_item_by_id(item_id)
    
    if not item:
        await safe_edit_message(callback.message, "❌ Товар не найден!")
        return
    
    status = "Доступен" if not item['is_used'] else "Продан"
    buyer_info = f"\nПокупатель: {item['buyer_id']}\nВремя покупки: {item['bought_at'][:16] if item['bought_at'] else 'Неизвестно'}" if item['buyer_id'] else ""
    project_info = f"\nПроект: {item['project_title']}" if item.get('project_title') else ""
    
    text = (
        f"🏪 РЕДАКТИРОВАНИЕ ТОВАРА\n\n"
        f"ID: {item['id']}\n"
        f"Название: {item['name']}\n"
        f"Цена: {item['price']}💰\n"
        f"Промокод: {item['promo_code']}{project_info}\n"
        f"Статус: {status}{buyer_info}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_shop_name_{item_id}"))
    keyboard.row(InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"edit_shop_price_{item_id}"))
    keyboard.row(InlineKeyboardButton(text="🎫 Изменить промокод", callback_data=f"edit_shop_code_{item_id}"))
    
    if not item['is_used']:
        keyboard.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_shop_item_{item_id}"))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="edit_shop_list"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("edit_shop_name_"))
async def edit_shop_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения названия товара"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        await state.update_data(item_id=item_id, edit_field="name")
        await safe_edit_message(callback.message, "Введите новое название товара:")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.callback_query(F.data.startswith("edit_shop_price_"))
async def edit_shop_price_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения цены товара"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        await state.update_data(item_id=item_id, edit_field="price")
        await safe_edit_message(callback.message, "Введите новую цену (только число):")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.callback_query(F.data.startswith("edit_shop_code_"))
async def edit_shop_code_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения промокода"""
    await callback.answer()
    try:
        item_id = int(callback.data.split("_")[3])
        await state.update_data(item_id=item_id, edit_field="code")
        await safe_edit_message(callback.message, "Введите новый промокод:")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")

@dp.message(AdminStates.waiting_for_promo_edit)
async def edit_shop_item_finish(message: Message, state: FSMContext):
    """Сохранение изменений товара"""
    data = await state.get_data()
    item_id = data['item_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    update_data = {}
    if edit_field == "name":
        update_data['name'] = new_value
    elif edit_field == "price":
        try:
            update_data['price'] = int(new_value)
        except:
            await message.answer("❌ Цена должна быть числом!")
            return
    elif edit_field == "code":
        update_data['promo_code'] = new_value
    
    if update_shop_item(item_id, **update_data):
        await message.answer(f"✅ Товар обновлен!")
        await asyncio.sleep(1)
        fake_callback = type('obj', (object,), {
            'message': message,
            'answer': lambda: None,
            'data': f"edit_shop_item_{item_id}"
        })
        await edit_shop_item_menu(fake_callback, None)
    else:
        await message.answer("❌ Ошибка при обновлении товара")
    
    await state.clear()

@dp.callback_query(F.data.startswith("delete_shop_item_"))
async def delete_shop_item_confirm(callback: CallbackQuery):
    """Подтверждение удаления товара"""
    await callback.answer()
    
    try:
        item_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_shop_{item_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_shop_item_{item_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        "⚠️ Вы уверены, что хотите удалить этот товар?\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_shop_"))
async def confirm_delete_shop_item(callback: CallbackQuery):
    """Удаление товара"""
    await callback.answer()
    
    try:
        item_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных товара")
        return
    
    if delete_shop_item(item_id):
        await safe_edit_message(callback.message, "✅ Товар удален из магазина!")
        await asyncio.sleep(1)
        await admin_shop_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении товара")

# ==================== УПРАВЛЕНИЕ КОЛЕСОМ ФОРТУНЫ ====================

@dp.callback_query(F.data == "admin_wheel")
async def admin_wheel_menu(callback: CallbackQuery):
    """Управление колесом фортуны"""
    await callback.answer()
    
    prizes = get_wheel_prizes()
    
    text = "🎡 УПРАВЛЕНИЕ КОЛЕСОМ ФОРТУНЫ\n\n"
    text += "ТЕКУЩИЕ ПРИЗЫ:\n"
    
    total_prob = 0
    for prize in prizes:
        text += f"• {prize['name']} - {prize['probability']}%"
        if prize['type'] == 'points':
            text += f" (+{prize['value']}💰)\n"
        elif prize['type'] == 'spins':
            text += f" (+{prize['value']}🎡)\n"
        elif prize['type'] == 'jackpot':
            text += f" (🎰)\n"
        else:
            text += f"\n"
        total_prob += prize['probability']
    
    text += f"\n📊 Сумма вероятностей: {total_prob}% (должно быть 100%)\n"
    
    if total_prob != 100:
        text += "⚠️ ВНИМАНИЕ: Сумма вероятностей не равна 100%!\n\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить приз", callback_data="add_wheel_prize"))
    keyboard.row(InlineKeyboardButton(text="✏️ Редактировать призы", callback_data="edit_wheel_prizes"))
    keyboard.row(InlineKeyboardButton(text="⚖️ Выровнять вероятности", callback_data="balance_wheel_probs"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "edit_wheel_prizes")
async def edit_wheel_prizes(callback: CallbackQuery):
    """Список призов для редактирования"""
    await callback.answer()
    
    prizes = get_wheel_prizes()
    
    text = "🎡 ВЫБЕРИТЕ ПРИЗ ДЛЯ РЕДАКТИРОВАНИЯ\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for prize in prizes:
        keyboard.row(InlineKeyboardButton(
            text=f"{prize['name']} ({prize['probability']}%)",
            callback_data=f"edit_wheel_prize_{prize['id']}"
        ))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_wheel"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "add_wheel_prize")
async def add_wheel_prize_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления приза"""
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "Введите данные приза в формате:\n"
        "Название | Тип | Значение | Вероятность\n\n"
        "Типы: points (баллы), spins (попытки), jackpot (джекпот), empty (пусто)\n\n"
        "Примеры:\n"
        "50 баллов | points | 50 | 25\n"
        "+1 попытка | spins | 1 | 15\n"
        "ДЖЕКПОТ | jackpot | 0 | 5\n"
        "Пусто | empty | 0 | 25"
    )
    await state.set_state(AdminStates.waiting_for_wheel_prize_add)

@dp.message(AdminStates.waiting_for_wheel_prize_add)
async def add_wheel_prize_finish(message: Message, state: FSMContext):
    """Сохранение приза"""
    try:
        parts = [x.strip() for x in message.text.split(" | ")]
        
        if len(parts) != 4:
            await message.answer("❌ Ошибка! Нужно 4 части: Название | Тип | Значение | Вероятность")
            return
        
        name, prize_type, value_str, prob_str = parts
        value = int(value_str)
        probability = int(prob_str)
        
        if prize_type not in ['points', 'spins', 'jackpot', 'empty']:
            await message.answer("❌ Неверный тип! Допустимые: points, spins, jackpot, empty")
            return
        
        if probability < 0 or probability > 100:
            await message.answer("❌ Вероятность должна быть от 0 до 100")
            return
        
        prize_id = add_wheel_prize(name, prize_type, value, probability)
        
        if prize_id:
            await message.answer(f"✅ Приз успешно добавлен! ID: {prize_id}")
        else:
            await message.answer("❌ Ошибка при добавлении приза")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка! {str(e)}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("edit_wheel_prize_"))
async def edit_wheel_prize_menu(callback: CallbackQuery, state: FSMContext):
    """Меню редактирования приза"""
    await callback.answer()
    
    try:
        prize_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")
        return
    
    prize = get_wheel_prize_by_id(prize_id)
    
    if not prize:
        await safe_edit_message(callback.message, "❌ Приз не найден!")
        return
    
    type_names = {
        'points': 'Баллы',
        'spins': 'Попытки',
        'jackpot': 'Джекпот',
        'empty': 'Пусто'
    }
    
    text = (
        f"🎡 РЕДАКТИРОВАНИЕ ПРИЗА\n\n"
        f"ID: {prize['id']}\n"
        f"Название: {prize['name']}\n"
        f"Тип: {type_names.get(prize['type'], prize['type'])}\n"
        f"Значение: {prize['value']}\n"
        f"Вероятность: {prize['probability']}%\n"
        f"Статус: {'Активен' if prize['is_active'] else 'Неактивен'}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_prize_name_{prize_id}"))
    keyboard.row(InlineKeyboardButton(text="🔄 Изменить тип", callback_data=f"edit_prize_type_{prize_id}"))
    keyboard.row(InlineKeyboardButton(text="🔢 Изменить значение", callback_data=f"edit_prize_value_{prize_id}"))
    keyboard.row(InlineKeyboardButton(text="📊 Изменить вероятность", callback_data=f"edit_prize_prob_{prize_id}"))
    keyboard.row(InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"toggle_prize_{prize_id}"))
    keyboard.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_prize_{prize_id}"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="edit_wheel_prizes"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "balance_wheel_probs")
async def balance_wheel_probabilities(callback: CallbackQuery):
    """Выравнивание вероятностей колеса"""
    await callback.answer()
    
    prizes = get_wheel_prizes()
    
    if not prizes:
        await safe_edit_message(callback.message, "❌ Нет призов для настройки!")
        return
    
    prob = 100 // len(prizes)
    remainder = 100 - prob * len(prizes)
    
    for i, prize in enumerate(prizes):
        new_prob = prob + (1 if i < remainder else 0)
        update_wheel_prize(prize['id'], probability=new_prob)
    
    await safe_edit_message(callback.message, "✅ Вероятности выровнены!")
    await asyncio.sleep(1)
    await admin_wheel_menu(callback)

@dp.callback_query(F.data.startswith("edit_prize_name_"))
async def edit_prize_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения названия приза"""
    await callback.answer()
    try:
        prize_id = int(callback.data.split("_")[3])
        await state.update_data(prize_id=prize_id, edit_field="name")
        await safe_edit_message(callback.message, "Введите новое название приза:")
        await state.set_state(AdminStates.waiting_for_wheel_prize_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")

@dp.callback_query(F.data.startswith("edit_prize_type_"))
async def edit_prize_type_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения типа приза"""
    await callback.answer()
    try:
        prize_id = int(callback.data.split("_")[3])
        await state.update_data(prize_id=prize_id, edit_field="type")
        await safe_edit_message(
            callback.message,
            "Введите новый тип приза:\n"
            "Допустимые: points, spins, jackpot, empty"
        )
        await state.set_state(AdminStates.waiting_for_wheel_prize_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")

@dp.callback_query(F.data.startswith("edit_prize_value_"))
async def edit_prize_value_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения значения приза"""
    await callback.answer()
    try:
        prize_id = int(callback.data.split("_")[3])
        await state.update_data(prize_id=prize_id, edit_field="value")
        await safe_edit_message(callback.message, "Введите новое значение приза (число):")
        await state.set_state(AdminStates.waiting_for_wheel_prize_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")

@dp.callback_query(F.data.startswith("edit_prize_prob_"))
async def edit_prize_prob_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения вероятности"""
    await callback.answer()
    try:
        prize_id = int(callback.data.split("_")[3])
        await state.update_data(prize_id=prize_id, edit_field="probability")
        await safe_edit_message(callback.message, "Введите новую вероятность (число от 0 до 100):")
        await state.set_state(AdminStates.waiting_for_wheel_prize_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")

@dp.message(AdminStates.waiting_for_wheel_prize_edit)
async def edit_wheel_prize_finish(message: Message, state: FSMContext):
    """Сохранение изменений приза"""
    data = await state.get_data()
    prize_id = data['prize_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    update_data = {}
    
    if edit_field == "name":
        update_data['name'] = new_value
    elif edit_field == "type":
        if new_value not in ['points', 'spins', 'jackpot', 'empty']:
            await message.answer("❌ Неверный тип! Допустимые: points, spins, jackpot, empty")
            return
        update_data['type'] = new_value
    elif edit_field in ["value", "probability"]:
        try:
            num_value = int(new_value)
            if edit_field == "probability" and (num_value < 0 or num_value > 100):
                await message.answer("❌ Вероятность должна быть от 0 до 100")
                return
            update_data[edit_field] = num_value
        except:
            await message.answer("❌ Введите число!")
            return
    
    if update_wheel_prize(prize_id, **update_data):
        await message.answer(f"✅ Приз обновлен!")
        await asyncio.sleep(1)
        fake_callback = type('obj', (object,), {
            'message': message,
            'answer': lambda: None,
            'data': f"edit_wheel_prize_{prize_id}"
        })
        await edit_wheel_prize_menu(fake_callback, None)
    else:
        await message.answer("❌ Ошибка при обновлении приза")
    
    await state.clear()

@dp.callback_query(F.data.startswith("toggle_prize_"))
async def toggle_prize_handler(callback: CallbackQuery):
    """Изменение статуса приза"""
    await callback.answer()
    
    try:
        prize_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")
        return
    
    prize = get_wheel_prize_by_id(prize_id)
    
    if prize:
        update_wheel_prize(prize_id, is_active=not prize['is_active'])
        new_status = "активирован" if not prize['is_active'] else "деактивирован"
        await safe_edit_message(callback.message, f"✅ Приз {new_status}!")
        await asyncio.sleep(1)
        await edit_wheel_prizes(callback)
    else:
        await safe_edit_message(callback.message, "❌ Приз не найден!")

@dp.callback_query(F.data.startswith("delete_prize_"))
async def delete_prize_confirm(callback: CallbackQuery):
    """Подтверждение удаления приза"""
    await callback.answer()
    
    try:
        prize_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_prize_{prize_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_wheel_prize_{prize_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        "⚠️ Вы уверены, что хотите удалить этот приз?\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_prize_"))
async def confirm_delete_prize(callback: CallbackQuery):
    """Удаление приза"""
    await callback.answer()
    
    try:
        prize_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных приза")
        return
    
    if delete_wheel_prize(prize_id):
        await safe_edit_message(callback.message, "✅ Приз удален!")
        await asyncio.sleep(1)
        await admin_wheel_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении приза")

# ==================== УПРАВЛЕНИЕ ЗАДАНИЯМИ ====================

@dp.callback_query(F.data == "admin_tasks")
async def admin_tasks_menu(callback: CallbackQuery):
    """Управление заданиями"""
    await callback.answer()
    
    tasks = get_tasks(include_inactive=True)
    
    text = "📋 УПРАВЛЕНИЕ ЗАДАНИЯМИ\n\n"
    
    if not tasks:
        text += "Нет добавленных заданий."
    else:
        active = sum(1 for t in tasks if t['is_active'])
        text += f"📊 Всего заданий: {len(tasks)} (активных: {active})\n\n"
        
        for task in tasks[:5]:
            status = "✅" if task['is_active'] else "❌"
            text += f"{status} {task['task_name']}\n"
            text += f"   Награда: +{task['reward_spins']}🎡 +{task['reward_points']}💰\n"
            text += f"   Тип: {task['task_type']}\n\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить задание", callback_data="add_task"))
    keyboard.row(InlineKeyboardButton(text="✏️ Редактировать задания", callback_data="edit_tasks_list"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "edit_tasks_list")
async def edit_tasks_list(callback: CallbackQuery):
    """Список заданий для редактирования"""
    await callback.answer()
    
    tasks = get_tasks(include_inactive=True)
    
    text = "📋 ВЫБЕРИТЕ ЗАДАНИЕ ДЛЯ РЕДАКТИРОВАНИЯ\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for task in tasks:
        status = "✅" if task['is_active'] else "❌"
        keyboard.row(InlineKeyboardButton(
            text=f"{status} {task['task_name']}",
            callback_data=f"edit_task_{task['id']}"
        ))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_tasks"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "add_task")
async def add_task_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления задания"""
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "Введите данные задания в формате:\n"
        "Название | Описание | Тип | Данные | Награда(попытки) | Награда(баллы)\n\n"
        "Типы: channel (канал), chat (чат), website (сайт)\n\n"
        "Примеры:\n"
        "Подписаться на канал | Подпишитесь на наш канал | channel | @channel_name | 1 | 50\n"
        "Вступить в чат | Присоединитесь к чату | chat | @chat_name | 2 | 100\n"
        "Посетить сайт | Перейдите на сайт | website | https://example.com | 1 | 0"
    )
    await state.set_state(AdminStates.waiting_for_task_add)

@dp.message(AdminStates.waiting_for_task_add)
async def add_task_finish(message: Message, state: FSMContext):
    """Сохранение задания"""
    try:
        parts = [x.strip() for x in message.text.split(" | ")]
        
        if len(parts) != 6:
            await message.answer("❌ Ошибка! Нужно 6 частей: Название | Описание | Тип | Данные | Награда(попытки) | Награда(баллы)")
            return
        
        name, description, task_type, task_data, spins_str, points_str = parts
        
        if task_type not in ['channel', 'chat', 'website']:
            await message.answer("❌ Неверный тип! Допустимые: channel, chat, website")
            return
        
        reward_spins = int(spins_str)
        reward_points = int(points_str)
        
        task_id = add_task(name, description, reward_spins, reward_points, task_type, task_data)
        
        if task_id:
            await message.answer(f"✅ Задание успешно добавлено! ID: {task_id}")
        else:
            await message.answer("❌ Ошибка при добавлении задания")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка! {str(e)}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("edit_task_") & ~F.data.startswith("edit_task_name_") & ~F.data.startswith("edit_task_desc_") & ~F.data.startswith("edit_task_reward_"))
async def edit_task_menu(callback: CallbackQuery):
    """Меню редактирования задания"""
    await callback.answer()
    
    try:
        task_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")
        return
    
    task = get_task_by_id(task_id)
    
    if not task:
        await safe_edit_message(callback.message, "❌ Задание не найдено!")
        return
    
    type_names = {
        'channel': 'Канал',
        'chat': 'Чат',
        'website': 'Сайт'
    }
    
    text = (
        f"📋 РЕДАКТИРОВАНИЕ ЗАДАНИЯ\n\n"
        f"ID: {task['id']}\n"
        f"Название: {task['task_name']}\n"
        f"Описание: {task['task_description']}\n"
        f"Тип: {type_names.get(task['task_type'], task['task_type'])}\n"
        f"Данные: {task['task_data']}\n"
        f"Награда: +{task['reward_spins']}🎡 +{task['reward_points']}💰\n"
        f"Статус: {'Активно' if task['is_active'] else 'Неактивно'}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_task_name_{task_id}")],
        [InlineKeyboardButton(text="📝 Изменить описание", callback_data=f"edit_task_desc_{task_id}")],
        [InlineKeyboardButton(text="🎁 Изменить награду", callback_data=f"edit_task_reward_{task_id}")],
        [InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"toggle_task_status_{task_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_task_{task_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="edit_tasks_list")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("edit_task_name_"))
async def edit_task_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения названия задания"""
    await callback.answer()
    try:
        task_id = int(callback.data.split("_")[3])
        await state.update_data(task_id=task_id, edit_field="task_name")
        await safe_edit_message(callback.message, "Введите новое название задания:")
        await state.set_state(AdminStates.waiting_for_task_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")

@dp.callback_query(F.data.startswith("edit_task_desc_"))
async def edit_task_desc_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения описания задания"""
    await callback.answer()
    try:
        task_id = int(callback.data.split("_")[3])
        await state.update_data(task_id=task_id, edit_field="task_description")
        await safe_edit_message(callback.message, "Введите новое описание задания:")
        await state.set_state(AdminStates.waiting_for_task_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")

@dp.callback_query(F.data.startswith("edit_task_reward_"))
async def edit_task_reward_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения награды задания"""
    await callback.answer()
    try:
        task_id = int(callback.data.split("_")[3])
        await state.update_data(task_id=task_id)
        await safe_edit_message(
            callback.message,
            "Введите новую награду в формате:\n"
            "попытки | баллы\n\n"
            "Например: 2 | 100"
        )
        await state.set_state(AdminStates.waiting_for_task_reward)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")

@dp.message(AdminStates.waiting_for_task_edit)
async def save_task_simple_edit(message: Message, state: FSMContext):
    """Сохранение простых изменений задания"""
    data = await state.get_data()
    task_id = data['task_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    if update_task(task_id, **{edit_field: new_value}):
        await message.answer(f"✅ Задание обновлено!")
        await asyncio.sleep(1)
        fake_callback = type('obj', (object,), {
            'message': message,
            'answer': lambda: None,
            'data': f"edit_task_{task_id}"
        })
        await edit_task_menu(fake_callback)
    else:
        await message.answer("❌ Ошибка при обновлении задания")
    
    await state.clear()

@dp.message(AdminStates.waiting_for_task_reward)
async def save_task_reward_edit(message: Message, state: FSMContext):
    """Сохранение награды задания"""
    try:
        parts = [x.strip() for x in message.text.split(" | ")]
        
        if len(parts) != 2:
            await message.answer("❌ Ошибка! Нужно 2 части: попытки | баллы")
            return
        
        spins_str, points_str = parts
        reward_spins = int(spins_str)
        reward_points = int(points_str)
        
        data = await state.get_data()
        task_id = data['task_id']
        
        if update_task(task_id, reward_spins=reward_spins, reward_points=reward_points):
            await message.answer(f"✅ Награда обновлена!")
            await asyncio.sleep(1)
            fake_callback = type('obj', (object,), {
                'message': message,
                'answer': lambda: None,
                'data': f"edit_task_{task_id}"
            })
            await edit_task_menu(fake_callback)
        else:
            await message.answer("❌ Ошибка при обновлении награды")
        
    except ValueError:
        await message.answer("❌ Награды должны быть числами!")
    except Exception as e:
        await message.answer(f"❌ Ошибка! {str(e)}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("toggle_task_status_"))
async def toggle_task_status_handler(callback: CallbackQuery):
    """Изменение статуса задания"""
    await callback.answer()
    
    try:
        task_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")
        return
    
    task = get_task_by_id(task_id)
    
    if task:
        update_task(task_id, is_active=not task['is_active'])
        new_status = "активировано" if not task['is_active'] else "деактивировано"
        await safe_edit_message(callback.message, f"✅ Задание {new_status}!")
        await asyncio.sleep(1)
        await edit_tasks_list(callback)
    else:
        await safe_edit_message(callback.message, "❌ Задание не найдено!")

@dp.callback_query(F.data.startswith("delete_task_"))
async def delete_task_confirm(callback: CallbackQuery):
    """Подтверждение удаления задания"""
    await callback.answer()
    
    try:
        task_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_task_{task_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_task_{task_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        "⚠️ Вы уверены, что хотите удалить это задание?\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_task_"))
async def confirm_delete_task(callback: CallbackQuery):
    """Удаление задания"""
    await callback.answer()
    
    try:
        task_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных задания")
        return
    
    if delete_task(task_id):
        await safe_edit_message(callback.message, "✅ Задание успешно удалено!")
        await asyncio.sleep(1)
        await admin_tasks_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении задания")

# ==================== УПРАВЛЕНИЕ ДЖЕКПОТОМ ====================

@dp.callback_query(F.data == "admin_jackpot")
async def admin_jackpot_menu(callback: CallbackQuery):
    """Управление джекпотом"""
    await callback.answer()
    
    promos = get_jackpot_promocodes(include_used=True)
    
    available = sum(1 for p in promos if not p['is_used'])
    used = sum(1 for p in promos if p['is_used'])
    
    text = "🎰 УПРАВЛЕНИЕ ДЖЕКПОТОМ\n\n"
    text += f"📊 Статистика:\n"
    text += f"   • Всего промокодов: {len(promos)}\n"
    text += f"   • Доступно: {available}\n"
    text += f"   • Использовано: {used}\n\n"
    
    if promos:
        text += "📋 Последние промокоды:\n"
        for promo in promos[:5]:
            status = "✅" if not promo['is_used'] else "💰"
            winner = f" (выиграл ID: {promo['winner_id']})" if promo['winner_id'] else ""
            project_info = f" [Проект: {promo['project_title']}]" if promo.get('project_title') else ""
            text += f"   {status} {promo['promo_code']}{project_info}{winner}\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить промокоды", callback_data="add_jackpot_promo"))
    keyboard.row(InlineKeyboardButton(text="✏️ Редактировать промокоды", callback_data="edit_jackpot_list"))
    keyboard.row(InlineKeyboardButton(text="📊 История выигрышей", callback_data="jackpot_history"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "edit_jackpot_list")
async def edit_jackpot_list(callback: CallbackQuery):
    """Список промокодов для редактирования"""
    await callback.answer()
    
    promos = get_jackpot_promocodes(include_used=True)
    
    text = "🎰 ВЫБЕРИТЕ ПРОМОКОД ДЛЯ РЕДАКТИРОВАНИЯ\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for promo in promos:
        status = "✅" if not promo['is_used'] else "💰"
        winner = f" (выиграл ID: {promo['winner_id']})" if promo['winner_id'] else ""
        project_info = f" [{promo['project_title']}]" if promo.get('project_title') else ""
        keyboard.row(InlineKeyboardButton(
            text=f"{status} {promo['promo_code']}{project_info}{winner}",
            callback_data=f"edit_jackpot_{promo['id']}"
        ))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_jackpot"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "add_jackpot_promo")
async def add_jackpot_promo_start(callback: CallbackQuery, state: FSMContext):
    """Добавление промокодов для джекпота"""
    await callback.answer()
    
    projects = get_projects()
    projects_text = "\n".join([f"{p['id']}. {p['title']}" for p in projects])
    
    await safe_edit_message(
        callback.message,
        f"Введите промокоды для джекпота (каждый с новой строки):\n\n"
        f"Формат: промокод | ID проекта (необязательно)\n"
        f"Доступные проекты:\n{projects_text if projects else 'Нет проектов'}\n\n"
        f"Примеры:\n"
        f"JACKPOT100 | 1\n"
        f"JACKPOT200\n"
        f"BONUS777 | 2"
    )
    await state.set_state(AdminStates.waiting_for_jackpot_promo)

@dp.message(AdminStates.waiting_for_jackpot_promo)
async def add_jackpot_promo_finish(message: Message, state: FSMContext):
    """Сохранение промокодов для джекпота"""
    try:
        lines = [line.strip() for line in message.text.split('\n') if line.strip()]
        
        added = 0
        for line in lines:
            parts = line.split('|')
            promo_code = parts[0].strip()
            project_id = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else None
            
            promo_id = add_jackpot_promocode(promo_code, project_id)
            if promo_id:
                added += 1
        
        await message.answer(f"✅ Добавлено {added} промокодов для джекпота!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка! {str(e)}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("edit_jackpot_"))
async def edit_jackpot_menu(callback: CallbackQuery, state: FSMContext):
    """Меню редактирования промокода джекпота"""
    await callback.answer()
    
    try:
        promo_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")
        return
    
    promo = get_jackpot_promocode_by_id(promo_id)
    
    if not promo:
        await safe_edit_message(callback.message, "❌ Промокод не найден!")
        return
    
    status = "Не использован" if not promo['is_used'] else "Использован"
    winner_info = f"\nПобедитель: {promo['winner_id']}\nВремя выигрыша: {promo['won_at'][:16] if promo['won_at'] else 'Неизвестно'}" if promo['winner_id'] else ""
    project_info = f"\nПроект: {promo['project_title']}" if promo.get('project_title') else ""
    
    text = (
        f"🎰 РЕДАКТИРОВАНИЕ ПРОМОКОДА\n\n"
        f"ID: {promo['id']}\n"
        f"Промокод: {promo['promo_code']}{project_info}\n"
        f"Статус: {status}{winner_info}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="✏️ Изменить промокод", callback_data=f"edit_jackpot_code_{promo_id}"))
    
    if not promo['is_used']:
        keyboard.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_jackpot_{promo_id}"))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="edit_jackpot_list"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("edit_jackpot_code_"))
async def edit_jackpot_code_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения промокода"""
    await callback.answer()
    try:
        promo_id = int(callback.data.split("_")[3])
        await state.update_data(promo_id=promo_id)
        await safe_edit_message(callback.message, "Введите новый промокод:")
        await state.set_state(AdminStates.waiting_for_promo_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")

@dp.message(AdminStates.waiting_for_promo_edit)
async def edit_jackpot_code_finish(message: Message, state: FSMContext):
    """Сохранение изменений промокода"""
    data = await state.get_data()
    promo_id = data['promo_id']
    new_code = message.text.strip()
    
    if update_jackpot_promocode(promo_id, promo_code=new_code):
        await message.answer(f"✅ Промокод обновлен!")
        await asyncio.sleep(1)
        fake_callback = type('obj', (object,), {
            'message': message,
            'answer': lambda: None,
            'data': f"edit_jackpot_{promo_id}"
        })
        await edit_jackpot_menu(fake_callback, None)
    else:
        await message.answer("❌ Ошибка при обновлении промокода")
    
    await state.clear()

@dp.callback_query(F.data.startswith("delete_jackpot_"))
async def delete_jackpot_confirm(callback: CallbackQuery):
    """Подтверждение удаления промокода"""
    await callback.answer()
    
    try:
        promo_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_jackpot_{promo_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_jackpot_{promo_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        "⚠️ Вы уверены, что хотите удалить этот промокод?\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_jackpot_"))
async def confirm_delete_jackpot(callback: CallbackQuery):
    """Удаление промокода"""
    await callback.answer()
    
    try:
        promo_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")
        return
    
    if delete_jackpot_promocode(promo_id):
        await safe_edit_message(callback.message, "✅ Промокод удален!")
        await asyncio.sleep(1)
        await admin_jackpot_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении промокода")

@dp.callback_query(F.data == "jackpot_history")
async def jackpot_history(callback: CallbackQuery):
    """История выигрышей джекпота"""
    await callback.answer()
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT j.*, u.username, u.first_name 
            FROM jackpot_promocodes j
            LEFT JOIN users u ON j.winner_id = u.user_id
            WHERE j.is_used = 1
            ORDER BY j.won_at DESC
            LIMIT 20
        """)
        wins = [dict(row) for row in c.fetchall()]
    
    text = "🎰 ИСТОРИЯ ДЖЕКПОТОВ\n\n"
    
    if not wins:
        text += "Пока нет выигрышей джекпота."
    else:
        for win in wins:
            winner_display = get_user_display(win['winner_id'], win['username'], win['first_name'])
            text += f"• Промокод: {win['promo_code']}\n"
            text += f"  Победитель: {winner_display}\n"
            text += f"  Время: {win['won_at'][:16] if win['won_at'] else 'Неизвестно'}\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_jackpot")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)
# ==================== УПРАВЛЕНИЕ ПРОМОКОДАМИ ====================

@dp.callback_query(F.data == "admin_promocodes")
async def admin_promocodes_menu(callback: CallbackQuery):
    """Меню управления промокодами"""
    await callback.answer()
    
    promos = get_all_promocodes(include_inactive=True)
    
    text = "🎫 УПРАВЛЕНИЕ ПРОМОКОДАМИ\n\n"
    
    if not promos:
        text += "Нет созданных промокодов."
    else:
        active = sum(1 for p in promos if p['is_active'])
        total_uses = sum(p['used_count'] for p in promos)
        text += f"📊 Статистика:\n"
        text += f"   • Всего промокодов: {len(promos)}\n"
        text += f"   • Активных: {active}\n"
        text += f"   • Всего активаций: {total_uses}\n\n"
        
        text += "📋 Последние промокоды:\n"
        for promo in promos[:5]:
            status = "✅" if promo['is_active'] else "❌"
            expires = f" (до {promo['expires_at']})" if promo['expires_at'] else ""
            text += f"   {status} {promo['code']} - {promo['name']}\n"
            text += f"      {promo['points']}💰, использовано {promo['used_count']}/{promo['max_uses']}{expires}\n\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="add_promo"))
    keyboard.row(InlineKeyboardButton(text="✏️ Редактировать промокоды", callback_data="edit_promos_list"))
    keyboard.row(InlineKeyboardButton(text="📊 История активаций", callback_data="promo_history"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "add_promo")
async def add_promo_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания промокода"""
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "Введите название промокода (например: Приветственный бонус):"
    )
    await state.set_state(AdminStates.waiting_for_promo_code_name)

@dp.message(AdminStates.waiting_for_promo_code_name)
async def add_promo_name(message: Message, state: FSMContext):
    """Получение названия промокода"""
    await state.update_data(promo_name=message.text)
    await message.answer("Введите код промокода (например: WELCOME100):")
    await state.set_state(AdminStates.waiting_for_promo_code_value)

@dp.message(AdminStates.waiting_for_promo_code_value)
async def add_promo_code(message: Message, state: FSMContext):
    """Получение кода промокода"""
    await state.update_data(promo_code=message.text.upper())
    await message.answer("Введите количество баллов за активацию:")
    await state.set_state(AdminStates.waiting_for_promo_code_uses)

@dp.message(AdminStates.waiting_for_promo_code_uses)
async def add_promo_points(message: Message, state: FSMContext):
    """Получение количества баллов"""
    try:
        points = int(message.text)
        if points <= 0:
            await message.answer("❌ Количество баллов должно быть больше 0!")
            return
        await state.update_data(promo_points=points)
        await message.answer("Введите максимальное количество активаций:")
        await state.set_state(AdminStates.waiting_for_promo_code_edit)
    except ValueError:
        await message.answer("❌ Введите число!")

@dp.message(AdminStates.waiting_for_promo_code_edit)
async def add_promo_max_uses(message: Message, state: FSMContext):
    """Получение максимального количества активаций и создание промокода"""
    try:
        max_uses = int(message.text)
        if max_uses <= 0:
            await message.answer("❌ Количество активаций должно быть больше 0!")
            return
        
        data = await state.get_data()
        
        # Создаем промокод
        promo_id = add_promocode(
            code=data['promo_code'],
            name=data['promo_name'],
            points=data['promo_points'],
            max_uses=max_uses
        )
        
        if promo_id:
            await message.answer(
                f"✅ Промокод успешно создан!\n\n"
                f"📌 Название: {data['promo_name']}\n"
                f"🎫 Код: {data['promo_code']}\n"
                f"💰 Баллы: {data['promo_points']}\n"
                f"📊 Макс. активаций: {max_uses}"
            )
        else:
            await message.answer("❌ Промокод с таким кодом уже существует!")
        
    except ValueError:
        await message.answer("❌ Введите число!")
    finally:
        await state.clear()

@dp.callback_query(F.data == "edit_promos_list")
async def edit_promos_list(callback: CallbackQuery):
    """Список промокодов для редактирования"""
    await callback.answer()
    
    promos = get_all_promocodes(include_inactive=True)
    
    text = "🎫 ВЫБЕРИТЕ ПРОМОКОД ДЛЯ РЕДАКТИРОВАНИЯ\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for promo in promos:
        status = "✅" if promo['is_active'] else "❌"
        keyboard.row(InlineKeyboardButton(
            text=f"{status} {promo['code']} - {promo['name']}",
            callback_data=f"edit_promo_{promo['id']}"
        ))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("edit_promo_"))
async def edit_promo_menu(callback: CallbackQuery):
    """Меню редактирования промокода"""
    await callback.answer()
    
    promo_id = int(callback.data.split("_")[2])
    promo = get_promocode_by_id(promo_id)
    
    if not promo:
        await safe_edit_message(callback.message, "❌ Промокод не найден!")
        return
    
    expires = promo['expires_at'] if promo['expires_at'] else "Бессрочно"
    status = "Активен" if promo['is_active'] else "Неактивен"
    
    text = (
        f"🎫 РЕДАКТИРОВАНИЕ ПРОМОКОДА\n\n"
        f"ID: {promo['id']}\n"
        f"Код: {promo['code']}\n"
        f"Название: {promo['name']}\n"
        f"Баллы: {promo['points']}💰\n"
        f"Активаций: {promo['used_count']}/{promo['max_uses']}\n"
        f"Срок: {expires}\n"
        f"Статус: {status}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="✏️ Изменить код", callback_data=f"edit_promo_code_{promo_id}"))
    keyboard.row(InlineKeyboardButton(text="📝 Изменить название", callback_data=f"edit_promo_name_{promo_id}"))
    keyboard.row(InlineKeyboardButton(text="💰 Изменить баллы", callback_data=f"edit_promo_points_{promo_id}"))
    keyboard.row(InlineKeyboardButton(text="📊 Изменить лимит", callback_data=f"edit_promo_uses_{promo_id}"))
    keyboard.row(InlineKeyboardButton(text="🔄 Изменить статус", callback_data=f"toggle_promo_{promo_id}"))
    keyboard.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_promo_{promo_id}"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="edit_promos_list"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "promo_history")
async def promo_history(callback: CallbackQuery):
    """История активаций промокодов"""
    await callback.answer()
    
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT p.code, p.name, pa.user_id, u.username, u.first_name, pa.activated_at
        FROM promo_activations pa
        JOIN promocodes p ON pa.promo_id = p.id
        LEFT JOIN users u ON pa.user_id = u.user_id
        ORDER BY pa.activated_at DESC
        LIMIT 20
    """)
    activations = [dict(row) for row in c.fetchall()]
    conn.close()
    
    text = "📊 ИСТОРИЯ АКТИВАЦИЙ\n\n"
    
    if not activations:
        text += "Пока нет активаций промокодов."
    else:
        for act in activations:
            user_display = get_user_display(act['user_id'], act['username'], act['first_name'])
            text += f"• {act['code']} - {act['name']}\n"
            text += f"  Пользователь: {user_display}\n"
            text += f"  Время: {act['activated_at'][:16]}\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes")]
    ])
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard)

# ==================== ОБРАБОТЧИКИ ДЛЯ РЕДАКТИРОВАНИЯ ПОЛЕЙ ПРОМОКОДА ====================

@dp.callback_query(F.data.startswith("edit_promo_code_"))
async def edit_promo_code_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения кода промокода"""
    await callback.answer()
    try:
        promo_id = int(callback.data.split("_")[3])
        await state.update_data(promo_id=promo_id, edit_field="code")
        await safe_edit_message(callback.message, "Введите новый код промокода:")
        await state.set_state(AdminStates.waiting_for_promo_code_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")

@dp.callback_query(F.data.startswith("edit_promo_name_"))
async def edit_promo_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения названия промокода"""
    await callback.answer()
    try:
        promo_id = int(callback.data.split("_")[3])
        await state.update_data(promo_id=promo_id, edit_field="name")
        await safe_edit_message(callback.message, "Введите новое название промокода:")
        await state.set_state(AdminStates.waiting_for_promo_code_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")

@dp.callback_query(F.data.startswith("edit_promo_points_"))
async def edit_promo_points_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения количества баллов"""
    await callback.answer()
    try:
        promo_id = int(callback.data.split("_")[3])
        await state.update_data(promo_id=promo_id, edit_field="points")
        await safe_edit_message(callback.message, "Введите новое количество баллов:")
        await state.set_state(AdminStates.waiting_for_promo_code_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")

@dp.callback_query(F.data.startswith("edit_promo_uses_"))
async def edit_promo_uses_start(callback: CallbackQuery, state: FSMContext):
    """Начало изменения лимита активаций"""
    await callback.answer()
    try:
        promo_id = int(callback.data.split("_")[3])
        await state.update_data(promo_id=promo_id, edit_field="max_uses")
        await safe_edit_message(callback.message, "Введите новый лимит активаций:")
        await state.set_state(AdminStates.waiting_for_promo_code_edit)
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")

@dp.message(AdminStates.waiting_for_promo_code_edit)
async def edit_promo_finish(message: Message, state: FSMContext):
    """Сохранение изменений промокода"""
    data = await state.get_data()
    promo_id = data['promo_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    update_data = {}
    
    if edit_field == "code":
        # Проверяем, не занят ли код
        existing = get_promocode_by_code(new_value.upper())
        if existing and existing['id'] != promo_id:
            await message.answer("❌ Промокод с таким кодом уже существует!")
            return
        update_data['code'] = new_value.upper()
    
    elif edit_field == "name":
        update_data['name'] = new_value
    
    elif edit_field == "points":
        try:
            points = int(new_value)
            if points <= 0:
                await message.answer("❌ Количество баллов должно быть больше 0!")
                return
            update_data['points'] = points
        except ValueError:
            await message.answer("❌ Введите число!")
            return
    
    elif edit_field == "max_uses":
        try:
            max_uses = int(new_value)
            if max_uses <= 0:
                await message.answer("❌ Лимит активаций должен быть больше 0!")
                return
            
            # Проверяем, не меньше ли новый лимит уже использованных
            promo = get_promocode_by_id(promo_id)
            if promo and max_uses < promo['used_count']:
                await message.answer(f"❌ Нельзя установить лимит меньше уже использованных ({promo['used_count']})!")
                return
            
            update_data['max_uses'] = max_uses
        except ValueError:
            await message.answer("❌ Введите число!")
            return
    
    if update_data:
        if update_promocode(promo_id, **update_data):
            # Получаем обновленный промокод для красивого сообщения
            promo = get_promocode_by_id(promo_id)
            await message.answer(
                f"✅ Промокод обновлен!\n\n"
                f"📌 Новые данные:\n"
                f"Код: {promo['code']}\n"
                f"Название: {promo['name']}\n"
                f"Баллы: {promo['points']}💰\n"
                f"Лимит: {promo['max_uses']}"
            )
            await asyncio.sleep(1)
            
            # Возвращаемся к меню промокода
            fake_callback = type('obj', (object,), {
                'message': message,
                'answer': lambda: None,
                'data': f"edit_promo_{promo_id}"
            })
            await edit_promo_menu(fake_callback)
        else:
            await message.answer("❌ Ошибка при обновлении промокода")
    else:
        await message.answer("❌ Нет данных для обновления")
    
    await state.clear()

@dp.callback_query(F.data.startswith("toggle_promo_"))
async def toggle_promo_handler(callback: CallbackQuery):
    """Изменение статуса промокода"""
    await callback.answer()
    
    try:
        promo_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")
        return
    
    promo = get_promocode_by_id(promo_id)
    
    if promo:
        update_promocode(promo_id, is_active=not promo['is_active'])
        new_status = "активирован" if not promo['is_active'] else "деактивирован"
        await safe_edit_message(callback.message, f"✅ Промокод {new_status}!")
        await asyncio.sleep(1)
        await edit_promo_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Промокод не найден!")

@dp.callback_query(F.data.startswith("delete_promo_"))
async def delete_promo_confirm(callback: CallbackQuery):
    """Подтверждение удаления промокода"""
    await callback.answer()
    
    try:
        promo_id = int(callback.data.split("_")[2])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")
        return
    
    # Проверяем, были ли активации
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as count FROM promo_activations WHERE promo_id = ?", (promo_id,))
    activations = c.fetchone()['count']
    conn.close()
    
    warning = ""
    if activations > 0:
        warning = f"\n\n⚠️ У этого промокода {activations} активаций! История активаций также будет удалена."
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_promo_{promo_id}")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"edit_promo_{promo_id}")]
    ])
    
    await safe_edit_message(
        callback.message,
        f"⚠️ Вы уверены, что хотите удалить этот промокод?{warning}\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("confirm_delete_promo_"))
async def confirm_delete_promo(callback: CallbackQuery):
    """Подтвержденное удаление промокода"""
    await callback.answer()
    
    try:
        promo_id = int(callback.data.split("_")[3])
    except:
        await safe_edit_message(callback.message, "❌ Ошибка в данных промокода")
        return
    
    if delete_promocode(promo_id):
        await safe_edit_message(callback.message, "✅ Промокод успешно удален!")
        await asyncio.sleep(1)
        await admin_promocodes_menu(callback)
    else:
        await safe_edit_message(callback.message, "❌ Ошибка при удалении промокода")

# Добавляем обработчик для возврата к списку промокодов
@dp.callback_query(F.data == "back_to_promos")
async def back_to_promos(callback: CallbackQuery):
    """Возврат к списку промокодов"""
    await callback.answer()
    await admin_promocodes_menu(callback)
# ==================== ИГРА MINES ====================

@dp.message(F.text == "💣 MINES")
async def mines_start(message: Message, state: FSMContext):
    """Начало игры Mines - выбор количества мин"""
    user_id = message.from_user.id
    user_data = get_user_data(user_id)
    
    if not user_data:
        await message.answer("❌ Сначала запустите бота через /start")
        return
    
    points = user_data['points']
    
    if points <= 0:
        await message.answer(
            "❌ У вас нет баллов для игры!\n"
            "Выполняйте задания или крутите колесо, чтобы заработать баллы.",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    # Показываем доступные варианты количества мин (БЕЗ КОЭФФИЦИЕНТОВ)
    text = (
        f"💣 ДОБРО ПОЖАЛОВАТЬ В MINES!\n\n"
        f"💰 Ваш баланс: {points} баллов\n"
        f"🎯 Максимальная ставка: {MINES_MAX_BET} баллов\n\n"
        f"Выберите количество мин на поле:\n"
    )
    
    # Просто список мин без коэффициентов
    keyboard = []
    for mines_count in AVAILABLE_MINES_COUNTS:
        text += f"• {mines_count} мин\n"
        keyboard.append([KeyboardButton(text=f"{mines_count} мин")])
    
    keyboard.append([KeyboardButton(text="❌ Отмена")])
    
    await message.answer(
        text,
        reply_markup=ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    )
    await state.set_state(MinesStates.waiting_for_mines_count)

@dp.message(MinesStates.waiting_for_mines_count)
async def mines_select_count(message: Message, state: FSMContext):
    """Выбор количества мин"""
    user_id = message.from_user.id
    
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Игра отменена", reply_markup=get_main_keyboard(user_id))
        return
    
    try:
        mines_count = int(message.text.replace(" мин", ""))
    except:
        await message.answer("❌ Пожалуйста, выберите количество мин из меню")
        return
    
    if mines_count not in AVAILABLE_MINES_COUNTS:
        await message.answer("❌ Недоступное количество мин")
        return
    
    await state.update_data(mines_count=mines_count)
    
    user_data = get_user_data(user_id)
    points = user_data['points']
    
    await message.answer(
        f"💣 Выбрано мин: {mines_count}\n"
        f"💰 Ваш баланс: {points} баллов\n\n"
        f"Введите сумму ставки (от 1 до {min(MINES_MAX_BET, points)}):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )
    await state.set_state(MinesStates.waiting_for_bet)

@dp.message(MinesStates.waiting_for_bet)
async def mines_process_bet(message: Message, state: FSMContext):
    """Обработка ставки"""
    user_id = message.from_user.id
    
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Игра отменена", reply_markup=get_main_keyboard(user_id))
        return
    
    try:
        bet = int(message.text)
    except ValueError:
        await message.answer("❌ Введите число!")
        return
    
    user_data = get_user_data(user_id)
    points = user_data['points']
    
    if bet < 1:
        await message.answer("❌ Минимальная ставка - 1 балл")
        return
    
    if bet > MINES_MAX_BET:
        await message.answer(f"❌ Максимальная ставка - {MINES_MAX_BET} баллов")
        return
    
    if bet > points:
        await message.answer(f"❌ У вас только {points} баллов!")
        return
    
    data = await state.get_data()
    mines_count = data['mines_count']
    
    field, mine_positions = create_mines_field(mines_count)
    
    await state.update_data(
        bet=bet,
        field=field,
        mine_positions=mine_positions,
        mines_count=mines_count,
        opened=[],
        current_step=0
    )
    
    update_user_points(user_id, -bet)
    
    text = format_mines_field(field, [], mines_count)
    text += f"\n💰 Ставка: {bet} баллов\n"
    text += f"📈 Множитель: x1.0\n"
    text += f"💎 Потенциальный выигрыш: {bet} баллов\n\n"
    text += "Выберите клетку (1-25) или заберите выигрыш:"
    
    # Создаем клавиатуру с клетками
    keyboard = []
    row = []
    for i in range(1, 26):
        row.append(KeyboardButton(text=str(i)))
        if i % 5 == 0:
            keyboard.append(row)
            row = []
    
    keyboard.append([KeyboardButton(text="💰 Забрать выигрыш"), KeyboardButton(text="❌ Выйти")])
    
    await message.answer(
        text,
        reply_markup=ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    )
    await state.set_state(MinesStates.playing)

@dp.message(MinesStates.playing)
async def mines_game(message: Message, state: FSMContext):
    """Игровой процесс Mines"""
    user_id = message.from_user.id
    
    if message.text == "❌ Выйти":
        await state.clear()
        await message.answer("Игра завершена", reply_markup=get_main_keyboard(user_id))
        return
    
    data = await state.get_data()
    bet = data['bet']
    field = data['field']
    mine_positions = data['mine_positions']
    opened = data['opened']
    mines_count = data['mines_count']
    current_step = data['current_step']
    max_steps = get_max_steps(mines_count)
    
    if message.text == "💰 Забрать выигрыш":
        if current_step > 0:
            multiplier = get_multiplier(mines_count, current_step)
            win = int(bet * multiplier)
        else:
            multiplier = 1.0
            win = bet
        
        update_user_points(user_id, win)
        
        # Открываем все мины для наглядности
        field_display = list(field)
        for pos in mine_positions:
            if pos not in opened:
                field_display[pos] = '💣'
        
        # Формируем отображение поля с минами
        field_text = ""
        for i in range(0, len(field_display), MINES_FIELD_SIZE):
            row = ""
            for j in range(MINES_FIELD_SIZE):
                idx = i + j
                if idx in opened:
                    row += "✅ "
                elif idx in mine_positions:
                    row += "💣 "
                else:
                    row += "⬜ "
            field_text += row + "\n"
        
        text = (
            f"✅ ВЫ ЗАБРАЛИ ВЫИГРЫШ!\n\n"
            f"{field_text}\n"
            f"📊 Открыто клеток: {current_step}\n"
            f"📈 Множитель: x{multiplier:.2f}\n"
            f"💰 Выигрыш: {win} баллов\n\n"
            f"💣 Мины были на позициях: {', '.join([str(p+1) for p in mine_positions])}\n"
            f"💰 Новый баланс: {get_user_points(user_id)} баллов"
        )
        
        await state.clear()
        await message.answer(text, reply_markup=get_main_keyboard(user_id))
        return
    
    try:
        cell = int(message.text) - 1
    except ValueError:
        await message.answer("❌ Выберите клетку от 1 до 25!")
        return
    
    if cell < 0 or cell >= 25:
        await message.answer("❌ Выберите клетку от 1 до 25!")
        return
    
    if cell in opened:
        await message.answer("❌ Эта клетка уже открыта!")
        return
    
    # Проверяем, не мина ли это
    if cell in mine_positions:
        # Проигрыш - показываем все мины
        field_display = list(field)
        for pos in mine_positions:
            field_display[pos] = '💣'
        
        # Формируем отображение поля с минами
        field_text = ""
        for i in range(0, len(field_display), MINES_FIELD_SIZE):
            row = ""
            for j in range(MINES_FIELD_SIZE):
                idx = i + j
                if idx in opened:
                    row += "✅ "
                elif idx in mine_positions:
                    row += "💣 "
                else:
                    row += "⬜ "
            field_text += row + "\n"
        
        text = (
            f"💥 БАБАХ! Вы наткнулись на мину!\n\n"
            f"{field_text}\n"
            f"💰 Потеряно: {bet} баллов\n"
            f"💔 Новый баланс: {get_user_points(user_id)} баллов"
        )
        
        await state.clear()
        await message.answer(text, reply_markup=get_main_keyboard(user_id))
        return
    
    # Открываем клетку
    opened.append(cell)
    current_step += 1
    
    # Получаем множитель для текущего шага
    multiplier = get_multiplier(mines_count, current_step)
    
    # Обновляем данные
    await state.update_data(opened=opened, current_step=current_step)
    
    # Показываем обновленное поле
    text = format_mines_field(field, opened, mines_count)
    text += f"\n💰 Ставка: {bet} баллов\n"
    text += f"📈 Текущий множитель: x{multiplier:.2f}\n"
    
    if current_step == max_steps:
        text += "🎉 ДЖЕКПОТ! Вы открыли все безопасные клетки!\n"
        text += "Нажмите 'Забрать выигрыш' для получения выигрыша!\n\n"
    else:
        remaining = max_steps - current_step
        potential_next = calculate_potential_win(bet, mines_count, current_step)
        text += f"💎 Потенциальный выигрыш на след. шаге: {potential_next} баллов\n"
        text += f"✅ Открыто безопасных клеток: {current_step}\n"
        text += f"💣 Осталось безопасных клеток: {remaining}\n\n"
    
    text += "Выберите клетку (1-25) или заберите выигрыш:"
    
    # Создаем клавиатуру
    keyboard = []
    row = []
    for i in range(1, 26):
        if i-1 in opened:
            row.append(KeyboardButton(text="✅"))
        else:
            row.append(KeyboardButton(text=str(i)))
        if i % 5 == 0:
            keyboard.append(row)
            row = []
    
    keyboard.append([KeyboardButton(text="💰 Забрать выигрыш"), KeyboardButton(text="❌ Выйти")])
    
    await message.answer(
        text,
        reply_markup=ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    )

@dp.callback_query(F.data == "admin_mines")
async def admin_mines_settings(callback: CallbackQuery, state: FSMContext):
    """Настройка игры Mines"""
    await callback.answer()
    
    current_default = get_mines_settings()
    
    text = (
        f"💣 НАСТРОЙКА ИГРЫ MINES\n\n"
        f"Доступные уровни сложности:\n"
    )
    
    for mines_count in AVAILABLE_MINES_COUNTS:
        max_steps = get_max_steps(mines_count)
        final_mult = get_multiplier(mines_count, max_steps)
        text += f"• {mines_count} мин - {max_steps} шагов, макс. x{final_mult:.2f}\n"
    
    text += f"\nТекущее значение по умолчанию: {current_default} мин\n\n"
    text += "Введите новое значение по умолчанию (2, 3, 5, 10, 24):"
    
    await callback.message.edit_text(text)
    # ВАЖНО: используем правильное состояние
    await state.set_state(AdminStates.waiting_for_mines_count)

@dp.message(AdminStates.waiting_for_mines_count)
async def admin_mines_set_default(message: Message, state: FSMContext):
    """Установка значения по умолчанию"""
    try:
        default_mines = int(message.text)
    except ValueError:
        await message.answer("❌ Введите число!")
        return
    
    if default_mines not in AVAILABLE_MINES_COUNTS:
        await message.answer("❌ Доступные значения: 2, 3, 5, 10, 24")
        return
    
    if update_mines_settings(default_mines):
        await message.answer(f"✅ Значение по умолчанию изменено на {default_mines} мин!")
    else:
        await message.answer("❌ Ошибка при сохранении настроек!")
    
    await state.clear()
    await show_admin_menu(message)

# Добавляем обработчик для отмены
@dp.message(F.text == "❌ Отмена")
async def cancel_game(message: Message, state: FSMContext):
    """Отмена любой игры или действия"""
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
    await message.answer("Действие отменено", reply_markup=get_main_keyboard(message.from_user.id))
# ==================== РАССЫЛКА ====================

@dp.callback_query(F.data == "admin_mailing")
async def admin_mailing_start(callback: CallbackQuery, state: FSMContext):
    """Начало рассылки"""
    await callback.answer()
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM users")
        count = c.fetchone()['count']
    
    await safe_edit_message(
        callback.message,
        f"📨 РАССЫЛКА\n\n"
        f"Всего пользователей: {count}\n\n"
        f"Введите текст для рассылки (можно использовать эмодзи):"
    )
    await state.set_state(AdminStates.waiting_for_mailing)

@dp.message(AdminStates.waiting_for_mailing)
async def admin_mailing_preview(message: Message, state: FSMContext):
    """Предпросмотр рассылки"""
    text = message.text
    await state.update_data(mailing_text=text)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_mailing")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_admin")]
    ])
    
    await message.answer(
        f"📨 ПРЕДПРОСМОТР РАССЫЛКИ:\n\n{text}\n\n"
        f"Отправить это сообщение всем пользователям?",
        reply_markup=keyboard
    )
    await state.set_state(AdminStates.waiting_for_mailing_confirm)

@dp.callback_query(F.data == "confirm_mailing", AdminStates.waiting_for_mailing_confirm)
async def admin_mailing_send(callback: CallbackQuery, state: FSMContext):
    """Отправка рассылки"""
    await callback.answer()
    
    data = await state.get_data()
    text = data['mailing_text']
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        users = [dict(row) for row in c.fetchall()]
    
    status_msg = await callback.message.edit_text(f"📨 Начинаю рассылку {len(users)} пользователям...")
    
    success = 0
    failed = 0
    
    for i, user in enumerate(users):
        try:
            await bot.send_message(user['user_id'], text)
            success += 1
            if i % 10 == 0:
                await status_msg.edit_text(f"📨 Прогресс: {success}/{len(users)}...")
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
    
    await status_msg.edit_text(f"✅ Рассылка завершена!\nУспешно: {success}\nОшибок: {failed}")
    await state.clear()

# ==================== НАЧИСЛЕНИЕ БОНУСОВ ====================

@dp.callback_query(F.data == "admin_add_bonus")
async def add_bonus_start(callback: CallbackQuery, state: FSMContext):
    """Начало начисления бонусов"""
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "💰 НАЧИСЛЕНИЕ БОНУСОВ\n\n"
        "Введите ID пользователя:"
    )
    await state.set_state(AdminStates.waiting_for_user_id)

@dp.message(AdminStates.waiting_for_user_id)
async def add_bonus_user(message: Message, state: FSMContext):
    """Получение ID пользователя"""
    try:
        user_id = int(message.text)
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT username, first_name FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
        
        if not user:
            await message.answer("❌ Пользователь с таким ID не найден!")
            await state.clear()
            return
        
        user_dict = dict(user)
        user_display = get_user_display(user_id, user_dict['username'], user_dict['first_name'])
        
        await state.update_data(user_id=user_id, user_display=user_display)
        await message.answer(
            f"👤 Найден пользователь: {user_display}\n\n"
            f"Введите количество попыток для начисления (или 0):"
        )
        await state.set_state(AdminStates.waiting_for_spins_amount)
    except ValueError:
        await message.answer("❌ Неверный ID пользователя! Введите число.")
        await state.clear()

@dp.message(AdminStates.waiting_for_spins_amount)
async def add_bonus_spins(message: Message, state: FSMContext):
    """Получение количества попыток"""
    try:
        spins = int(message.text)
        if spins < 0:
            await message.answer("❌ Число должно быть положительным!")
            return
        
        await state.update_data(spins=spins)
        await message.answer("Введите количество баллов для начисления (или 0):")
        await state.set_state(AdminStates.waiting_for_points_amount)
    except ValueError:
        await message.answer("❌ Введите число!")
        await state.clear()

@dp.message(AdminStates.waiting_for_points_amount)
async def add_bonus_points(message: Message, state: FSMContext):
    """Начисление бонусов"""
    try:
        points = int(message.text)
        if points < 0:
            await message.answer("❌ Число должно быть положительным!")
            return
        
        data = await state.get_data()
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET spins = spins + ?, points = points + ? WHERE user_id = ?",
                      (data['spins'], points, data['user_id']))
            
            c.execute("SELECT spins, points FROM users WHERE user_id = ?", (data['user_id'],))
            user_row = c.fetchone()
            user = dict(user_row) if user_row else {"spins": 0, "points": 0}
            conn.commit()
        
        try:
            await bot.send_message(
                data['user_id'],
                f"💰 ВАМ НАЧИСЛЕНЫ БОНУСЫ!\n\n"
                f"+{data['spins']}🎡 попыток\n"
                f"+{points}💰 баллов\n\n"
                f"Текущий баланс: {user['spins']}🎡, {user['points']}💰"
            )
        except:
            pass
        
        await message.answer(
            f"✅ Бонусы начислены пользователю {data['user_display']}!\n"
            f"+{data['spins']}🎡 +{points}💰\n\n"
            f"Новый баланс: {user['spins']}🎡, {user['points']}💰"
        )
        
        add_admin_notification(
            "bonus",
            f"💰 Начислены бонусы!\nПользователь: {data['user_display']}\n+{data['spins']}🎡 +{points}💰",
            data['user_id']
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при начислении: {str(e)}")
    
    await state.clear()

# ==================== ВОЗВРАТ В АДМИН МЕНЮ ====================

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin_handler(callback: CallbackQuery):
    """Возврат в админ меню"""
    await callback.answer()
    await show_admin_menu(callback.message)

# ==================== ОБРАБОТЧИКИ ДЛЯ НЕИЗВЕСТНЫХ КОМАНД ====================
@dp.message(Command("backup"))
async def cmd_backup(message: Message):
    """Команда для скачивания резервной копии БД (только для админов)"""
    user_id = message.from_user.id
    
    # Проверяем, является ли пользователь админом
    if user_id not in ADMIN_IDS:
        await message.answer("⛔ Доступ запрещен!")
        return
    
    await message.answer("🔄 Создаю резервную копию базы данных...")
    
    try:
        # Отправляем файл базы данных
        with open('casino_bot.db', 'rb') as f:
            file_data = f.read()
            
        await message.answer_document(
            types.input_file.BufferedInputFile(
                file_data,
                filename=f'casino_bot_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            ),
            caption="📦 Резервная копия базы данных"
        )
        
        # Показываем статистику
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as count FROM users")
        users_count = c.fetchone()['count']
        conn.close()
        
        await message.answer(f"📊 В базе данных {users_count} пользователей")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при создании бэкапа: {e}")

@dp.message()
async def handle_unknown(message: Message):
    """Обработчик неизвестных сообщений"""
    user_id = message.from_user.id
    await message.answer(
        "❓ Я не понимаю эту команду.\n"
        "Используй кнопки меню для навигации!",
        reply_markup=get_main_keyboard(user_id)
    )

@dp.callback_query()
async def handle_unknown_callback(callback: CallbackQuery):
    """Обработчик неизвестных callback запросов"""
    await callback.answer("Эта кнопка больше не работает!", show_alert=True)


async def main():
    init_db()
    migrate_db()
    migrate_projects_table()
    migrate_promocodes_table()
    migrate_shop_table()  # ЭТА СТРОКА ДОЛЖНА БЫТЬ
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())




























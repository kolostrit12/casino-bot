# 1. СНАЧАЛА импорты
import asyncio
import logging
import sqlite3
import random
import os
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, 
    InlineKeyboardButton, CallbackQuery, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
    """Инициализация базы данных"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Таблица пользователей
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY,
                      username TEXT,
                      first_name TEXT,
                      points INTEGER DEFAULT 0,
                      spins INTEGER DEFAULT 3,
                      referrer_id INTEGER DEFAULT NULL,
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      total_spins INTEGER DEFAULT 0,
                      total_wins INTEGER DEFAULT 0,
                      last_daily_bonus DATE DEFAULT NULL,
                      daily_bonus_streak INTEGER DEFAULT 0,
                      last_free_spin DATE DEFAULT NULL)''')
        
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
                      project_id INTEGER DEFAULT NULL,
                      is_used BOOLEAN DEFAULT 0,
                      buyer_id INTEGER DEFAULT NULL,
                      bought_at TIMESTAMP DEFAULT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (project_id) REFERENCES projects (id))''')
        
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
                      project_id INTEGER DEFAULT NULL,
                      is_used BOOLEAN DEFAULT 0,
                      winner_id INTEGER DEFAULT NULL,
                      won_at TIMESTAMP DEFAULT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (project_id) REFERENCES projects (id))''')
        
        # Таблица уведомлений для админов
        c.execute('''CREATE TABLE IF NOT EXISTS admin_notifications
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      type TEXT,
                      message TEXT,
                      user_id INTEGER,
                      data TEXT,
                      is_read BOOLEAN DEFAULT 0,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Добавляем начальные данные
        c.execute("SELECT COUNT(*) as count FROM tasks")
        if c.fetchone()['count'] == 0:
            initial_tasks = [
                ("Подписаться на канал", "Подпишитесь на наш официальный канал", 1, 50, "channel", "@your_channel"),
                ("Вступить в чат", "Присоединитесь к нашему чату", 2, 100, "chat", "@your_chat"),
                ("Посетить сайт партнера", "Перейдите на сайт нашего партнера", 1, 0, "website", "https://example.com"),
            ]
            c.executemany("INSERT INTO tasks (task_name, task_description, reward_spins, reward_points, task_type, task_data) VALUES (?,?,?,?,?,?)", initial_tasks)
        
        c.execute("SELECT COUNT(*) as count FROM projects")
        if c.fetchone()['count'] == 0:
            initial_projects = [
                ("Casino X", "https://example.com/casino_x", "XBONUS"),
                ("Azino 777", "https://example.com/azino777", "777GOLD"),
                ("Joy Casino", "https://example.com/joy", "JOYSPIN"),
            ]
            c.executemany("INSERT INTO projects (title, url, promo_code) VALUES (?,?,?)", initial_projects)
        
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
        
        c.execute("SELECT COUNT(*) as count FROM jackpot_promocodes")
        if c.fetchone()['count'] == 0:
            # Проверяем, есть ли проекты для привязки
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
        
        conn.commit()
        print("✅ База данных инициализирована")
        
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
        
        # Проверяем и добавляем колонку project_id в таблицу jackpot_promocodes
        c.execute("PRAGMA table_info(jackpot_promocodes)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'project_id' not in columns:
            c.execute("ALTER TABLE jackpot_promocodes ADD COLUMN project_id INTEGER DEFAULT NULL")
            print("✅ Добавлена колонка project_id в jackpot_promocodes")
        
        # Проверяем и добавляем колонку project_id в таблицу shop_promocodes
        c.execute("PRAGMA table_info(shop_promocodes)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'project_id' not in columns:
            c.execute("ALTER TABLE shop_promocodes ADD COLUMN project_id INTEGER DEFAULT NULL")
            print("✅ Добавлена колонка project_id в shop_promocodes")
        
        # Проверяем и добавляем колонки для бонусов в таблицу users
        c.execute("PRAGMA table_info(users)")
        columns = [col['name'] for col in c.fetchall()]
        
        if 'last_daily_bonus' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN last_daily_bonus DATE DEFAULT NULL")
            print("✅ Добавлена колонка last_daily_bonus в users")
        
        if 'daily_bonus_streak' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN daily_bonus_streak INTEGER DEFAULT 0")
            print("✅ Добавлена колонка daily_bonus_streak в users")
        
        if 'last_free_spin' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN last_free_spin DATE DEFAULT NULL")
            print("✅ Добавлена колонка last_free_spin в users")
        
        conn.commit()
        print("✅ Миграция базы данных завершена")
        
    except Exception as e:
        print(f"❌ Ошибка при миграции БД: {e}")
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
        return [dict(row) for row in c.fetchall()]
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
    """Вращение колеса с учетом вероятностей (без отображения шансов)"""
    prizes = get_wheel_prizes()
    
    if not prizes:
        return {"name": "Пусто", "type": "empty", "value": 0, "probability": 100}
    
    total_probability = sum(prize['probability'] for prize in prizes)
    
    if total_probability != 100:
        factor = 100 / total_probability
        for prize in prizes:
            prize['probability'] = int(prize['probability'] * factor)
    
    chosen_index = random.choices(range(len(prizes)), weights=[p['probability'] for p in prizes])[0]
    
    return prizes[chosen_index]

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
def get_shop_items(include_used: bool = False) -> List:
    """Получить список товаров в магазине"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        if include_used:
            c.execute("""
                SELECT s.*, p.title as project_title, p.url as project_url 
                FROM shop_promocodes s
                LEFT JOIN projects p ON s.project_id = p.id
                ORDER BY s.id
            """)
        else:
            c.execute("""
                SELECT s.*, p.title as project_title, p.url as project_url 
                FROM shop_promocodes s
                LEFT JOIN projects p ON s.project_id = p.id
                WHERE s.is_used = 0 
                ORDER BY s.price
            """)
        return [dict(row) for row in c.fetchall()]
    finally:
        if conn:
            conn.close()

@retry_on_locked()
def get_shop_item_by_id(item_id: int):
    """Получить товар по ID"""
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
def add_shop_item(name: str, price: int, promo_code: str, project_id: int = None):
    """Добавить товар в магазин"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO shop_promocodes (name, price, promo_code, project_id) VALUES (?, ?, ?, ?)",
            (name, price, promo_code, project_id)
        )
        conn.commit()
        return c.lastrowid
    finally:
        if conn:
            conn.close()

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
def buy_shop_item(user_id: int, item_id: int) -> Tuple[bool, str, dict]:
    """Купить товар в магазине"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        
        c.execute("""
            SELECT s.*, p.title as project_title, p.url as project_url 
            FROM shop_promocodes s
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE s.id = ? AND s.is_used = 0
        """, (item_id,))
        item_row = c.fetchone()
        
        if not item_row:
            return False, "Товар не найден или уже куплен", None
        
        item = dict(item_row)
        
        c.execute("SELECT points, username, first_name FROM users WHERE user_id = ?", (user_id,))
        user_row = c.fetchone()
        
        if not user_row:
            return False, "Пользователь не найден", None
        
        user = dict(user_row)
        
        if user['points'] < item['price']:
            return False, f"Недостаточно баллов! Нужно {item['price']}", None
        
        c.execute("UPDATE users SET points = points - ? WHERE user_id = ?", (item['price'], user_id))
        c.execute(
            "UPDATE shop_promocodes SET is_used = 1, buyer_id = ?, bought_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_id, item_id)
        )
        
        conn.commit()
        
        user_display = get_user_display(user_id, user['username'], user['first_name'])
        
        add_admin_notification(
            "purchase",
            f"💰 Покупка в магазине!\nПользователь: {user_display}\nТовар: {item['name']}\nЦена: {item['price']}💰",
            user_id
        )
        
        return True, item['promo_code'], item
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
def add_project(title: str, url: str, promo_code: str):
    """Добавить проект"""
    conn = None
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT INTO projects (title, url, promo_code) VALUES (?, ?, ?)",
            (title, url, promo_code)
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
        
        allowed_fields = ['title', 'url', 'promo_code', 'is_active']
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
        [KeyboardButton(text="👥 РЕФЕРАЛЫ"), KeyboardButton(text="🎡 КОЛЕСО ФОРТУНЫ")],
        [KeyboardButton(text="🏪 МАГАЗИН"), KeyboardButton(text="👤 ПРОФИЛЬ")],
        [KeyboardButton(text="🎁 БОНУСЫ")],
    ]
    
    if user_id and user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton(text="⚙️ АДМИН ПАНЕЛЬ")])
    
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
async def projects(message: Message):
    """Список проектов"""
    user_id = message.from_user.id
    projects_list = get_projects()
    
    if not projects_list:
        await message.answer("📂 ПРОЕКТЫ\n\nК сожалению, сейчас нет активных проектов.", 
                           reply_markup=get_main_keyboard(user_id))
        return
    
    text = "🔥 АКТУАЛЬНЫЕ ПАРТНЕРСКИЕ ПРОЕКТЫ\n\n"
    text += "🎰 Сорви куш вместе с нашими партнерами!\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for project in projects_list:
        text += f"{project['title']}\n"
        text += f"📌 Промокод: {project['promo_code']}\n\n"
        keyboard.row(InlineKeyboardButton(text=f"🎰 {project['title']}", url=project['url']))
    
    await message.answer(text, reply_markup=keyboard.as_markup())
    await message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

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

@dp.message(F.text == "🎡 КОЛЕСО ФОРТУНЫ")
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
    
    # Создаем клавиатуру с одной кнопкой, которую можно нажимать многократно
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎡 КРУТИТЬ КОЛЕСО (осталось {spins})", callback_data="spin_wheel")]
    ])
    
    await message.answer(
        f"🎡 КОЛЕСО ФОРТУНЫ\n\n"
        f"У тебя {spins} попыток вращения\n\n"
        f"Нажми кнопку ниже, чтобы крутить колесо!",
        reply_markup=keyboard
    )
    # Убираем второе сообщение "Выберите действие"

@dp.callback_query(F.data == "spin_wheel")
async def spin_wheel_callback(callback: CallbackQuery):
    """Вращение колеса"""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    try:
        spins = get_user_spins(user_id)
        
        if spins <= 0:
            await safe_edit_message(callback.message, "❌ У тебя нет попыток! Забери бесплатную попытку в разделе БОНУСЫ.")
            return
        
        update_user_spins(user_id, -1)
        
        result = spin_wheel()
        
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
                
                # Получаем информацию о пользователе
                user_data = get_user_data(user_id)
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
        
        # Обновляем сообщение с результатом
        await safe_edit_message(callback.message, final_text)
        
        # Если остались попытки, показываем обновленную кнопку в новом сообщении
        if new_spins > 0:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🎡 КРУТИТЬ ЕЩЕ (осталось {new_spins})", callback_data="spin_wheel")]
            ])
            await callback.message.answer("Нажми кнопку, чтобы крутить еще!", reply_markup=keyboard)
        else:
            await callback.message.answer("Попытки закончились! Забери бесплатную попытку в разделе БОНУСЫ.", 
                                        reply_markup=get_main_keyboard(user_id))
    
    except Exception as e:
        logger.error(f"Ошибка в spin_wheel_callback: {e}")
        await safe_edit_message(callback.message, "❌ Произошла ошибка при вращении колеса. Попробуйте еще раз.")

@dp.message(F.text == "🏪 МАГАЗИН")
async def shop(message: Message):
    """Магазин промокодов"""
    user_id = message.from_user.id
    items = get_shop_items()
    
    if not items:
        await message.answer("🏪 МАГАЗИН\n\nК сожалению, сейчас нет доступных товаров.",
                           reply_markup=get_main_keyboard(user_id))
        return
    
    text = "🏪 МАГАЗИН ПРОМОКОДОВ\n\n"
    text += "Обменивай баллы на реальные промокоды!\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for item in items:
        keyboard.row(InlineKeyboardButton(
            text=f"{item['name']} | {item['price']}💰",
            callback_data=f"buy_{item['id']}"
        ))
    
    await message.answer(text, reply_markup=keyboard.as_markup())
    await message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

@dp.callback_query(F.data.startswith("buy_"))
async def buy_callback(callback: CallbackQuery):
    """Покупка промокода"""
    await callback.answer()
    
    item_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    success, result, item = buy_shop_item(user_id, item_id)
    
    if success:
        text = f"✅ Покупка успешна!\n\n🎫 Твой промокод:\n{result}"
        
        if item and item.get('project_url'):
            text += f"\n\n🔗 Ссылка на проект: {item['project_url']}"
        
        await safe_edit_message(callback.message, text)
    else:
        await safe_edit_message(callback.message, f"❌ {result}")
    
    await callback.message.answer("Выберите действие:", reply_markup=get_main_keyboard(user_id))

# ==================== БОНУСЫ ====================

@dp.message(F.text == "🎁 БОНУСЫ")
async def bonuses_menu(message: Message):
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
    
    if not bonus_status["available"] and not spin_status["available"]:
        text += "✨ Все бонусы на сегодня получены! Возвращайся завтра!"
    
    await message.answer(text, reply_markup=kb.as_markup() if kb.as_markup().inline_keyboard else None)

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

# ==================== АДМИН ПАНЕЛЬ ====================

@dp.message(F.text == "⚙️ АДМИН ПАНЕЛЬ")
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

async def show_admin_menu(message: Message):
    """Показать меню администратора"""
    notifications_count = get_unread_notifications_count()
    
    text = "⚙️ АДМИН ПАНЕЛЬ\n\n"
    if notifications_count > 0:
        text += f"🔔 У вас {notifications_count} новых уведомлений!\n\n"
    text += "Выберите раздел для управления:"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text=f"🔔 Уведомления ({notifications_count})", callback_data="admin_notifications")],
        [InlineKeyboardButton(text="📂 Управление проектами", callback_data="admin_projects")],
        [InlineKeyboardButton(text="🏪 Управление магазином", callback_data="admin_shop")],
        [InlineKeyboardButton(text="🎡 Управление колесом", callback_data="admin_wheel")],
        [InlineKeyboardButton(text="📋 Управление заданиями", callback_data="admin_tasks")],
        [InlineKeyboardButton(text="🎰 Управление джекпотом", callback_data="admin_jackpot")],
        [InlineKeyboardButton(text="📨 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="💰 Начислить бонусы", callback_data="admin_add_bonus")],
    ])
    
    await message.answer(text, reply_markup=keyboard)

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
        "Название | Ссылка | Промокод\n\n"
        "Например: Casino X | https://example.com | XBONUS"
    )
    await state.set_state(AdminStates.waiting_for_project_data)

@dp.message(AdminStates.waiting_for_project_data)
async def add_project_finish(message: Message, state: FSMContext):
    """Сохранение проекта"""
    try:
        parts = [x.strip() for x in message.text.split(" | ")]
        
        if len(parts) != 3:
            await message.answer("❌ Ошибка! Нужно 3 части: Название | Ссылка | Промокод")
            return
        
        title, url, promo = parts
        project_id = add_project(title, url, promo)
        
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
    
    text = (
        f"📂 РЕДАКТИРОВАНИЕ ПРОЕКТА\n\n"
        f"ID: {project['id']}\n"
        f"Название: {project['title']}\n"
        f"URL: {project['url']}\n"
        f"Промокод: {project['promo_code']}\n"
        f"Статус: {status}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"edit_project_title_{project_id}")],
        [InlineKeyboardButton(text="🔗 Изменить URL", callback_data=f"edit_project_url_{project_id}")],
        [InlineKeyboardButton(text="🎫 Изменить промокод", callback_data=f"edit_project_promo_{project_id}")],
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

@dp.message(AdminStates.waiting_for_project_edit)
async def edit_project_finish(message: Message, state: FSMContext):
    """Сохранение изменений проекта"""
    data = await state.get_data()
    project_id = data['project_id']
    edit_field = data['edit_field']
    new_value = message.text.strip()
    
    update_data = {}
    if edit_field == "title":
        update_data['title'] = new_value
    elif edit_field == "url":
        update_data['url'] = new_value
    elif edit_field == "promo":
        update_data['promo_code'] = new_value
    
    if update_project(project_id, **update_data):
        await message.answer(f"✅ Проект обновлен!")
        await asyncio.sleep(1)
        fake_callback = type('obj', (object,), {
            'message': message,
            'answer': lambda: None,
            'data': f"edit_project_{project_id}"
        })
        await edit_project_handler(fake_callback)
    else:
        await message.answer("❌ Ошибка при обновлении проекта")
    
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
    
    items = get_shop_items(include_used=True)
    
    text = "🏪 УПРАВЛЕНИЕ МАГАЗИНОМ\n\n"
    
    if not items:
        text += "Нет товаров в магазине."
    else:
        available = sum(1 for i in items if not i['is_used'])
        sold = sum(1 for i in items if i['is_used'])
        text += f"📊 Статистика:\n"
        text += f"   • Всего товаров: {len(items)}\n"
        text += f"   • Доступно: {available}\n"
        text += f"   • Продано: {sold}\n\n"
        
        text += "📋 Последние товары:\n"
        for item in items[:5]:
            status = "✅" if not item['is_used'] else "💰"
            buyer = f" (куплен ID: {item['buyer_id']})" if item['buyer_id'] else ""
            project_info = f" [Проект: {item['project_title']}]" if item.get('project_title') else ""
            text += f"   {status} {item['name']} - {item['price']}💰{project_info}{buyer}\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data="add_shop_item"))
    keyboard.row(InlineKeyboardButton(text="✏️ Редактировать товары", callback_data="edit_shop_list"))
    keyboard.row(InlineKeyboardButton(text="📊 История покупок", callback_data="shop_purchase_history"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "edit_shop_list")
async def edit_shop_list(callback: CallbackQuery):
    """Список товаров для редактирования"""
    await callback.answer()
    
    items = get_shop_items(include_used=True)
    
    text = "🏪 ВЫБЕРИТЕ ТОВАР ДЛЯ РЕДАКТИРОВАНИЯ\n\n"
    
    keyboard = InlineKeyboardBuilder()
    
    for item in items:
        status = "✅" if not item['is_used'] else "💰"
        project_info = f" [{item['project_title']}]" if item.get('project_title') else ""
        keyboard.row(InlineKeyboardButton(
            text=f"{status} {item['name']}{project_info} | {item['price']}💰",
            callback_data=f"edit_shop_item_{item['id']}"
        ))
    
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_shop"))
    
    await safe_edit_message(callback.message, text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "add_shop_item")
async def add_shop_item_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления товара"""
    await callback.answer()
    
    projects = get_projects()
    projects_text = "\n".join([f"{p['id']}. {p['title']}" for p in projects])
    
    await safe_edit_message(
        callback.message,
        f"Введите данные товара в формате:\n"
        f"Название | Цена | Промокод | ID проекта (необязательно)\n\n"
        f"Доступные проекты:\n{projects_text if projects else 'Нет проектов'}\n\n"
        f"Пример: Промокод Starda на 500₽ | 1000 | STARDA500 | 1\n"
        f"Если проект не нужен, укажите 0"
    )
    await state.set_state(AdminStates.waiting_for_promo_data)

@dp.message(AdminStates.waiting_for_promo_data)
async def add_shop_item_finish(message: Message, state: FSMContext):
    """Сохранение товара"""
    try:
        parts = [x.strip() for x in message.text.split(" | ")]
        
        if len(parts) < 3:
            await message.answer("❌ Ошибка! Нужно минимум 3 части: Название | Цена | Промокод")
            return
        
        name = parts[0]
        price = int(parts[1])
        promo_code = parts[2]
        project_id = int(parts[3]) if len(parts) > 3 and parts[3] != '0' else None
        
        item_id = add_shop_item(name, price, promo_code, project_id)
        
        if item_id:
            await message.answer(f"✅ Товар успешно добавлен! ID: {item_id}")
        else:
            await message.answer("❌ Ошибка при добавлении товара")
            
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
            LIMIT 20
        """)
        purchases = [dict(row) for row in c.fetchall()]
    
    text = "💰 ИСТОРИЯ ПОКУПОК\n\n"
    
    if not purchases:
        text += "Пока нет совершенных покупок."
    else:
        for p in purchases:
            buyer_display = get_user_display(p['buyer_id'], p['username'], p['first_name'])
            text += f"• {p['name']} - {p['price']}💰\n"
            text += f"  Покупатель: {buyer_display}\n"
            text += f"  Время: {p['bought_at'][:16] if p['bought_at'] else 'Неизвестно'}\n\n"
    
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

# 5. В САМОМ КОНЦЕ функция main()
async def main():
    init_db()
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())





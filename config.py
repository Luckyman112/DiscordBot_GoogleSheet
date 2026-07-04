import os
import datetime
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# НАСТРОЙКИ
# ==========================================
SHEET_ID = os.getenv("SHEET_ID")
WORKSHEET_NAME = "АвтоВопросы"  # Название листа (вкладки) внутри таблицы
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_ROLE_ID = os.getenv("TARGET_ROLE_ID")       # ID роли для автодобавления
MODERATOR_ROLE_ID = os.getenv("MODERATOR_ROLE_ID") # Роль модератора вопросов
ADMIN_CHANNEL_ID = os.getenv("ADMIN_CHANNEL_ID")   # Канал для уведомлений и логов

_required = {
    "SHEET_ID": SHEET_ID,
    "DISCORD_TOKEN": TOKEN,
    "TARGET_ROLE_ID": TARGET_ROLE_ID,
    "MODERATOR_ROLE_ID": MODERATOR_ROLE_ID,
    "ADMIN_CHANNEL_ID": ADMIN_CHANNEL_ID,
}
_missing = [name for name, value in _required.items() if not value]
if _missing:
    raise RuntimeError(f"В .env не заданы переменные: {', '.join(_missing)}")

TARGET_ROLE_ID = int(TARGET_ROLE_ID)
MODERATOR_ROLE_ID = int(MODERATOR_ROLE_ID)
ADMIN_CHANNEL_ID = int(ADMIN_CHANNEL_ID)

# Автозапуск ежедневной рассылки при старте бота. Поставь "false" в .env,
# чтобы при локальном тестировании бот поднимался без авторассылки
# (запустить вручную можно командой !go).
AUTO_START_MAILING = os.getenv("AUTO_START_MAILING", "true").lower() == "true"

MEMORY_FILE = 'waiting_answers.json'  # Файл для сохранения памяти бота

# --- НАСТРОЙКА ВРЕМЕНИ РАССЫЛКИ ---
# ВАЖНО: Время здесь указывается по UTC (по Гринвичу).
# Если твой сервер (или нужный часовой пояс) находится в МСК (UTC+3),
# то чтобы рассылка была в 08:00 утра по МСК, здесь нужно указать hour=5.
SEND_TIME = datetime.time(hour=0, minute=55)

# С какого дня игнора подряд пинговать модераторов (1 и 2 день — просто
# мягкое напоминание пользователю, без публичной жалобы).
ESCALATION_THRESHOLD_DAYS = 2

# Сколько секунд после ответа пользователь может исправить его командой !editanswer.
ANSWER_EDIT_WINDOW_SECONDS = 600

# Единый стиль линий для всей таблицы
BORDER_STYLE = {"style": "SOLID_MEDIUM", "color": {"red": 0, "green": 0, "blue": 0}}

# ==========================================
# ДИСКОРД БОТ
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def plural_days(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return f"{n} дня"
    return f"{n} дней"


# --- УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ЛОГИРОВАНИЯ ---
async def log_ds(text):
    print(text)  # Вывод в консоль сервера
    try:
        channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if channel:
            await channel.send(f"`{text}`")  # Отправка в канал Дискорда
    except Exception:
        pass

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import Message
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# 🔐 Токен Telegram-бота от @BotFather
API_TOKEN = '7856781434:AAEhmSaFGEPVigjqEL8_zLobuVMJp9dHBSg'

# 🔧 Подключение к Google Sheets
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

# 📄 Подключаемся к таблице
sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1HiPi8UX_ekCHVDXdRxHwD3NlD2w796T2z_BjNBUj8Bg/edit")
worksheet = sheet.sheet1

# 🤖 Инициализация Telegram-бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Команда /start
@dp.message_handler(commands=['start'])
async def send_welcome(message: Message):
    await message.reply("Привет! Чтобы добавить запись в трекер, используй:\n\n`/add Токен | Причина входа | Потенциал`\n\nПример:\n`/add BONK | памп китов | x10`", parse_mode="Markdown")

# Команда /add
@dp.message_handler(commands=['add'])
async def add_entry(message: Message):
    try:
        text = message.text.replace('/add', '').strip()
        parts = [p.strip() for p in text.split('|')]

        if len(parts) != 3:
            await message.reply("❌ Формат неверный. Пример:\n`/add BONK | памп китов | x10`", parse_mode="Markdown")
            return

        token_name, reason, potential = parts
        today = datetime.today().strftime('%Y-%m-%d')

        values = worksheet.get_all_values()
        row_number = len(values) + 1

        new_row = [
            row_number - 1,       # №
            token_name,           # Название токена
            today,                # Дата входа
            reason,               # Причина входа
            10,                   # Сумма входа ($)
            potential,            # Потенциал (x)
            "", "", "", ""        # Остальные поля
        ]

        worksheet.append_row(new_row)
        await message.reply(f"✅ Запись добавлена: {token_name} — {potential}")

    except Exception as e:
        await message.reply(f"🚫 Ошибка:\n{e}")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)

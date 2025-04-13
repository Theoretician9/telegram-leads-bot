# bot.py

import ssl
import asyncio
import json
import os
from datetime import datetime

import aiohttp
import gspread
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from oauth2client.service_account import ServiceAccountCredentials

# --- Константы ---
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1HiPi8UX_ekCHVDXdRxHwD3NlD2w796T2z_BjNBUj8Bg/edit"
MIN_LIQUIDITY = 5000
MIN_VOLUME = 2000
CHECK_INTERVAL = 60
NETWORK = "bsc"

print("ENV DEBUG:")
print("TOKEN:", os.getenv("TELEGRAM_BOT_TOKEN"))
print("ADMIN_ID:", os.getenv("TELEGRAM_ADMIN_ID"))
print("CREDS:", os.getenv("GOOGLE_CREDS")[:30])  # только начало

# --- Проверка переменных окружения ---
missing_env = []
if not API_TOKEN:
    missing_env.append("TELEGRAM_BOT_TOKEN")
creds_raw = os.getenv('GOOGLE_CREDS')
if not creds_raw:
    missing_env.append("GOOGLE_CREDS")
admin_id = os.getenv('TELEGRAM_ADMIN_ID')
if not admin_id:
    missing_env.append("TELEGRAM_ADMIN_ID")
if missing_env:
    raise EnvironmentError(f"Missing environment variable(s): {', '.join(missing_env)}")

# --- Google Sheets ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(creds_raw)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_url(SPREADSHEET_URL).sheet1

# --- Telegram Bot ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# --- Хранилище уже отправленных токенов ---
sent_tokens = set()

# --- Парсинг новых пар с GeckoTerminal ---
async def fetch_new_pairs():
    url = f"https://api.geckoterminal.com/api/v2/networks/{NETWORK}/pools"
    params = {"include": "base_token,quote_token"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, ssl=ssl.create_default_context()) as resp:
                data = await resp.json()
                return data.get("data", [])
    except Exception as e:
        print("[ERROR] fetch_new_pairs:", e)
        return []

# --- Отправка уведомления с кнопками ---
async def send_token_alert(pool):
    token_name = pool['attributes']['base_token']['name']
    symbol = pool['attributes']['base_token']['symbol']
    liquidity = float(pool['attributes']['reserve_in_usd'] or 0)
    volume = float(pool['attributes']['volume_usd']['h1'] or 0)
    pair_url = f"https://www.geckoterminal.com/{NETWORK}/pools/{pool['id'].split('_')[-1]}"

    if liquidity < MIN_LIQUIDITY or volume < MIN_VOLUME:
        return

    key = pool['id']
    if key in sent_tokens:
        return
    sent_tokens.add(key)

    text = f"\U0001F539 <b>Новая пара:</b> {token_name} (${symbol})\n" \
           f"\n\U0001F4B0 <b>Ликвидность:</b> ${int(liquidity):,}" \
           f"\n\U0001F4CA <b>Объём (1ч):</b> ${int(volume):,}" \
           f"\n\U0001F517 <a href=\"{pair_url}\">GeckoTerminal</a>"

    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("\U0001F50D Анализ", callback_data=f"analyze|{key}"),
        InlineKeyboardButton("➕ В трекер", callback_data=f"track|{key}"),
        InlineKeyboardButton("\U0001F9E0 Контракт", callback_data=f"contract|{key}")
    )

    await bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard, parse_mode="HTML")

# --- Периодическая проверка ---
async def periodic_checker():
    while True:
        pools = await fetch_new_pairs()
        for pool in pools:
            await send_token_alert(pool)
        await asyncio.sleep(CHECK_INTERVAL)

# --- Обработка нажатий кнопок ---
@dp.callback_query_handler(lambda c: c.data.startswith("track|"))
async def handle_track(call: types.CallbackQuery):
    token_id = call.data.split("|")[1]
    await call.answer("Добавлено в трекер")
    pool_url = f"https://www.geckoterminal.com/{NETWORK}/pools/{token_id.split('_')[-1]}"
    now = datetime.now().strftime('%Y-%m-%d')
    new_row = ["auto", f"{token_id}", now, "auto-lister", 10, "x10", "", "", "", pool_url]
    sheet.append_row(new_row)

@dp.callback_query_handler(lambda c: c.data.startswith("analyze|"))
async def handle_analyze(call: types.CallbackQuery):
    await call.answer("Скоро добавим анализ 🚧", show_alert=True)

@dp.callback_query_handler(lambda c: c.data.startswith("contract|"))
async def handle_contract(call: types.CallbackQuery):
    await call.answer("Скоро добавим проверку контракта ⚠️", show_alert=True)

# --- Старт ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. Жду новые листинги...")

async def main():
    asyncio.create_task(periodic_checker())
    await dp.start_polling()

if __name__ == '__main__':
    asyncio.run(main())

# bot.py

import ssl
import asyncio
import json
import os
from datetime import datetime, timedelta

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
CHECK_INTERVAL = 20
NETWORK = "bsc"
BSCSCAN_API_KEY = os.getenv('BSCSCAN_API_KEY')

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
if not BSCSCAN_API_KEY:
    missing_env.append("BSCSCAN_API_KEY")
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
log_sheet = client.open_by_url(SPREADSHEET_URL).get_worksheet(1)  # Вторая вкладка под лог

# --- Telegram Bot ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# --- Хранилище уже проверенных токенов ---
sent_tokens = set()
pending_tokens = {}

# --- Проверка возраста токена через BscScan ---
async def is_new_token(token_address):
    try:
        url = f"https://api.bscscan.com/api"
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": token_address,
            "apikey": BSCSCAN_API_KEY
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                result = data.get("result", [])
                if not result:
                    return False
                first_tx = result[0].get("timeStamp")
                if first_tx:
                    created_time = datetime.utcfromtimestamp(int(first_tx))
                    return (datetime.utcnow() - created_time) < timedelta(hours=24)
    except Exception as e:
        print("[ERROR] BscScan contract age check:", e)
    return False

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

# --- Проверка торгового объёма ---
async def check_volume():
    now = datetime.utcnow()
    expired = []
    for key, pool in pending_tokens.items():
        first_seen = pool['timestamp']
        if now - first_seen > timedelta(minutes=2):
            expired.append(key)
            continue

        volume = float(pool['data']['attributes']['volume_usd']['h1'] or 0)
        if volume >= MIN_VOLUME:
            await send_token_alert(pool['data'])
            expired.append(key)
    for key in expired:
        del pending_tokens[key]

# --- Отправка уведомления с кнопками ---
async def send_token_alert(pool):
    attributes = pool.get('attributes', {})
    base_token = attributes.get('base_token', {})

    token_name = base_token.get('name', 'Unknown')
    symbol = base_token.get('symbol', '?')
    liquidity = float(attributes.get('reserve_in_usd', 0) or 0)
    volume = float(attributes.get('volume_usd', {}).get('h1', 0) or 0)
    pool_id = pool.get('id', 'unknown').split('_')[-1]
    token_address = base_token.get('address', 'unknown')

    gecko_url = f"https://www.geckoterminal.com/{NETWORK}/pools/{pool_id}"
    dex_url = f"https://dexscreener.com/{NETWORK}/{pool_id}"
    pancake_url = f"https://pancakeswap.finance/swap?outputCurrency={token_address}"

    key = pool.get('id')
    if key in sent_tokens:
        return
    sent_tokens.add(key)

    text = f"\U0001F539 <b>Новая пара:</b> {token_name} (${symbol})\n" \
           f"\n\U0001F4B0 <b>Ликвидность:</b> ${int(liquidity):,}" \
           f"\n\U0001F4CA <b>Объём (1ч):</b> ${int(volume):,}" \
           f"\n\U0001F517 <a href='{gecko_url}'>GeckoTerminal</a> | <a href='{dex_url}'>DexScreener</a> | <a href='{pancake_url}'>PancakeSwap</a>"

    await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row = [now, token_name, symbol, liquidity, volume, gecko_url, dex_url, pancake_url]
    sheet.append_row(row)

# --- Периодическая проверка ---
async def periodic_checker():
    while True:
        pools = await fetch_new_pairs()
        now = datetime.utcnow()
        for pool in pools:
            try:
                attributes = pool['attributes']
                liquidity = float(attributes['reserve_in_usd'] or 0)
                base_token = attributes.get('base_token', {})
                token_address = base_token.get('address')
                token_name = base_token.get('name', 'Unknown')
                symbol = base_token.get('symbol', '?')
                key = pool['id']

                log_sheet.append_row([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    token_name, symbol,
                    liquidity,
                    attributes.get('volume_usd', {}).get('h1', 0),
                    "NEW" if token_address and await is_new_token(token_address) else "OLD"
                ])

                if liquidity >= MIN_LIQUIDITY and key not in pending_tokens and key not in sent_tokens:
                    if token_address and await is_new_token(token_address):
                        pending_tokens[key] = {'data': pool, 'timestamp': now}
            except Exception as e:
                print("[ERROR] during liquidity/new token check:", e)
        await check_volume()
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
    await call.answer("Скоро добавим анализ \U0001F6A7", show_alert=True)

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

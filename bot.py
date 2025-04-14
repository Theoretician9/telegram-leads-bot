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
log_sheet = client.open_by_url(SPREADSHEET_URL).get_worksheet(1)

# --- Telegram Bot ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

sent_tokens = set()
pending_tokens = {}

# --- Поиск информации о токене в included ---
def extract_token_info(token_id, included):
    for entry in included:
        if entry['id'] == token_id and entry['type'] == 'token':
            attrs = entry.get('attributes', {})
            return {
                'name': attrs.get('name') or 'Unknown',
                'symbol': attrs.get('symbol') or '?',
                'address': attrs.get('address') or 'unknown'
            }
    return {'name': 'Unknown', 'symbol': '?', 'address': 'unknown'}

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
                return data.get("data", []), data.get("included", [])
    except Exception as e:
        print("[ERROR] fetch_new_pairs:", e)
        return [], []

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
            await send_token_alert(pool['data'], pool['token_info'])
            expired.append(key)
    for key in expired:
        del pending_tokens[key]

# --- Отправка уведомления ---
async def send_token_alert(pool, token_info):
    attributes = pool.get('attributes', {})
    token_name = token_info['name']
    symbol = token_info['symbol']
    token_address = token_info['address']

    liquidity = float(attributes.get('reserve_in_usd', 0) or 0)
    volume = float(attributes.get('volume_usd', {}).get('h1', 0) or 0)
    pool_id = pool.get('id', 'unknown').split('_')[-1]
    dex_name = attributes.get('dex_name', 'Unknown')

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
           f"\n\U0001F3E2 <b>Биржа:</b> {dex_name}" \
           f"\n\U0001F517 <a href='{gecko_url}'>GeckoTerminal</a> | <a href='{dex_url}'>DexScreener</a> | <a href='{pancake_url}'>PancakeSwap</a>"

    await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row = [now, token_name, symbol, liquidity, volume, dex_name, pool_id, gecko_url, dex_url, pancake_url]
    sheet.append_row(row)

# --- Периодическая проверка ---
async def periodic_checker():
    while True:
        pools, included = await fetch_new_pairs()
        now = datetime.utcnow()
        for pool in pools:
            try:
                attributes = pool['attributes']
                liquidity = float(attributes['reserve_in_usd'] or 0)

                token_id = pool.get('relationships', {}).get('base_token', {}).get('data', {}).get('id')
                if not token_id:
                    continue

                token_info = extract_token_info(token_id, included)
                token_address = token_info['address']
                key = pool['id']
                pool_id = key.split('_')[-1]
                dex_name = attributes.get('dex_name', 'Unknown')

                log_sheet.append_row([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    token_info['name'], token_info['symbol'],
                    liquidity,
                    attributes.get('volume_usd', {}).get('h1', 0),
                    "NEW" if token_address and await is_new_token(token_address) else "OLD",
                    dex_name,
                    pool_id
                ])

                if liquidity >= MIN_LIQUIDITY and key not in pending_tokens and key not in sent_tokens:
                    if token_address and await is_new_token(token_address):
                        pending_tokens[key] = {'data': pool, 'timestamp': now, 'token_info': token_info}
            except Exception as e:
                print("[ERROR] during liquidity/new token check:", e)
        await check_volume()
        await asyncio.sleep(CHECK_INTERVAL)

# --- Telegram команды ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. Жду новые листинги...")

# --- Main ---
async def main():
    asyncio.create_task(periodic_checker())
    await dp.start_polling()

if __name__ == '__main__':
    asyncio.run(main())

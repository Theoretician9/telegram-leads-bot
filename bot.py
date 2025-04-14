# bot.py

import ssl
import asyncio
import json
import os
from datetime import datetime, timedelta

import aiohttp
import gspread
from aiogram import Bot, Dispatcher, types
from oauth2client.service_account import ServiceAccountCredentials

# --- Константы ---
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1HiPi8UX_ekCHVDXdRxHwD3NlD2w796T2z_BjNBUj8Bg/edit"
MIN_LIQUIDITY = 5000
MIN_VOLUME = 2000
CHECK_INTERVAL = 20
NETWORKS = [
    ("bsc", "bsc"),
    ("eth", "eth"),
    ("polygon", "polygon_pos"),
    ("arbitrum", "arbitrum"),
    ("base", "base")
]
SCAN_KEYS = {
    "bsc": os.getenv("BSCSCAN_API_KEY"),
    "eth": os.getenv("ETHERSCAN_API_KEY"),
    "polygon": os.getenv("POLYGONSCAN_API_KEY"),
    "arbitrum": os.getenv("ARBISCAN_API_KEY"),
    "base": os.getenv("BASESCAN_API_KEY")
}

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

for key, value in SCAN_KEYS.items():
    if not value:
        missing_env.append(f"{key.upper()}SCAN_API_KEY")

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

sent_tokens = {net[0]: set() for net in NETWORKS}
seen_pool_ids = set()

# --- Вспомогательные функции ---
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

def extract_dex_name(pool, included):
    dex_id = pool.get('relationships', {}).get('dex', {}).get('data', {}).get('id')
    if not dex_id:
        return "Unknown"
    for entry in included:
        if entry['id'] == dex_id and entry['type'] == 'dex':
            return entry.get('attributes', {}).get('name', 'Unknown')
    return "Unknown"

async def is_new_token(network, token_address):
    try:
        key = SCAN_KEYS.get(network)
        if not key:
            return False

        domain = {
            "bsc": "bscscan.com",
            "eth": "etherscan.io",
            "polygon": "polygonscan.com",
            "arbitrum": "arbiscan.io",
            "base": "basescan.org"
        }[network]

        url = f"https://api.{domain}/api"
        params = {
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": token_address,
            "apikey": key
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
        print(f"[ERROR] {network} Scan contract age check:", e)
    return False

async def fetch_new_pairs(label, network_id, limit=50):
    url = f"https://api.geckoterminal.com/api/v2/networks/{network_id}/pools"
    params = {
        "include": "base_token,quote_token,dex",
        "per_page": limit,
        "page": 1
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, ssl=ssl.create_default_context()) as resp:
                if resp.status != 200:
                    print(f"[ERROR] {label} HTTP {resp.status}: {await resp.text()}")
                    return [], []
                data = await resp.json()
                return data.get("data", []), data.get("included", [])
    except Exception as e:
        print(f"[ERROR] fetch_new_pairs ({label}):", e)
        return [], []

async def debug_stats(network, total, passed_liquidity, passed_new):
    print(f"[{network.upper()}] Total pairs: {total}, Passed liquidity: {passed_liquidity}, New: {passed_new}")

async def periodic_checker():
    while True:
        for idx, (label, network_id) in enumerate(NETWORKS):
            pools, included = await fetch_new_pairs(label, network_id, limit=50)
            await asyncio.sleep(1.5 + idx * 0.7)

            now = datetime.utcnow()
            total = len(pools)
            passed_liquidity = 0
            passed_new = 0

            for pool in pools:
                try:
                    pool_id = pool['id']
                    if pool_id in seen_pool_ids:
                        continue
                    seen_pool_ids.add(pool_id)

                    attributes = pool['attributes']
                    liquidity = float(attributes['reserve_in_usd'] or 0)
                    if liquidity < MIN_LIQUIDITY:
                        continue
                    passed_liquidity += 1

                    base_token_id = pool.get('relationships', {}).get('base_token', {}).get('data', {}).get('id')
                    quote_token_id = pool.get('relationships', {}).get('quote_token', {}).get('data', {}).get('id')
                    if not base_token_id or not quote_token_id:
                        continue

                    base_info = extract_token_info(base_token_id, included)
                    quote_info = extract_token_info(quote_token_id, included)

                    is_new = base_info['address'] and await is_new_token(label, base_info['address'])
                    if is_new:
                        passed_new += 1

                        log_sheet.append_row([
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            base_info['name'], base_info['symbol'],
                            quote_info['name'], quote_info['symbol'],
                            liquidity,
                            attributes.get('volume_usd', {}).get('h1', 0),
                            "NEW",
                            base_info['address'],
                            extract_dex_name(pool, included)
                        ])
                except Exception as e:
                    print(f"[ERROR] {label} pair check:", e)

            await debug_stats(label, total, passed_liquidity, passed_new)
        await asyncio.sleep(CHECK_INTERVAL)

@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await message.answer("Бот запущен. Жду новые листинги...")

async def main():
    asyncio.create_task(periodic_checker())
    await dp.start_polling()

if __name__ == '__main__':
    asyncio.run(main())

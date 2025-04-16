# token_alert_bot.py

import asyncio
import websockets
import json
from datetime import datetime, timedelta
import aiohttp
from dotenv import load_dotenv
import os
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram import F

load_dotenv()

# Подключаемые WSS-ссылки по сетям
NETWORKS = {
    'bsc': os.getenv('WSS_BSC'),
    'eth': os.getenv('WSS_ETH'),
    'polygon': os.getenv('WSS_POLYGON'),
    'arbitrum': os.getenv('WSS_ARBITRUM'),
    'base': os.getenv('WSS_BASE')
}

# DEX Router адреса для отслеживания ликвидности
DEX_ADDRESSES = {
    'bsc': [
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",
        "0xc9b085d878e28fa776b1e269595f65726b000039",
        "0x05ff2b0db69458a0750badebc4f9e13add608c7f"
    ],
    'polygon': [
        "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff",
        "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"
    ],
    'eth': [
        "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "0xE592427A0AEce92De3Edee1F18E0157C05861564"
    ],
    'arbitrum': [
        "0x1f98431c8ad98523631ae4a59f267346ea31f984",
        "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
    ],
    'base': [
        "0x420dd381b31aef6683fa7eebdd3f1f5e78c82cb9",
        "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86"
    ]
}

# Telegram уведомления
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Redis клиент
REDIS_URL = os.getenv("REDIS_URL")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
PENDING_TTL = 72 * 3600

async def record_deploy(address):
    print(f"[REDIS] saving pending: {address}")
    await redis_client.setex(f"pending:{address.lower()}", PENDING_TTL, datetime.utcnow().isoformat())

async def is_pending(address):
    return await redis_client.exists(f"pending:{address.lower()}")

async def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

@dp.message(CommandStart())
async def handle_start(message: Message):
    await message.answer("Бот успешно активирован. Ожидаю новые токены 🚀")

async def handle_event(chain, tx):
    from_address = tx['from']
    to_address = tx.get('to')

    if to_address is None:
        contract = tx['hash']
        print(f"[{chain.upper()}] 🚀 POSSIBLE TOKEN DEPLOYMENT: {contract}")
        await record_deploy(contract)
        return

    from_lower = from_address.lower()
    if await is_pending(from_lower) and to_address and to_address.lower() in DEX_ADDRESSES.get(chain, []):
        print(f"[{chain.upper()}] ✅ Sending NEW LISTING alert for {from_address}")
        print(f"[{chain.upper()}] ⬆️ Sending to Telegram: Token {from_address} to DEX {to_address}")
        await send_telegram(
            f"[{chain.upper()}] 📣 *NEW LISTING!*\n"
            f"Token: `{from_address}`\n"
            f"DEX: `{to_address}`"
        )

async def listen(chain, url):
    reconnect_delay = 5
    max_retries = 5
    while True:
        for attempt in range(1, max_retries + 1):
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    max_queue=None
                ) as ws:
                    subscribe = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": ["newPendingTransactions"]
                    }
                    await ws.send(json.dumps(subscribe))
                    print(f"[{chain.upper()}] Connected to WebSocket")
                    reconnect_delay = 5

                    while True:
                        try:
                            print(f"[{chain.upper()}] 🟢 Waiting for tx...")
                            message = await ws.recv()
                            data = json.loads(message)
                            if 'params' in data:
                                tx_hash = data['params']['result']
                                async with aiohttp.ClientSession() as session:
                                    async with session.post(NETWORKS[chain].replace('wss://', 'https://'), json={
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "eth_getTransactionByHash",
                                        "params": [tx_hash]
                                    }) as resp:
                                        tx_data = await resp.json()
                                        tx = tx_data.get("result")
                                        if tx:
                                            await handle_event(chain, tx)
                        except Exception as inner_e:
                            print(f"[{chain.upper()}] ⚠️ Inner error: {type(inner_e).__name__}: {inner_e}")
                            await asyncio.sleep(3)
            except Exception as outer_e:
                print(f"[{chain.upper()}] 🔁 Reconnecting WebSocket (attempt {attempt}/{max_retries}) due to error: {type(outer_e).__name__}: {outer_e}")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
        print(f"[{chain.upper()}] ❌ Max retries reached. Waiting before retrying...")
        await asyncio.sleep(120)

async def main():
    listeners = []
    for i, (chain, url) in enumerate(NETWORKS.items()):
        if url:
            listeners.append(asyncio.create_task(listen(chain, url)))
            await asyncio.sleep(1)
    await dp.start_polling(bot)
    await asyncio.gather(*listeners)

if __name__ == '__main__':
    asyncio.run(main())

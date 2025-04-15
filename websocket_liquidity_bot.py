# token_alert_bot.py

import asyncio
import websockets
import json
from datetime import datetime, timedelta
import aiohttp
from dotenv import load_dotenv
import os
from aiogram import Bot
import random

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
    'bsc': ["0x10ed43c718714eb63d5aa57b78b54704e256024e", "0xc9b085d878e28fa776b1e269595f65726b000039"],
    'polygon': ["0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff"],
    'eth': [],
    'arbitrum': [],
    'base': []
}

# Telegram уведомления
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=BOT_TOKEN)

# Временное хранилище новых токенов и pending токенов
pending_tokens = {}
PENDING_TTL = timedelta(hours=72)  # Храним токены до 72 часов


def record_deploy(address):
    pending_tokens[address] = datetime.utcnow()

def cleanup_pending():
    now = datetime.utcnow()
    expired = [addr for addr, ts in pending_tokens.items() if now - ts > PENDING_TTL]
    for addr in expired:
        del pending_tokens[addr]

async def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

async def handle_event(chain, tx):
    from_address = tx['from']
    to_address = tx.get('to')

    cleanup_pending()

    # Проверка на создание контракта (деплой)
    if to_address is None:
        contract = tx['hash']
        print(f"[{chain.upper()}] 🚀 POSSIBLE TOKEN DEPLOYMENT: {contract}")
        record_deploy(contract.lower())
        return

    # Проверка на добавление ликвидности только для ранее задеплоенных токенов
    from_lower = from_address.lower()
    if from_lower in pending_tokens and to_address and to_address.lower() in DEX_ADDRESSES.get(chain, []):
        print(f"[{chain.upper()}] 📣 NEW LISTING: {from_address} to DEX: {to_address}")
        await send_telegram(
            f"[{chain.upper()}] 📣 *NEW LISTING!*\nToken: `{from_address}`\nDEX: `{to_address}`"
        )
        del pending_tokens[from_lower]


async def listen(chain, url):
    reconnect_attempt = 0
    while True:
        try:
            async with websockets.connect(url, ping_interval=60, ping_timeout=30, max_queue=None) as ws:
                subscribe = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["newPendingTransactions"]
                }
                await ws.send(json.dumps(subscribe))
                print(f"[{chain.upper()}] Connected to WebSocket")
                reconnect_attempt = 0

                while True:
                    try:
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
            reconnect_attempt += 1
            delay = min(5 * reconnect_attempt + random.randint(1, 5), 60)
            print(f"[{chain.upper()}] 🔁 Reconnecting WebSocket in {delay}s due to error: {type(outer_e).__name__}: {outer_e}")
            await asyncio.sleep(delay)


async def main():
    tasks = [listen(chain, url) for chain, url in NETWORKS.items() if url]
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())

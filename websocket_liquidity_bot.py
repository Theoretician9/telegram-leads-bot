# token_alert_bot.py

import asyncio
import websockets
import json
from datetime import datetime, timedelta
import aiohttp
from dotenv import load_dotenv
import os

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

# Временное хранилище новых токенов и pending токенов
new_tokens = {}
pending_tokens = {}
PENDING_TTL = timedelta(minutes=180)


def is_recent(address):
    created = new_tokens.get(address)
    if not created:
        return False
    return (datetime.utcnow() - created).total_seconds() < 600  # 10 минут


def record_deploy(address):
    new_tokens[address] = datetime.utcnow()
    pending_tokens[address] = datetime.utcnow()


def cleanup_pending():
    now = datetime.utcnow()
    expired = [addr for addr, ts in pending_tokens.items() if now - ts > PENDING_TTL]
    for addr in expired:
        del pending_tokens[addr]


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

    # Проверка на добавление ликвидности
    if to_address.lower() in DEX_ADDRESSES.get(chain, []):
        from_lower = from_address.lower()
        if from_lower in pending_tokens:
            print(f"[{chain.upper()}] 📣 NEW LISTING: {from_address} to DEX: {to_address}")
            del pending_tokens[from_lower]
        elif is_recent(from_lower):
            print(f"[{chain.upper()}] ✅ NEW LISTING EVENT! Token: {from_address} to DEX: {to_address}")
        else:
            print(f"[{chain.upper()}] 💧 POSSIBLE LIQUIDITY EVENT: {from_address} → {to_address}")


async def listen(chain, url):
    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                subscribe = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["newPendingTransactions"]
                }
                await ws.send(json.dumps(subscribe))
                print(f"[{chain.upper()}] Connected to WebSocket")

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
                        print(f"[{chain.upper()}] Inner error: {inner_e}")
                        await asyncio.sleep(2)
        except Exception as outer_e:
            print(f"[{chain.upper()}] Reconnecting due to error: {outer_e}")
            await asyncio.sleep(10)


async def main():
    tasks = [listen(chain, url) for chain, url in NETWORKS.items() if url]
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())

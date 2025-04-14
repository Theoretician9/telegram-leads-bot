# websocket_listener.py

import asyncio
import json
import os
import websockets
from datetime import datetime
import aiohttp

# --- Сети и их WSS-адреса из переменных окружения ---
NETWORKS = {
    'bsc': os.getenv('WSS_BSC'),
    'eth': os.getenv('WSS_ETH'),
    'polygon': os.getenv('WSS_POLYGON'),
    'arbitrum': os.getenv('WSS_ARBITRUM'),
    'base': os.getenv('WSS_BASE')
}

# --- Подключение к RPC HTTP для получения подробной информации ---
RPC_HTTP = {
    'bsc': NETWORKS['bsc'].replace('wss://', 'https://'),
    'eth': NETWORKS['eth'].replace('wss://', 'https://'),
    'polygon': NETWORKS['polygon'].replace('wss://', 'https://'),
    'arbitrum': NETWORKS['arbitrum'].replace('wss://', 'https://'),
    'base': NETWORKS['base'].replace('wss://', 'https://'),
}

# --- Подписка и обработка ---
async def listen_pending_transactions(network, wss_url):
    async with websockets.connect(wss_url) as ws:
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": ["newPendingTransactions"]
        }
        await ws.send(json.dumps(subscribe_msg))
        print(f"[{network.upper()}] Subscribed to pending transactions")

        while True:
            try:
                message = await ws.recv()
                data = json.loads(message)
                if 'params' in data and 'result' in data['params']:
                    tx_hash = data['params']['result']
                    asyncio.create_task(process_tx_hash(network, tx_hash))
            except Exception as e:
                print(f"[{network.upper()}] Error: {e}")
                break

# --- Получение полной информации по хэшу ---
async def process_tx_hash(network, tx_hash):
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getTransactionByHash",
            "params": [tx_hash],
            "id": 1
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(RPC_HTTP[network], json=payload) as resp:
                response = await resp.json()
                tx = response.get("result")

                if not tx:
                    return

                to_address = tx.get("to")
                input_data = tx.get("input")

                # --- Фильтрация потенциальных листингов ---
                if not to_address and input_data and input_data != '0x':
                    print(f"[{network.upper()}] 🚀 POSSIBLE TOKEN DEPLOYMENT: {tx_hash}")

                # Возможная ликвидность/листинг — добавление ликвидности или вызов swap
                elif input_data and is_add_liquidity_or_swap(input_data):
                    print(f"[{network.upper()}] 💧 POSSIBLE LIQUIDITY EVENT: {tx_hash} → {to_address}")

    except Exception as e:
        print(f"[{network.upper()}] Error while processing tx: {e}")

# --- Простая эвристика по сигнатурам функций ---
def is_add_liquidity_or_swap(input_data):
    common_prefixes = [
        "0xf305d719",  # addLiquidityETH(address,...)
        "0xe8e33700",  # addLiquidity(address,address,...)
        "0x38ed1739",  # swapExactTokensForTokens
        "0x18cbafe5",  # swapExactETHForTokens
        "0x8803dbee",  # createPair(address,address) — Uniswap V2 factory
    ]
    return any(input_data.startswith(sig) for sig in common_prefixes)

# --- Запуск всех слушателей ---
async def main():
    tasks = [listen_pending_transactions(net, url) for net, url in NETWORKS.items() if url]
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    asyncio.run(main())

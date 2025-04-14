import asyncio
import json
import os
from datetime import datetime, timedelta

import aiohttp
import websockets
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread
from aiogram import Bot

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_ADMIN_ID")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDS"))

bot = Bot(token=TELEGRAM_TOKEN)

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=scope)
client = gspread.authorize(creds)
sheet = client.open_by_url(SPREADSHEET_URL).sheet1

wss_urls = {
    'bsc': os.getenv("BSC_WSS"),
    'eth': os.getenv("ETH_WSS"),
    'polygon': os.getenv("POLYGON_WSS")
}

pending_tokens = {}
checked_liquidity = set()

async def send_alert(msg: str):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

def log_to_sheet(network, address, event_type):
    try:
        sheet.append_row([
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            network.upper(),
            address,
            event_type
        ])
    except Exception as e:
        print(f"[ERROR] Writing to sheet: {e}")

async def cleanup_pending_tokens():
    while True:
        now = datetime.utcnow()
        expired = [token for token, ts in pending_tokens.items() if now - ts > timedelta(minutes=180)]
        for token in expired:
            del pending_tokens[token]
        await asyncio.sleep(300)

async def process_event(network, event):
    try:
        tx = json.loads(event["params"]["result"])
        to = tx.get("to", "")
        input_data = tx.get("input", "")

        if input_data.startswith("0x60806040"):
            token_address = tx["hash"][-40:]
            print(f"[{network.upper()}] 🚀 POSSIBLE TOKEN DEPLOYMENT: {token_address}")
            pending_tokens[token_address] = datetime.utcnow()
            log_to_sheet(network, token_address, "DEPLOY")

        elif input_data.startswith("0xf305d719") or input_data.startswith("0xe8e33700"):
            token_address = tx["to"]
            if token_address in checked_liquidity:
                return
            checked_liquidity.add(token_address)
            print(f"[{network.upper()}] 💧 POSSIBLE LIQUIDITY EVENT: {tx['hash']} → {token_address}")
            log_to_sheet(network, token_address, "LIQUIDITY")

            # Check for deployment match
            for pending_token, timestamp in list(pending_tokens.items()):
                if pending_token.lower() in input_data.lower():
                    print(f"[{network.upper()}] 🔥 NEW LISTING DETECTED: {pending_token}")
                    log_to_sheet(network, pending_token, "NEW LISTING")
                    await send_alert(f"[{network.upper()}] 🔥 NEW LISTING\n{pending_token}")
                    del pending_tokens[pending_token]

    except Exception as e:
        print(f"[ERROR] processing {network}: {e}")

async def listen_to_network(network, url):
    while True:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({
                    "method": "eth_subscribe",
                    "params": ["newPendingTransactions"],
                    "id": 1,
                    "jsonrpc": "2.0"
                }))
                while True:
                    message = await ws.recv()
                    data = json.loads(message)
                    if "params" in data:
                        await process_event(network, data)
        except Exception as e:
            print(f"[ERROR] {network} listener: {e}")
            await asyncio.sleep(5)

async def main():
    tasks = [listen_to_network(net, url) for net, url in wss_urls.items() if url]
    tasks.append(cleanup_pending_tokens())
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
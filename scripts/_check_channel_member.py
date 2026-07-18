#!/usr/bin/env python3
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
from config.settings import ADVERT_CHANNEL_ID, BOT_TOKEN
from telegram import Bot


async def main():
    uid = int(sys.argv[1])
    bot = Bot(BOT_TOKEN)
    cid = int(ADVERT_CHANNEL_ID)
    try:
        m = await bot.get_chat_member(cid, uid)
        print(f"channel_member status={m.status}")
    except Exception as e:
        print(f"channel_member ERROR: {e}")
    await bot.close()


if __name__ == "__main__":
    asyncio.run(main())

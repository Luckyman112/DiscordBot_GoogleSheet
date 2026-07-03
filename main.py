import asyncio

from config import TOKEN, bot

EXTENSIONS = [
    "cogs.registration",
    "cogs.reports",
    "cogs.management",
]


async def main():
    async with bot:
        for extension in EXTENSIONS:
            await bot.load_extension(extension)
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

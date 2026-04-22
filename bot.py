import discord
from discord.ext import commands
import asyncio
import os
from db.connection import init_db, close_db
from db.migrate import migrate

COGS = [
    "cogs.setup",
    "cogs.menu",
    "cogs.businesses",
    "cogs.stocks",
]

class EconBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        await migrate()
        for cog in COGS:
            await self.load_extension(cog)
        await self.tree.sync()
        print(f"Synced slash commands.")

    async def on_ready(self):
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Faith in God & the Economy."
            )
        )
        print(f"Logged in as {self.user} (ID: {self.user.id})")

    async def close(self):
        await close_db()
        await super().close()

async def main():
    token = os.environ["DISCORD_TOKEN"]
    bot = EconBot()
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())

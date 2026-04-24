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
    "cogs.casino",
]

class EconBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True  # Required to read message.content in on_message
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await init_db()
        await migrate()
        for cog in COGS:
            await self.load_extension(cog)
        # Sync globally (can take up to 1 hour to propagate on first run).
        # To get commands instantly during development, set GUILD_ID in your environment.
        guild_id = os.environ.get("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Synced slash commands to guild {guild_id} (instant).")
        else:
            await self.tree.sync()
            print(f"Synced slash commands globally (may take up to 1 hour).")

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

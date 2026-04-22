import discord
import os
from db.connection import get_pool

BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))

# ── Permission helpers ────────────────────────────────────────────────────────

async def is_admin(interaction: discord.Interaction) -> bool:
    """Returns True if the user is the bot owner or has the guild's admin role."""
    if interaction.user.id == BOT_OWNER_ID:
        return True
    if interaction.user.guild_permissions.administrator:
        return True
    pool = get_pool()
    # Ensure the guild row exists before querying it
    await pool.execute(
        "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
        interaction.guild_id
    )
    row = await pool.fetchrow(
        "SELECT admin_role_id FROM guilds WHERE guild_id = $1",
        interaction.guild_id
    )
    if row and row["admin_role_id"]:
        role = interaction.guild.get_role(row["admin_role_id"])
        if role and role in interaction.user.roles:
            return True
    return False

async def admin_check(interaction: discord.Interaction) -> bool:
    """Use as an app_commands.check. Sends an error if not admin."""
    if await is_admin(interaction):
        return True
    await interaction.response.send_message(
        "You don't have permission to use this command.", ephemeral=True
    )
    return False

# ── Guild helpers ─────────────────────────────────────────────────────────────

async def ensure_guild(guild_id: int):
    """Inserts guild row if it doesn't exist yet."""
    pool = get_pool()
    await pool.execute(
        "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
        guild_id
    )

async def get_guild(guild_id: int):
    pool = get_pool()
    return await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", guild_id)

# ── Embed builder ─────────────────────────────────────────────────────────────

def styled_embed(title: str, description: str = "", color: int = 0x1a1a2e) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Economy System")
    return embed

ACCENT = 0x00d4aa   # teal accent
DANGER = 0xe63946   # red
WARNING = 0xf4a261  # orange
SUCCESS = 0x2a9d8f  # green

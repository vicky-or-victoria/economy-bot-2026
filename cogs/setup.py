import discord
from discord import app_commands
from discord.ext import commands
from utils.helpers import admin_check, ensure_guild, get_guild, styled_embed, ACCENT, SUCCESS, DANGER
from db.connection import get_pool


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /setup_role ───────────────────────────────────────────────────────────

    @app_commands.command(name="setup_role", description="Set the admin role for this server's economy bot.")
    @app_commands.describe(role="The role to grant economy admin permissions")
    async def setup_role(self, interaction: discord.Interaction, role: discord.Role):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        await pool.execute(
            "UPDATE guilds SET admin_role_id = $1 WHERE guild_id = $2",
            role.id, interaction.guild_id
        )
        embed = styled_embed(
            "Admin Role Set",
            f"**{role.name}** has been granted economy admin permissions.\n\n"
            f"Members with this role can now use admin commands.",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setup_currency ───────────────────────────────────────────────────────

    @app_commands.command(name="setup_currency", description="Configure this server's currency name, symbol, and USD rate.")
    @app_commands.describe(
        name="Currency name (e.g. Credits, Gold, Dollars)",
        symbol="Currency symbol (e.g. C, G, $)",
        usd_rate="How many of this currency equals 1 USD"
    )
    async def setup_currency(
        self,
        interaction: discord.Interaction,
        name: str,
        symbol: str,
        usd_rate: float
    ):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        await pool.execute(
            """UPDATE guilds
               SET currency_name = $1, currency_symbol = $2, usd_rate = $3
               WHERE guild_id = $4""",
            name, symbol, usd_rate, interaction.guild_id
        )
        embed = styled_embed(
            "Currency Configured",
            f"**Name:** {name}\n"
            f"**Symbol:** {symbol}\n"
            f"**USD Rate:** 1 USD = {usd_rate} {name}",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /set_usd_rate ─────────────────────────────────────────────────────────

    @app_commands.command(name="set_usd_rate", description="Update the live USD conversion rate for this server's currency.")
    @app_commands.describe(rate="How many of this server's currency equals 1 USD")
    async def set_usd_rate(self, interaction: discord.Interaction, rate: float):
        if not await admin_check(interaction):
            return
        if rate <= 0:
            await interaction.response.send_message("Rate must be greater than 0.", ephemeral=True)
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        await pool.execute(
            "UPDATE guilds SET usd_rate = $1 WHERE guild_id = $2",
            rate, interaction.guild_id
        )
        row = await get_guild(interaction.guild_id)
        embed = styled_embed(
            "USD Rate Updated",
            f"1 USD is now worth **{rate} {row['currency_name']}**.",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setup_menu_channel ───────────────────────────────────────────────────

    @app_commands.command(name="setup_menu_channel", description="Set the channel where the player menu will be posted.")
    @app_commands.describe(channel="The channel to post the player menu in")
    async def setup_menu_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        await pool.execute(
            "UPDATE guilds SET menu_channel_id = $1 WHERE guild_id = $2",
            channel.id, interaction.guild_id
        )
        embed = styled_embed(
            "Menu Channel Set",
            f"The player menu will be posted in {channel.mention}.\n\n"
            f"Use `/post_menu` to post or refresh the menu.",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setup_stock_channel ──────────────────────────────────────────────────

    @app_commands.command(name="setup_stock_channel", description="Set the channel for the live stock market board.")
    @app_commands.describe(channel="The channel to post the stock market in")
    async def setup_stock_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        await pool.execute(
            "UPDATE guilds SET stock_channel_id = $1 WHERE guild_id = $2",
            channel.id, interaction.guild_id
        )
        embed = styled_embed(
            "Stock Market Channel Set",
            f"The stock market board will be posted in {channel.mention}.\n\n"
            f"Use `/post_stockmarket` to post or refresh the board.",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setup_business_channel ───────────────────────────────────────────────

    @app_commands.command(name="setup_business_channel", description="Set the channel where business posts will be created.")
    @app_commands.describe(channel="The channel where business posts will appear")
    async def setup_business_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        await pool.execute(
            "UPDATE guilds SET business_channel_id = $1 WHERE guild_id = $2",
            channel.id, interaction.guild_id
        )
        embed = styled_embed(
            "Business Channel Set",
            f"New business posts will appear in {channel.mention}.",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /server_config ────────────────────────────────────────────────────────

    @app_commands.command(name="server_config", description="View this server's current economy configuration.")
    async def server_config(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        row = await get_guild(interaction.guild_id)

        def fmt_channel(cid):
            if not cid:
                return "Not set"
            ch = interaction.guild.get_channel(cid)
            return ch.mention if ch else f"Unknown ({cid})"

        def fmt_role(rid):
            if not rid:
                return "Not set"
            r = interaction.guild.get_role(rid)
            return r.mention if r else f"Unknown ({rid})"

        embed = styled_embed(
            "Server Configuration",
            color=ACCENT
        )
        embed.add_field(name="Admin Role", value=fmt_role(row["admin_role_id"]), inline=True)
        embed.add_field(name="Currency", value=f"{row['currency_name']} ({row['currency_symbol']})", inline=True)
        embed.add_field(name="USD Rate", value=f"1 USD = {row['usd_rate']} {row['currency_name']}", inline=True)
        embed.add_field(name="Menu Channel", value=fmt_channel(row["menu_channel_id"]), inline=True)
        embed.add_field(name="Stock Channel", value=fmt_channel(row["stock_channel_id"]), inline=True)
        embed.add_field(name="Business Channel", value=fmt_channel(row["business_channel_id"]), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))

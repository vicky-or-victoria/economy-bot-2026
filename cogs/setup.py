import traceback
import discord
from discord import app_commands
from discord.ext import commands
from utils.helpers import is_admin, ensure_guild, get_guild, styled_embed, styled_embed_formal, ACCENT, SUCCESS, DANGER, WARNING
from db.connection import get_pool


def _no_perm():
    return "You don't have permission to use this command."


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /setup_role ───────────────────────────────────────────────────────────

    @app_commands.command(name="setup_role", description="Set the admin role for this server's economy bot.")
    @app_commands.describe(role="The role to grant economy admin permissions")
    async def setup_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute(
                "UPDATE guilds SET admin_role_id = $1 WHERE guild_id = $2",
                role.id, interaction.guild_id
            )
            await interaction.followup.send(embed=styled_embed(
                "Admin Role Set",
                f"**{role.name}** has been granted economy admin permissions.\n\n"
                f"Members with this role can now use admin commands.",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /setup_currency ───────────────────────────────────────────────────────

    @app_commands.command(name="setup_currency", description="Configure this server's currency name, symbol, and USD rate.")
    @app_commands.describe(
        name="Currency name (e.g. Credits, Gold, Dollars)",
        symbol="Currency symbol (e.g. C, G, $)",
        usd_rate="How many of this currency equals 1 USD"
    )
    async def setup_currency(self, interaction: discord.Interaction, name: str, symbol: str, usd_rate: float):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute(
                "UPDATE guilds SET currency_name = $1, currency_symbol = $2, usd_rate = $3 WHERE guild_id = $4",
                name, symbol, usd_rate, interaction.guild_id
            )
            await interaction.followup.send(embed=styled_embed(
                "Currency Configured",
                f"**Name:** {name}\n**Symbol:** {symbol}\n**USD Rate:** 1 USD = {usd_rate} {name}",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /set_usd_rate ─────────────────────────────────────────────────────────

    @app_commands.command(name="set_usd_rate", description="Update the live USD conversion rate for this server's currency.")
    @app_commands.describe(rate="How many of this server's currency equals 1 USD")
    async def set_usd_rate(self, interaction: discord.Interaction, rate: float):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            if rate <= 0:
                await interaction.followup.send("Rate must be greater than 0.", ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute("UPDATE guilds SET usd_rate = $1 WHERE guild_id = $2", rate, interaction.guild_id)
            row = await get_guild(interaction.guild_id)
            await interaction.followup.send(embed=styled_embed(
                "USD Rate Updated",
                f"1 USD is now worth **{rate} {row['currency_name']}**.",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /setup_menu_channel ───────────────────────────────────────────────────

    @app_commands.command(name="setup_menu_channel", description="Set the channel where the player menu will be posted.")
    @app_commands.describe(channel="The channel to post the player menu in")
    async def setup_menu_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute("UPDATE guilds SET menu_channel_id = $1 WHERE guild_id = $2", channel.id, interaction.guild_id)
            await interaction.followup.send(embed=styled_embed(
                "Menu Channel Set",
                f"The player menu will be posted in {channel.mention}.\n\nUse `/post_menu` to post or refresh the menu.",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /setup_stock_channel ──────────────────────────────────────────────────

    @app_commands.command(name="setup_stock_channel", description="Set the channel for the live stock market board.")
    @app_commands.describe(channel="The channel to post the stock market in")
    async def setup_stock_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute("UPDATE guilds SET stock_channel_id = $1 WHERE guild_id = $2", channel.id, interaction.guild_id)
            await interaction.followup.send(embed=styled_embed(
                "Stock Market Channel Set",
                f"The stock market board will be posted in {channel.mention}.\n\nUse `/post_stockmarket` to post or refresh the board.",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /setup_review_channel ─────────────────────────────────────────────────

    @app_commands.command(name="setup_review_channel", description="Set the channel where business applications and expansion proposals are sent for review.")
    @app_commands.describe(channel="The text channel admins will use to review submissions")
    async def setup_review_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute("UPDATE guilds SET review_channel_id = $1 WHERE guild_id = $2", channel.id, interaction.guild_id)
            await interaction.followup.send(embed=styled_embed(
                "Review Channel Set",
                f"New business applications and expansion proposals will be posted in {channel.mention} for admin review.\n\n"
                f"Each submission will appear as an embed with **Approve / Reject** buttons directly in that channel.",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /setup_business_channel ───────────────────────────────────────────────

    @app_commands.command(name="setup_business_channel", description="Set the forum channel where business threads will be created.")
    @app_commands.describe(channel="A Forum Channel — each approved business gets its own thread here")
    async def setup_business_channel(self, interaction: discord.Interaction, channel: discord.ForumChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            await pool.execute("UPDATE guilds SET business_channel_id = $1 WHERE guild_id = $2", channel.id, interaction.guild_id)
            await interaction.followup.send(embed=styled_embed(
                "Business Forum Channel Set",
                f"Approved businesses will get their own thread in {channel.mention}.\n\n"
                f"Make sure this is a **Forum Channel** — each business will appear as a separate post.",
                color=SUCCESS
            ), ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /server_config ────────────────────────────────────────────────────────

    @app_commands.command(name="server_config", description="View this server's current economy configuration.")
    async def server_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
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

            embed = styled_embed("Server Configuration", color=ACCENT)
            embed.add_field(name="Admin Role",       value=fmt_role(row["admin_role_id"]),             inline=True)
            embed.add_field(name="Currency",         value=f"{row['currency_name']} ({row['currency_symbol']})", inline=True)
            embed.add_field(name="USD Rate",         value=f"1 USD = {row['usd_rate']} {row['currency_name']}", inline=True)
            embed.add_field(name="Menu Channel",     value=fmt_channel(row["menu_channel_id"]),        inline=True)
            embed.add_field(name="Stock Channel",    value=fmt_channel(row["stock_channel_id"]),       inline=True)
            embed.add_field(name="Business Channel", value=fmt_channel(row["business_channel_id"]),    inline=True)
            embed.add_field(name="Review Channel",   value=fmt_channel(row.get("review_channel_id")),  inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

    # ── /post_pending_reviews ─────────────────────────────────────────────────

    @app_commands.command(
        name="post_pending_reviews",
        description="Post all pending business applications and expansion proposals into the review channel."
    )
    async def post_pending_reviews(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if not await is_admin(interaction):
                await interaction.followup.send(_no_perm(), ephemeral=True)
                return
            await ensure_guild(interaction.guild_id)
            pool = get_pool()
            guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)

            review_channel_id = guild_row.get("review_channel_id") if guild_row else None
            if not review_channel_id:
                await interaction.followup.send(
                    "G.R.E.T.A. has no review channel configured. Use `/setup_review_channel` to assign one.", ephemeral=True
                )
                return

            review_channel = interaction.guild.get_channel(review_channel_id)
            if not review_channel:
                await interaction.followup.send(
                    "Review channel not found — it may have been deleted. Use `/setup_review_channel` to set a new one.",
                    ephemeral=True
                )
                return

            sym = guild_row["currency_symbol"]
            admin_role_id = guild_row.get("admin_role_id")
            role_mention = f"<@&{admin_role_id}>" if admin_role_id else None

            from cogs.businesses import ReviewView, ExpansionReviewView

            # ── Pending business applications ──
            apps = await pool.fetch(
                "SELECT * FROM business_applications WHERE guild_id = $1 AND status = 'pending' ORDER BY created_at",
                interaction.guild_id
            )
            app_count = 0
            for app in apps:
                try:
                    embed = styled_embed(
                        f"📋 Business Application #{app['id']}",
                        f"**{app['name']}** by <@{app['owner_id']}>\n"
                        f"**Industry:** {app['industry']}\n\n"
                        f"{app['description']}",
                        color=WARNING
                    )
                    await review_channel.send(content=role_mention, embed=embed, view=ReviewView(app["id"], self.bot))
                    app_count += 1
                except Exception:
                    traceback.print_exc()

            # ── Pending expansion proposals ──
            expansions = await pool.fetch(
                """SELECT ep.*, b.name AS business_name, b.industry AS business_industry
                   FROM expansion_proposals ep
                   JOIN businesses b ON b.id = ep.business_id
                   WHERE ep.guild_id = $1 AND ep.status = 'pending'
                   ORDER BY ep.created_at""",
                interaction.guild_id
            )
            exp_count = 0
            for exp in expansions:
                try:
                    embed = styled_embed(
                        f"🏗️ Expansion Proposal #{exp['id']}",
                        f"**{exp['business_name']}** by <@{exp['owner_id']}>\n"
                        f"**Industry:** {exp['business_industry']}\n\n"
                        f"**Proposal:** {exp['title']}\n"
                        f"**Est. Revenue Increase:** {sym}{exp['estimated_revenue']:,.2f}/day\n\n"
                        f"{exp['description']}",
                        color=WARNING
                    )
                    await review_channel.send(content=role_mention, embed=embed, view=ExpansionReviewView(exp["id"]))
                    exp_count += 1
                except Exception:
                    traceback.print_exc()

            total = app_count + exp_count
            if total == 0:
                await interaction.followup.send("No pending applications or proposals to post.", ephemeral=True)
            else:
                await interaction.followup.send(embed=styled_embed(
                    "Pending Reviews Posted",
                    f"Posted **{app_count}** business application(s) and "
                    f"**{exp_count}** expansion proposal(s) to {review_channel.mention}.",
                    color=SUCCESS
                ), ephemeral=True)

        except Exception as e:
            traceback.print_exc()
            await interaction.followup.send(f"Error: `{e}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))

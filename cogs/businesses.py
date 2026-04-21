import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from utils.helpers import admin_check, ensure_guild, styled_embed, ACCENT, SUCCESS, DANGER, WARNING
from utils.graphs import generate_business_chart
from db.queries.businesses import (
    get_pending_applications, get_application, approve_application,
    reject_application, get_business, update_business_message,
    update_daily, set_business_public
)
from db.queries.stocks import (
    create_stock, get_stock_by_ticker, get_stock_by_business,
    get_price_history, complete_ipo
)
from db.queries.wallets import add_cash
from db.connection import get_pool

DAILY_COOLDOWN_HOURS = 20
DAILY_REWARD = 500.0

# IPO: owner sets a price and the stock goes live
IPO_MIN_PRICE = 1.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _forum_thread_name(member: discord.Member | None, username: str, business_name: str) -> str:
    display = member.display_name if member else username
    return f"{display} — {business_name}"


# ── Business post persistent view ─────────────────────────────────────────────

class BusinessPostView(discord.ui.View):
    def __init__(self, business_id: int):
        super().__init__(timeout=None)
        self.business_id = business_id
        self.daily_button.custom_id = f"biz:daily:{business_id}"
        self.stats_button.custom_id  = f"biz:stats:{business_id}"
        self.ipo_button.custom_id    = f"biz:ipo:{business_id}"

    @discord.ui.button(label="Daily Claim", style=discord.ButtonStyle.success, custom_id="biz:daily:0")
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_daily(interaction, self.business_id)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.secondary, custom_id="biz:stats:0")
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_stats(interaction, self.business_id)

    @discord.ui.button(label="Go Public (IPO)", style=discord.ButtonStyle.primary, custom_id="biz:ipo:0")
    async def ipo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_ipo_button(interaction, self.business_id)


async def handle_daily(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    now = datetime.now(timezone.utc)
    if business["last_daily"]:
        last = business["last_daily"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds() / 3600
        if elapsed < DAILY_COOLDOWN_HOURS:
            remaining = DAILY_COOLDOWN_HOURS - elapsed
            h, m = int(remaining), int((remaining % 1) * 60)
            embed = styled_embed("Daily Already Claimed",
                f"Come back in **{h}h {m}m**.", color=WARNING)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    await update_daily(business_id, DAILY_REWARD)
    await add_cash(interaction.guild_id, interaction.user.id, DAILY_REWARD)
    sym = guild_row["currency_symbol"]
    embed = styled_embed("Daily Claimed",
        f"You earned **{sym}{DAILY_REWARD:,.2f}** from **{business['name']}**.\n"
        f"Come back in {DAILY_COOLDOWN_HOURS} hours.", color=SUCCESS)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_stats(interaction: discord.Interaction, business_id: int):
    await interaction.response.defer(ephemeral=True)
    business = await get_business(business_id)
    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    sym = guild_row["currency_symbol"]

    stock = await get_stock_by_business(business_id)
    history = []
    if stock:
        history = await get_price_history(stock["id"], limit=60)

    public_status = "Public 📈" if business["is_public"] else "Private 🔒"
    stock_info = ""
    if stock and stock["ipo_completed"]:
        stock_info = (
            f"**Stock Ticker:** {stock['ticker']}\n"
            f"**Stock Price:** {sym}{stock['current_price']:,.4f}\n"
            f"**IPO Price:** {sym}{stock['ipo_price']:,.4f}"
        )
    elif stock:
        stock_info = f"**Stock Ticker:** {stock['ticker']} *(IPO pending)*"
    else:
        stock_info = "**Stock:** Not listed"

    embed = styled_embed(
        business["name"],
        f"**Industry:** {business['industry']}\n"
        f"**Status:** {public_status}\n"
        f"**Total Revenue:** {sym}{business['revenue']:,.2f}\n"
        f"{stock_info}",
        color=ACCENT
    )

    if history:
        chart_buf = generate_business_chart(
            stock["ticker"], business["name"],
            [(r["price"], r["recorded_at"]) for r in history]
        )
        file = discord.File(chart_buf, filename="chart.png")
        embed.set_image(url="attachment://chart.png")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)


async def handle_ipo_button(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return
    if business["is_public"]:
        await interaction.response.send_message("Your business is already public.", ephemeral=True)
        return
    await interaction.response.send_modal(IPOModal(business_id, business["name"]))


# ── IPO Modal ─────────────────────────────────────────────────────────────────

class IPOModal(discord.ui.Modal, title="Launch IPO"):
    ticker_input = discord.ui.TextInput(
        label="Stock Ticker (2–5 letters)",
        placeholder="e.g. ACME",
        min_length=2, max_length=5
    )
    price_input = discord.ui.TextInput(
        label="IPO Price per Share",
        placeholder=f"Minimum {IPO_MIN_PRICE}",
        max_length=12
    )
    shares_input = discord.ui.TextInput(
        label="Total Shares to Issue",
        placeholder="e.g. 10000",
        max_length=12
    )

    def __init__(self, business_id: int, business_name: str):
        super().__init__()
        self.business_id = business_id
        self.business_name = business_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        ticker = self.ticker_input.value.upper().strip()
        if not ticker.isalpha():
            await interaction.followup.send("Ticker must be letters only.", ephemeral=True)
            return

        try:
            price = float(self.price_input.value.replace(",", ""))
            if price < IPO_MIN_PRICE:
                raise ValueError
        except ValueError:
            await interaction.followup.send(
                f"Invalid price. Minimum IPO price is {IPO_MIN_PRICE}.", ephemeral=True
            )
            return

        try:
            total_shares = float(self.shares_input.value.replace(",", ""))
            if total_shares < 1:
                raise ValueError
        except ValueError:
            await interaction.followup.send("Invalid share count.", ephemeral=True)
            return

        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        sym = guild_row["currency_symbol"]

        # Check ticker not taken
        existing = await get_stock_by_ticker(interaction.guild_id, ticker)
        if existing:
            await interaction.followup.send(
                f"Ticker `{ticker}` is already taken. Choose another.", ephemeral=True
            )
            return

        # Make business public
        await set_business_public(self.business_id, True)

        # Create stock (ipo_completed=False until we flip it)
        stock = await create_stock(
            interaction.guild_id, ticker, self.business_name,
            stock_type="business", business_id=self.business_id,
            initial_price=price, ipo_price=price
        )
        if stock:
            await complete_ipo(stock["id"])

        # Announce in stock channel
        if guild_row["stock_channel_id"]:
            channel = interaction.guild.get_channel(guild_row["stock_channel_id"])
            if channel:
                embed = styled_embed(
                    f"🚀 IPO: {self.business_name}",
                    f"**{self.business_name}** has gone public!\n\n"
                    f"**Ticker:** `{ticker}`\n"
                    f"**IPO Price:** {sym}{price:,.4f}\n"
                    f"**Total Shares:** {total_shares:,.0f}\n\n"
                    f"Owned by <@{interaction.user.id}>. Trade it now from the Stock Market!",
                    color=SUCCESS
                )
                await channel.send(embed=embed)

        # Update the forum post embed to reflect public status
        business = await get_business(self.business_id)
        if business and business["post_thread_id"]:
            try:
                thread = interaction.guild.get_channel(business["post_thread_id"])
                if thread is None:
                    thread = await interaction.guild.fetch_channel(business["post_thread_id"])
                if thread:
                    updated_embed = await _build_business_embed(business, guild_row)
                    # Edit the first message in the thread
                    async for msg in thread.history(limit=1, oldest_first=True):
                        await msg.edit(embed=updated_embed)
            except Exception:
                pass

        await interaction.followup.send(
            embed=styled_embed(
                "IPO Launched! 🎉",
                f"**{self.business_name}** is now publicly traded as `{ticker}`.\n"
                f"IPO price: {sym}{price:,.4f}",
                color=SUCCESS
            ),
            ephemeral=True
        )


# ── Embed builder ─────────────────────────────────────────────────────────────

async def _build_business_embed(business: dict, guild_row: dict) -> discord.Embed:
    sym = guild_row["currency_symbol"]
    owner_id = business["owner_id"]
    public_status = "Public 📈" if business["is_public"] else "Private 🔒"
    stock = await get_stock_by_business(business["id"])
    stock_line = ""
    if stock and stock["ipo_completed"]:
        stock_line = f"\n**Stock Ticker:** `{stock['ticker']}` @ {sym}{stock['current_price']:,.4f}"

    embed = styled_embed(
        business["name"],
        f"**Owner:** <@{owner_id}>\n"
        f"**Industry:** {business['industry']}\n"
        f"**Description:** {business['description']}\n\n"
        f"**Status:** {public_status}{stock_line}\n"
        f"**Total Revenue:** {sym}{business['revenue']:,.2f}\n"
        f"**Listed Since:** <t:{int(business['created_at'].timestamp())}:D>",
        color=ACCENT
    )
    embed.set_footer(text=f"Business ID: {business['id']}  |  Economy System")
    return embed


# ── Cog ───────────────────────────────────────────────────────────────────────

class Businesses(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        pool = get_pool()
        rows = await pool.fetch("SELECT id FROM businesses WHERE post_message_id IS NOT NULL")
        for row in rows:
            view = BusinessPostView(row["id"])
            self.bot.add_view(view)

    # ── Username sync ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """When a member's display name changes, update their business forum thread titles."""
        if before.display_name == after.display_name:
            return
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT id, name, post_thread_id FROM businesses WHERE guild_id = $1 AND owner_id = $2 AND post_thread_id IS NOT NULL",
            after.guild.id, after.id
        )
        for row in rows:
            try:
                thread = after.guild.get_channel(row["post_thread_id"])
                if thread is None:
                    thread = await after.guild.fetch_channel(row["post_thread_id"])
                if thread:
                    new_name = _forum_thread_name(after, after.name, row["name"])
                    await thread.edit(name=new_name)
            except Exception:
                pass

    # ── Admin: review applications ─────────────────────────────────────────────

    @app_commands.command(name="pending_applications", description="View pending business applications.")
    async def pending_apps(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        apps = await get_pending_applications(interaction.guild_id)
        if not apps:
            await interaction.response.send_message("No pending applications.", ephemeral=True)
            return
        desc = ""
        for a in apps:
            desc += f"**#{a['id']} — {a['name']}** by <@{a['owner_id']}>\n"
            desc += f"Industry: {a['industry']}\n{a['description']}\n\n"
        embed = styled_embed("Pending Applications", desc.strip(), color=WARNING)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="review_application", description="Approve or reject a business application.")
    @app_commands.describe(application_id="The application ID to review")
    async def review_application(self, interaction: discord.Interaction, application_id: int):
        if not await admin_check(interaction):
            return
        app = await get_application(application_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        if app["status"] != "pending":
            await interaction.response.send_message(
                f"Application is already **{app['status']}**.", ephemeral=True
            )
            return
        embed = styled_embed(
            f"Application #{application_id}",
            f"**{app['name']}** by <@{app['owner_id']}>\n"
            f"Industry: {app['industry']}\n{app['description']}",
            color=WARNING
        )
        await interaction.response.send_message(
            embed=embed,
            view=ReviewView(application_id, self.bot),
            ephemeral=True
        )

    @app_commands.command(name="post_business", description="Manually re-post a business's forum thread.")
    @app_commands.describe(business_id="The business ID")
    async def post_business(self, interaction: discord.Interaction, business_id: int):
        if not await admin_check(interaction):
            return
        await self._create_business_post(interaction, business_id)
        await interaction.response.send_message("Business forum post created.", ephemeral=True)

    # ── Internal: create forum post ───────────────────────────────────────────

    async def _create_business_post(self, interaction_or_guild, business_id: int):
        pool = get_pool()
        business = await get_business(business_id)
        if not business:
            return

        guild = interaction_or_guild.guild if hasattr(interaction_or_guild, "guild") else interaction_or_guild
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", guild.id)
        if not guild_row or not guild_row["business_channel_id"]:
            return

        channel = guild.get_channel(guild_row["business_channel_id"])
        if not channel:
            return

        # Must be a ForumChannel
        if not isinstance(channel, discord.ForumChannel):
            # Fallback: post in regular channel (shouldn't happen after proper setup)
            embed = await _build_business_embed(business, guild_row)
            view = BusinessPostView(business_id)
            msg = await channel.send(embed=embed, view=view)
            await update_business_message(business_id, msg.id, None)
            self.bot.add_view(view)
            return

        member = guild.get_member(business["owner_id"])
        thread_name = _forum_thread_name(member, f"User {business['owner_id']}", business["name"])
        embed = await _build_business_embed(business, guild_row)
        view = BusinessPostView(business_id)

        thread_with_msg = await channel.create_thread(
            name=thread_name,
            embed=embed,
            view=view
        )
        thread = thread_with_msg.thread
        msg   = thread_with_msg.message

        await update_business_message(business_id, msg.id, thread.id)
        self.bot.add_view(view)


# ── Review buttons ─────────────────────────────────────────────────────────────

class ReviewView(discord.ui.View):
    def __init__(self, app_id: int, bot: commands.Bot):
        super().__init__(timeout=120)
        self.app_id = app_id
        self.bot = bot

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        business = await approve_application(self.app_id)
        pool = get_pool()
        guild_row = await pool.fetchrow(
            "SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id
        )

        # Post business to forum channel
        if guild_row and guild_row["business_channel_id"]:
            channel = interaction.guild.get_channel(guild_row["business_channel_id"])
            if channel:
                if isinstance(channel, discord.ForumChannel):
                    member = interaction.guild.get_member(business["owner_id"])
                    thread_name = _forum_thread_name(
                        member, f"User {business['owner_id']}", business["name"]
                    )
                    embed = await _build_business_embed(business, guild_row)
                    view = BusinessPostView(business["id"])
                    thread_with_msg = await channel.create_thread(
                        name=thread_name, embed=embed, view=view
                    )
                    thread = thread_with_msg.thread
                    msg   = thread_with_msg.message
                    await update_business_message(business["id"], msg.id, thread.id)
                    self.bot.add_view(view)
                else:
                    # Fallback for non-forum channels
                    embed = await _build_business_embed(business, guild_row)
                    view = BusinessPostView(business["id"])
                    msg = await channel.send(embed=embed, view=view)
                    await update_business_message(business["id"], msg.id, None)
                    self.bot.add_view(view)

        # Notify applicant
        try:
            member = interaction.guild.get_member(business["owner_id"])
            if member:
                await member.send(
                    embed=styled_embed(
                        "Business Approved",
                        f"Your business **{business['name']}** has been approved!\n\n"
                        f"It's currently **private**. You can launch an IPO from the business post "
                        f"to list it on the stock market when you're ready.",
                        color=SUCCESS
                    )
                )
        except Exception:
            pass

        await interaction.response.edit_message(
            embed=styled_embed(
                "Approved",
                f"Business **{business['name']}** is now live (private).",
                color=SUCCESS
            ),
            view=None
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        app = await get_application(self.app_id)
        await reject_application(self.app_id)
        try:
            member = interaction.guild.get_member(app["owner_id"])
            if member:
                await member.send(
                    embed=styled_embed(
                        "Business Rejected",
                        f"Your application for **{app['name']}** was not approved.",
                        color=DANGER
                    )
                )
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=styled_embed("Rejected", "Application rejected.", color=DANGER),
            view=None
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Businesses(bot))

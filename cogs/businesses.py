import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
from utils.helpers import admin_check, ensure_guild, styled_embed, ACCENT, SUCCESS, DANGER, WARNING
from utils.graphs import generate_business_chart
from db.queries.businesses import (
    get_pending_applications, get_application, approve_application,
    reject_application, get_business, update_business_message,
    set_business_public, set_ceo_salary, claim_daily_salary,
    deduct_company_wallet, create_expansion_proposal,
    get_pending_expansions, get_expansion, resolve_expansion,
    work_business, delete_business, get_businesses_by_guild,
)
from db.queries.stocks import (
    create_stock, get_stock_by_ticker, get_stock_by_business,
    get_price_history, complete_ipo, get_holders_of_stock, get_total_shares
)
from db.queries.wallets import add_cash, get_or_create_wallet
from db.connection import get_pool

DAILY_COOLDOWN_HOURS = 20
IPO_MIN_PRICE = 1.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _forum_thread_name(member: discord.Member | None, username: str, business_name: str) -> str:
    display = member.display_name if member else username
    return f"{display} — {business_name}"


def _tax(amount: float, rate_pct: float) -> tuple[float, float]:
    """Returns (net_after_tax, tax_amount)."""
    tax = round(amount * rate_pct / 100, 2)
    return round(amount - tax, 2), tax


async def _delete_forum_thread(guild: discord.Guild, thread_id: int | None):
    """Attempts to delete a forum thread by ID. Silently ignores errors."""
    if not thread_id:
        return
    try:
        thread = guild.get_channel(thread_id) or await guild.fetch_channel(thread_id)
        if thread:
            await thread.delete()
    except Exception:
        pass


# ── Business post persistent view ─────────────────────────────────────────────

class BusinessPostView(discord.ui.View):
    def __init__(self, business_id: int):
        super().__init__(timeout=None)
        self.business_id = business_id
        self.daily_button.custom_id    = f"biz:daily:{business_id}"
        self.work_button.custom_id     = f"biz:work:{business_id}"
        self.stats_button.custom_id    = f"biz:stats:{business_id}"
        self.ipo_button.custom_id      = f"biz:ipo:{business_id}"
        self.salary_button.custom_id   = f"biz:salary:{business_id}"
        self.expand_button.custom_id   = f"biz:expand:{business_id}"
        self.dividend_button.custom_id = f"biz:dividend:{business_id}"
        self.shutdown_button.custom_id = f"biz:shutdown:{business_id}"

    @discord.ui.button(label="💰 Claim Salary", style=discord.ButtonStyle.success, custom_id="biz:daily:0")
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_daily(interaction, self.business_id)

    @discord.ui.button(label="🏢 Work", style=discord.ButtonStyle.primary, custom_id="biz:work:0")
    async def work_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_work(interaction, self.business_id)

    @discord.ui.button(label="📊 Stats", style=discord.ButtonStyle.secondary, custom_id="biz:stats:0")
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_stats(interaction, self.business_id)

    @discord.ui.button(label="🚀 Go Public (IPO)", style=discord.ButtonStyle.primary, custom_id="biz:ipo:0")
    async def ipo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_ipo_button(interaction, self.business_id)

    @discord.ui.button(label="💼 Set Salary", style=discord.ButtonStyle.secondary, custom_id="biz:salary:0")
    async def salary_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_set_salary(interaction, self.business_id)

    @discord.ui.button(label="🏗️ Expand", style=discord.ButtonStyle.primary, custom_id="biz:expand:0")
    async def expand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_expand(interaction, self.business_id)

    @discord.ui.button(label="💸 Pay Dividends", style=discord.ButtonStyle.danger, custom_id="biz:dividend:0")
    async def dividend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_dividend(interaction, self.business_id)

    @discord.ui.button(label="🔴 Shut Down", style=discord.ButtonStyle.danger, custom_id="biz:shutdown:0")
    async def shutdown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_shutdown(interaction, self.business_id)


# ── Work (deposit daily revenue into company wallet) ──────────────────────────

async def handle_work(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    sym = guild_row["currency_symbol"]

    if float(business["revenue"]) <= 0:
        await interaction.response.send_message(
            "Your business has **no revenue** yet. Grow it first via the **🏗️ Expand** button!",
            ephemeral=True
        )
        return

    result, success = await work_business(business_id)

    if not success:
        remaining = result  # hours remaining
        h, m = int(remaining), int((remaining % 1) * 60)
        embed = styled_embed(
            "Business Already Worked",
            f"Your business already generated revenue today.\nCome back in **{h}h {m}m**.",
            color=WARNING
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    earned = result
    embed = styled_embed(
        "🏢 Revenue Collected",
        f"Your business worked and earned **{sym}{earned:,.2f}** in revenue!\n\n"
        f"This amount has been deposited into the **Company Wallet**.\n"
        f"Use **💰 Claim Salary** to pay yourself from the company wallet.\n\n"
        f"Come back in **{DAILY_COOLDOWN_HOURS} hours** to work again.",
        color=SUCCESS
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Daily salary claim ────────────────────────────────────────────────────────

async def handle_daily(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    if float(business["ceo_salary"]) <= 0:
        await interaction.response.send_message(
            "Your CEO salary is set to **0**. Use **Set Salary** to configure it first.",
            ephemeral=True
        )
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
            embed = styled_embed("Salary Already Claimed",
                f"Come back in **{h}h {m}m**.", color=WARNING)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    sym = guild_row["currency_symbol"]
    tax_rate = float(guild_row["tax_rate_salary"])

    salary = float(business["ceo_salary"])
    net, tax = _tax(salary, tax_rate)

    ok = await deduct_company_wallet(business_id, salary)
    if not ok:
        biz = await get_business(business_id)
        available = float(biz["company_wallet"])
        if available <= 0:
            await interaction.response.send_message(
                f"Your company wallet is empty (**{sym}0.00**). "
                f"Use the **🏢 Work** button to deposit today's revenue first.",
                ephemeral=True
            )
            return
        salary = available
        net, tax = _tax(salary, tax_rate)
        await deduct_company_wallet(business_id, salary)

    await pool.execute(
        "UPDATE businesses SET last_daily = NOW(), revenue = revenue + $1 WHERE id = $2",
        salary, business_id
    )
    await add_cash(interaction.guild_id, interaction.user.id, net)

    embed = styled_embed(
        "💰 Salary Claimed",
        f"**Gross Salary:** {sym}{salary:,.2f}\n"
        f"**Tax ({tax_rate:.0f}%):** -{sym}{tax:,.2f}\n"
        f"**Net to Wallet:** {sym}{net:,.2f}\n\n"
        f"Come back in {DAILY_COOLDOWN_HOURS} hours.",
        color=SUCCESS
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Set salary ────────────────────────────────────────────────────────────────

async def handle_set_salary(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    sym = guild_row["currency_symbol"]
    max_pct = float(guild_row["salary_max_pct"])
    daily_revenue = float(business["revenue"])
    max_salary = daily_revenue * max_pct / 100 if daily_revenue > 0 else 0

    embed = styled_embed(
        "💼 Set CEO Salary",
        f"**Current Salary:** {sym}{business['ceo_salary']:,.2f}/day\n"
        f"**Total Company Revenue:** {sym}{daily_revenue:,.2f}\n"
        f"**Max Allowed ({max_pct:.0f}% of revenue):** {sym}{max_salary:,.2f}/day\n\n"
        f"Your salary is deducted from the company wallet each time you claim it. "
        f"The company wallet earns revenue when you press **🏢 Work**.",
        color=ACCENT
    )
    await interaction.response.send_message(
        embed=embed,
        view=SetSalaryView(business_id, max_salary, sym),
        ephemeral=True
    )


class SetSalaryView(discord.ui.View):
    def __init__(self, business_id: int, max_salary: float, sym: str):
        super().__init__(timeout=60)
        self.business_id = business_id
        self.max_salary = max_salary
        self.sym = sym

    @discord.ui.button(label="Set Salary Amount", style=discord.ButtonStyle.primary)
    async def set_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SalaryModal(self.business_id, self.max_salary, self.sym))


class SalaryModal(discord.ui.Modal, title="Set CEO Salary"):
    amount = discord.ui.TextInput(label="Daily Salary Amount", placeholder="e.g. 500", max_length=16)

    def __init__(self, business_id: int, max_salary: float, sym: str):
        super().__init__()
        self.business_id = business_id
        self.max_salary = max_salary
        self.sym = sym

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = float(self.amount.value.replace(",", ""))
            if val < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return

        if self.max_salary > 0 and val > self.max_salary:
            await interaction.response.send_message(
                f"Salary exceeds the maximum allowed ({self.sym}{self.max_salary:,.2f}/day). "
                f"Grow your business revenue first.",
                ephemeral=True
            )
            return

        await set_ceo_salary(self.business_id, val)
        embed = styled_embed("Salary Updated",
            f"CEO salary set to **{self.sym}{val:,.2f}/day**.", color=SUCCESS)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Stats ─────────────────────────────────────────────────────────────────────

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
        total_shares = await get_total_shares(stock["id"])
        stock_info = (
            f"**Ticker:** {stock['ticker']} @ {sym}{stock['current_price']:,.4f}\n"
            f"**IPO Price:** {sym}{stock['ipo_price']:,.4f}\n"
            f"**Total Shares:** {total_shares:,.2f}"
        )
    elif stock:
        stock_info = f"**Ticker:** {stock['ticker']} *(IPO pending)*"
    else:
        stock_info = "**Stock:** Not listed"

    embed = styled_embed(
        business["name"],
        f"**Industry:** {business['industry']}\n"
        f"**Status:** {public_status}\n"
        f"**Company Wallet:** {sym}{business['company_wallet']:,.2f}\n"
        f"**Total Revenue:** {sym}{business['revenue']:,.2f}\n"
        f"**CEO Salary:** {sym}{business['ceo_salary']:,.2f}/day\n"
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


# ── IPO ───────────────────────────────────────────────────────────────────────

async def handle_ipo_button(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return
    if business["is_public"]:
        await interaction.response.send_message("Your business is already public.", ephemeral=True)
        return
    await interaction.response.send_modal(IPOModal(business_id, business["name"]))


class IPOModal(discord.ui.Modal, title="Launch IPO"):
    ticker_input = discord.ui.TextInput(label="Stock Ticker (2–5 letters)", placeholder="e.g. ACME",
                                         min_length=2, max_length=5)
    price_input  = discord.ui.TextInput(label="IPO Price per Share", placeholder=f"Minimum {IPO_MIN_PRICE}",
                                         max_length=12)
    shares_input = discord.ui.TextInput(label="Total Shares to Issue", placeholder="e.g. 10000", max_length=12)

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
            await interaction.followup.send(f"Invalid price. Minimum is {IPO_MIN_PRICE}.", ephemeral=True)
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

        existing = await get_stock_by_ticker(interaction.guild_id, ticker)
        if existing:
            await interaction.followup.send(f"Ticker `{ticker}` is already taken.", ephemeral=True)
            return

        await set_business_public(self.business_id, True)
        stock = await create_stock(
            interaction.guild_id, ticker, self.business_name,
            stock_type="business", business_id=self.business_id,
            initial_price=price, ipo_price=price
        )
        if stock:
            await complete_ipo(stock["id"])

        if guild_row["stock_channel_id"]:
            channel = interaction.guild.get_channel(guild_row["stock_channel_id"])
            if channel:
                await channel.send(embed=styled_embed(
                    f"🚀 IPO: {self.business_name}",
                    f"**{self.business_name}** has gone public!\n\n"
                    f"**Ticker:** `{ticker}`\n"
                    f"**IPO Price:** {sym}{price:,.4f}\n"
                    f"**Total Shares:** {total_shares:,.0f}\n\n"
                    f"Owned by <@{interaction.user.id}>. Trade it now!",
                    color=SUCCESS
                ))

        business = await get_business(self.business_id)
        if business and business["post_thread_id"]:
            try:
                thread = interaction.guild.get_channel(business["post_thread_id"]) or \
                         await interaction.guild.fetch_channel(business["post_thread_id"])
                if thread:
                    updated_embed = await _build_business_embed(business, guild_row)
                    async for msg in thread.history(limit=1, oldest_first=True):
                        await msg.edit(embed=updated_embed)
            except Exception:
                pass

        await interaction.followup.send(embed=styled_embed(
            "IPO Launched! 🎉",
            f"**{self.business_name}** is now publicly traded as `{ticker}`.\n"
            f"IPO price: {sym}{price:,.4f}",
            color=SUCCESS
        ), ephemeral=True)


# ── Expansion ─────────────────────────────────────────────────────────────────

async def handle_expand(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    sym = guild_row["currency_symbol"]

    embed = styled_embed(
        "🏗️ Business Expansion",
        f"**{business['name']}**\n\n"
        f"Submit an expansion proposal to grow your business. Admins will review it and "
        f"may approve, deny, or modify the revenue increase.\n\n"
        f"**Current Company Wallet:** {sym}{business['company_wallet']:,.2f}\n"
        f"**Total Revenue:** {sym}{business['revenue']:,.2f}\n\n"
        f"Describe your expansion in as much detail as possible — the more convincing "
        f"the proposal, the better your chances of approval.",
        color=ACCENT
    )
    await interaction.response.send_message(
        embed=embed,
        view=ExpandConfirmView(business_id),
        ephemeral=True
    )


class ExpandConfirmView(discord.ui.View):
    def __init__(self, business_id: int):
        super().__init__(timeout=60)
        self.business_id = business_id

    @discord.ui.button(label="✅ Write Proposal", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ExpansionModal(self.business_id))

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=styled_embed("Cancelled", "Expansion proposal cancelled.", color=WARNING),
            view=None
        )


class ExpansionModal(discord.ui.Modal, title="Expansion Proposal"):
    title_input = discord.ui.TextInput(
        label="Expansion Title",
        placeholder="e.g. Open a second location downtown",
        max_length=100
    )
    description_input = discord.ui.TextInput(
        label="Detailed Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your expansion plan in detail...",
        max_length=1000
    )
    revenue_input = discord.ui.TextInput(
        label="Estimated Revenue Increase",
        placeholder="e.g. 500 (per day)",
        max_length=16
    )

    def __init__(self, business_id: int):
        super().__init__()
        self.business_id = business_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            est_rev = float(self.revenue_input.value.replace(",", ""))
            if est_rev <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Invalid revenue estimate.", ephemeral=True)
            return

        business = await get_business(self.business_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        sym = guild_row["currency_symbol"]

        proposal_id = await create_expansion_proposal(
            self.business_id, interaction.guild_id, interaction.user.id,
            self.title_input.value, self.description_input.value, est_rev
        )

        await interaction.response.send_message(
            embed=styled_embed(
                "Proposal Submitted ✅",
                f"Your expansion proposal **#{proposal_id}** for **{business['name']}** "
                f"has been submitted for admin review.\n\n"
                f"**Title:** {self.title_input.value}\n"
                f"**Est. Revenue Increase:** {sym}{est_rev:,.2f}/day\n\n"
                f"You'll be notified of the decision.",
                color=SUCCESS
            ),
            ephemeral=True
        )

        if guild_row["admin_role_id"]:
            role = interaction.guild.get_role(guild_row["admin_role_id"])
            if role and interaction.channel:
                try:
                    notify = styled_embed(
                        f"🏗️ Expansion Proposal #{proposal_id}",
                        f"**{business['name']}** by <@{interaction.user.id}>\n\n"
                        f"**Title:** {self.title_input.value}\n"
                        f"**Est. Revenue:** {sym}{est_rev:,.2f}/day\n\n"
                        f"Use `/review_expansion {proposal_id}` to review.",
                        color=WARNING
                    )
                    await interaction.channel.send(content=role.mention, embed=notify)
                except Exception:
                    pass


# ── Shutdown (player self-delete) ─────────────────────────────────────────────

async def handle_shutdown(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    embed = styled_embed(
        "⚠️ Shut Down Business?",
        f"Are you sure you want to **permanently shut down** `{business['name']}`?\n\n"
        f"This will:\n"
        f"• Delete the business and **all its data**\n"
        f"• Remove the forum post\n"
        f"• Delist any associated stock\n\n"
        f"**This action cannot be undone.**",
        color=DANGER
    )
    await interaction.response.send_message(
        embed=embed,
        view=ShutdownConfirmView(business_id, business["name"]),
        ephemeral=True
    )


class ShutdownConfirmView(discord.ui.View):
    def __init__(self, business_id: int, business_name: str):
        super().__init__(timeout=60)
        self.business_id = business_id
        self.business_name = business_name

    @discord.ui.button(label="✅ Yes, Shut Down", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        business = await get_business(self.business_id)
        if not business or business["owner_id"] != interaction.user.id:
            await interaction.response.edit_message(
                embed=styled_embed("Error", "Could not verify ownership.", color=DANGER), view=None
            )
            return

        thread_id = business.get("post_thread_id")
        await delete_business(self.business_id)
        await _delete_forum_thread(interaction.guild, thread_id)

        await interaction.response.edit_message(
            embed=styled_embed(
                "Business Shut Down",
                f"**{self.business_name}** has been permanently shut down and removed.",
                color=DANGER
            ),
            view=None
        )

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=styled_embed("Cancelled", "Shutdown cancelled. Your business is safe.", color=SUCCESS),
            view=None
        )


# ── Dividends ─────────────────────────────────────────────────────────────────

async def handle_dividend(interaction: discord.Interaction, business_id: int):
    business = await get_business(business_id)
    if not business or business["owner_id"] != interaction.user.id:
        await interaction.response.send_message("This isn't your business.", ephemeral=True)
        return

    if not business["is_public"]:
        await interaction.response.send_message(
            "Dividends can only be paid to public companies with stockholders.", ephemeral=True
        )
        return

    stock = await get_stock_by_business(business_id)
    if not stock or not stock["ipo_completed"]:
        await interaction.response.send_message("No active stock found for this business.", ephemeral=True)
        return

    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    sym = guild_row["currency_symbol"]
    tax_rate = float(guild_row["tax_rate_dividend"])
    company_wallet = float(business["company_wallet"])

    total_shares = await get_total_shares(stock["id"])
    if total_shares <= 0:
        await interaction.response.send_message("No shareholders to pay dividends to.", ephemeral=True)
        return

    embed = styled_embed(
        "💸 Pay Dividends",
        f"**Company Wallet:** {sym}{company_wallet:,.2f}\n"
        f"**Total Shares Outstanding:** {total_shares:,.2f}\n"
        f"**Dividend Tax:** {tax_rate:.0f}% (deducted from each shareholder's payout)\n\n"
        f"Enter the total dividend pool to distribute. It will be split proportionally "
        f"by share count among all holders. Deducted from company wallet.",
        color=ACCENT
    )
    await interaction.response.send_message(
        embed=embed,
        view=DividendConfirmView(business_id, stock["id"], company_wallet, total_shares, tax_rate, sym),
        ephemeral=True
    )


class DividendConfirmView(discord.ui.View):
    def __init__(self, business_id, stock_id, company_wallet, total_shares, tax_rate, sym):
        super().__init__(timeout=60)
        self.business_id = business_id
        self.stock_id = stock_id
        self.company_wallet = company_wallet
        self.total_shares = total_shares
        self.tax_rate = tax_rate
        self.sym = sym

    @discord.ui.button(label="Set Dividend Amount", style=discord.ButtonStyle.danger)
    async def pay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            DividendModal(self.business_id, self.stock_id, self.company_wallet,
                          self.total_shares, self.tax_rate, self.sym)
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=styled_embed("Cancelled", "Dividend payment cancelled.", color=WARNING), view=None
        )


class DividendModal(discord.ui.Modal, title="Pay Dividends"):
    pool_input = discord.ui.TextInput(label="Total Dividend Pool", placeholder="e.g. 10000", max_length=16)

    def __init__(self, business_id, stock_id, company_wallet, total_shares, tax_rate, sym):
        super().__init__()
        self.business_id = business_id
        self.stock_id = stock_id
        self.company_wallet = company_wallet
        self.total_shares = total_shares
        self.tax_rate = tax_rate
        self.sym = sym

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            pool_amount = float(self.pool_input.value.replace(",", ""))
            if pool_amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.followup.send("Invalid amount.", ephemeral=True)
            return

        if pool_amount > self.company_wallet:
            await interaction.followup.send(
                f"Insufficient company wallet balance. "
                f"Available: {self.sym}{self.company_wallet:,.2f}",
                ephemeral=True
            )
            return

        ok = await deduct_company_wallet(self.business_id, pool_amount)
        if not ok:
            await interaction.followup.send("Failed to deduct from company wallet.", ephemeral=True)
            return

        holders = await get_holders_of_stock(self.stock_id)
        if not holders:
            await interaction.followup.send("No shareholders found.", ephemeral=True)
            return

        pool = get_pool()
        paid_count = 0
        total_paid = 0.0
        for holder in holders:
            share_fraction = float(holder["shares"]) / self.total_shares
            gross = pool_amount * share_fraction
            net, tax = _tax(gross, self.tax_rate)
            await add_cash(holder["guild_id"], holder["user_id"], net)
            total_paid += net
            paid_count += 1

            try:
                guild = interaction.guild
                member = guild.get_member(holder["user_id"])
                guild_row = await pool.fetchrow(
                    "SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id
                )
                sym = guild_row["currency_symbol"]
                if member:
                    await member.send(embed=styled_embed(
                        "💸 Dividend Payment",
                        f"You received a dividend from a business you hold shares in.\n\n"
                        f"**Gross:** {sym}{gross:,.2f}\n"
                        f"**Tax ({self.tax_rate:.0f}%):** -{sym}{tax:,.2f}\n"
                        f"**Net to Cash Wallet:** {sym}{net:,.2f}",
                        color=SUCCESS
                    ))
            except Exception:
                pass

        business = await get_business(self.business_id)
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        sym = guild_row["currency_symbol"]
        await interaction.followup.send(embed=styled_embed(
            "Dividends Paid ✅",
            f"**Total Pool:** {sym}{pool_amount:,.2f}\n"
            f"**Shareholders Paid:** {paid_count}\n"
            f"**Total Distributed (after tax):** {sym}{total_paid:,.2f}\n"
            f"**Dividend Tax Rate:** {self.tax_rate:.0f}%\n\n"
            f"All shareholders have been notified via DM.",
            color=SUCCESS
        ), ephemeral=True)


# ── Embed builder ─────────────────────────────────────────────────────────────

async def _build_business_embed(business: dict, guild_row: dict) -> discord.Embed:
    sym = guild_row["currency_symbol"]
    owner_id = business["owner_id"]
    public_status = "Public 📈" if business["is_public"] else "Private 🔒"
    stock = await get_stock_by_business(business["id"])
    stock_line = ""
    if stock and stock["ipo_completed"]:
        stock_line = f"\n**Stock:** `{stock['ticker']}` @ {sym}{stock['current_price']:,.4f}"

    embed = styled_embed(
        business["name"],
        f"**Owner:** <@{owner_id}>\n"
        f"**Industry:** {business['industry']}\n"
        f"**Description:** {business['description']}\n\n"
        f"**Status:** {public_status}{stock_line}\n"
        f"**Company Wallet:** {sym}{business['company_wallet']:,.2f}\n"
        f"**Total Revenue:** {sym}{business['revenue']:,.2f}\n"
        f"**CEO Salary:** {sym}{business['ceo_salary']:,.2f}/day\n"
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
            self.bot.add_view(BusinessPostView(row["id"]))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.display_name == after.display_name:
            return
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT id, name, post_thread_id FROM businesses WHERE guild_id = $1 AND owner_id = $2 AND post_thread_id IS NOT NULL",
            after.guild.id, after.id
        )
        for row in rows:
            try:
                thread = after.guild.get_channel(row["post_thread_id"]) or \
                         await after.guild.fetch_channel(row["post_thread_id"])
                if thread:
                    await thread.edit(name=_forum_thread_name(after, after.name, row["name"]))
            except Exception:
                pass

    # ── Admin: review applications ────────────────────────────────────────────

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
        await interaction.response.send_message(
            embed=styled_embed("Pending Applications", desc.strip(), color=WARNING),
            ephemeral=True
        )

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
            await interaction.response.send_message(f"Application is already **{app['status']}**.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=styled_embed(
                f"Application #{application_id}",
                f"**{app['name']}** by <@{app['owner_id']}>\n"
                f"Industry: {app['industry']}\n{app['description']}",
                color=WARNING
            ),
            view=ReviewView(application_id, self.bot),
            ephemeral=True
        )

    # ── Admin: review expansions ──────────────────────────────────────────────

    @app_commands.command(name="pending_expansions", description="View pending expansion proposals.")
    async def pending_expansions_cmd(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        proposals = await get_pending_expansions(interaction.guild_id)
        if not proposals:
            await interaction.response.send_message("No pending expansion proposals.", ephemeral=True)
            return
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        sym = guild_row["currency_symbol"]
        desc = ""
        for p in proposals:
            desc += f"**#{p['id']} — {p['title']}**\n"
            desc += f"Business: {p['business_name']} | Owner: <@{p['owner_id']}>\n"
            desc += f"Est. Revenue: {sym}{p['estimated_revenue']:,.2f}/day\n\n"
        await interaction.response.send_message(
            embed=styled_embed("Pending Expansion Proposals", desc.strip(), color=WARNING),
            ephemeral=True
        )

    @app_commands.command(name="review_expansion", description="Approve, deny or modify an expansion proposal.")
    @app_commands.describe(proposal_id="The expansion proposal ID")
    async def review_expansion_cmd(self, interaction: discord.Interaction, proposal_id: int):
        if not await admin_check(interaction):
            return
        proposal = await get_expansion(proposal_id)
        if not proposal:
            await interaction.response.send_message("Proposal not found.", ephemeral=True)
            return
        if proposal["status"] != "pending":
            await interaction.response.send_message(f"Proposal already **{proposal['status']}**.", ephemeral=True)
            return
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        sym = guild_row["currency_symbol"]
        await interaction.response.send_message(
            embed=styled_embed(
                f"Expansion #{proposal_id}: {proposal['title']}",
                f"**Business:** {proposal['business_name']}\n"
                f"**Owner:** <@{proposal['owner_id']}>\n\n"
                f"**Description:**\n{proposal['description']}\n\n"
                f"**Estimated Revenue:** {sym}{proposal['estimated_revenue']:,.2f}/day",
                color=WARNING
            ),
            view=ExpansionReviewView(proposal_id, float(proposal["estimated_revenue"]), proposal["owner_id"]),
            ephemeral=True
        )

    # ── Admin: delete businesses ──────────────────────────────────────────────

    @app_commands.command(
        name="delete_business",
        description="[Admin] Delete one, several, or all businesses in this server."
    )
    @app_commands.describe(
        business_id="ID of a specific business to delete (leave blank with delete_all=True to wipe all)",
        delete_all="If True, deletes ALL businesses in this server. Requires confirmation.",
    )
    async def delete_business_cmd(
        self,
        interaction: discord.Interaction,
        business_id: int = None,
        delete_all: bool = False,
    ):
        if not await admin_check(interaction):
            return

        if not business_id and not delete_all:
            await interaction.response.send_message(
                "Provide a `business_id` to delete a specific business, "
                "or set `delete_all:True` to delete every business in this server.",
                ephemeral=True
            )
            return

        if delete_all:
            businesses = await get_businesses_by_guild(interaction.guild_id)
            if not businesses:
                await interaction.response.send_message("No businesses found in this server.", ephemeral=True)
                return
            count = len(businesses)
            embed = styled_embed(
                "⚠️ Delete ALL Businesses?",
                f"This will permanently delete **{count} business(es)** in this server, "
                f"along with their forum posts, stocks, and expansion history.\n\n"
                f"**This cannot be undone.**",
                color=DANGER
            )
            await interaction.response.send_message(
                embed=embed,
                view=AdminDeleteAllView(businesses, interaction.guild),
                ephemeral=True
            )
            return

        # Single business delete
        business = await get_business(business_id)
        if not business or business["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(
                f"Business `#{business_id}` not found in this server.", ephemeral=True
            )
            return

        embed = styled_embed(
            f"⚠️ Delete Business #{business_id}?",
            f"**{business['name']}** (owned by <@{business['owner_id']}>)\n\n"
            f"This will permanently delete the business, its forum post, "
            f"associated stock, and expansion history.\n\n"
            f"**This cannot be undone.**",
            color=DANGER
        )
        await interaction.response.send_message(
            embed=embed,
            view=AdminDeleteSingleView(business_id, business["name"], interaction.guild),
            ephemeral=True
        )

    # ── Admin: tax & salary config ────────────────────────────────────────────

    @app_commands.command(name="set_tax_rates", description="Configure tax rates for work, salary, stocks, and dividends.")
    @app_commands.describe(
        tax_work="Tax % on work income (0–100)",
        tax_salary="Tax % on CEO salary (0–100)",
        tax_stock_profit="Tax % on stock sale profits (0–100)",
        tax_dividend="Tax % on dividend payouts (0–100)"
    )
    async def set_tax_rates(self, interaction: discord.Interaction,
                            tax_work: float = None, tax_salary: float = None,
                            tax_stock_profit: float = None, tax_dividend: float = None):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        updates = {}
        if tax_work is not None:
            updates["tax_rate_work"] = max(0.0, min(100.0, tax_work))
        if tax_salary is not None:
            updates["tax_rate_salary"] = max(0.0, min(100.0, tax_salary))
        if tax_stock_profit is not None:
            updates["tax_rate_stock_profit"] = max(0.0, min(100.0, tax_stock_profit))
        if tax_dividend is not None:
            updates["tax_rate_dividend"] = max(0.0, min(100.0, tax_dividend))

        if not updates:
            await interaction.response.send_message("No values provided.", ephemeral=True)
            return

        set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(updates))
        vals = list(updates.values()) + [interaction.guild_id]
        await pool.execute(f"UPDATE guilds SET {set_clause} WHERE guild_id = ${len(vals)}", *vals)

        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        await interaction.response.send_message(embed=styled_embed(
            "Tax Rates Updated",
            f"**Work:** {guild_row['tax_rate_work']:.1f}%\n"
            f"**CEO Salary:** {guild_row['tax_rate_salary']:.1f}%\n"
            f"**Stock Profit:** {guild_row['tax_rate_stock_profit']:.1f}%\n"
            f"**Dividend:** {guild_row['tax_rate_dividend']:.1f}%",
            color=SUCCESS
        ), ephemeral=True)

    @app_commands.command(name="set_salary_cap", description="Set the max CEO salary as a % of total company revenue.")
    @app_commands.describe(percent="Max salary as % of total revenue (e.g. 50 = up to 50% of revenue/day)")
    async def set_salary_cap(self, interaction: discord.Interaction, percent: float):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        percent = max(1.0, min(100.0, percent))
        pool = get_pool()
        await pool.execute(
            "UPDATE guilds SET salary_max_pct = $1 WHERE guild_id = $2",
            percent, interaction.guild_id
        )
        await interaction.response.send_message(embed=styled_embed(
            "Salary Cap Updated",
            f"CEOs can now set their salary up to **{percent:.1f}%** of their company's total revenue per day.",
            color=SUCCESS
        ), ephemeral=True)

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

        embed = await _build_business_embed(business, guild_row)
        view = BusinessPostView(business_id)

        if isinstance(channel, discord.ForumChannel):
            member = guild.get_member(business["owner_id"])
            thread_name = _forum_thread_name(member, f"User {business['owner_id']}", business["name"])
            twm = await channel.create_thread(name=thread_name, embed=embed, view=view)
            await update_business_message(business_id, twm.message.id, twm.thread.id)
        else:
            msg = await channel.send(embed=embed, view=view)
            await update_business_message(business_id, msg.id, None)
        self.bot.add_view(view)


# ── Admin delete views ────────────────────────────────────────────────────────

class AdminDeleteSingleView(discord.ui.View):
    def __init__(self, business_id: int, business_name: str, guild: discord.Guild):
        super().__init__(timeout=60)
        self.business_id = business_id
        self.business_name = business_name
        self.guild = guild

    @discord.ui.button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        biz = await get_business(self.business_id)
        if not biz:
            await interaction.response.edit_message(
                embed=styled_embed("Already Gone", "Business not found — it may have already been deleted.", color=WARNING),
                view=None
            )
            return
        thread_id = biz.get("post_thread_id")
        await delete_business(self.business_id)
        await _delete_forum_thread(self.guild, thread_id)
        await interaction.response.edit_message(
            embed=styled_embed(
                "Business Deleted",
                f"**{self.business_name}** has been permanently deleted.",
                color=SUCCESS
            ),
            view=None
        )

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=styled_embed("Cancelled", "No businesses were deleted.", color=WARNING),
            view=None
        )


class AdminDeleteAllView(discord.ui.View):
    def __init__(self, businesses: list, guild: discord.Guild):
        super().__init__(timeout=60)
        self.businesses = businesses
        self.guild = guild

    @discord.ui.button(label="✅ Yes, Delete All", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        deleted = 0
        for biz in self.businesses:
            biz_dict = dict(biz)
            thread_id = biz_dict.get("post_thread_id")
            result = await delete_business(biz_dict["id"])
            if result:
                deleted += 1
                await _delete_forum_thread(self.guild, thread_id)

        await interaction.followup.send(
            embed=styled_embed(
                "All Businesses Deleted",
                f"Successfully deleted **{deleted}** business(es) and their forum posts.",
                color=SUCCESS
            ),
            ephemeral=True
        )

    @discord.ui.button(label="✖ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=styled_embed("Cancelled", "No businesses were deleted.", color=WARNING),
            view=None
        )


# ── Review views ──────────────────────────────────────────────────────────────

class ReviewView(discord.ui.View):
    def __init__(self, app_id: int, bot: commands.Bot):
        super().__init__(timeout=120)
        self.app_id = app_id
        self.bot = bot

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        business = await approve_application(self.app_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)

        if guild_row and guild_row["business_channel_id"]:
            channel = interaction.guild.get_channel(guild_row["business_channel_id"])
            if channel:
                embed = await _build_business_embed(business, guild_row)
                view = BusinessPostView(business["id"])
                if isinstance(channel, discord.ForumChannel):
                    member = interaction.guild.get_member(business["owner_id"])
                    thread_name = _forum_thread_name(member, f"User {business['owner_id']}", business["name"])
                    twm = await channel.create_thread(name=thread_name, embed=embed, view=view)
                    await update_business_message(business["id"], twm.message.id, twm.thread.id)
                else:
                    msg = await channel.send(embed=embed, view=view)
                    await update_business_message(business["id"], msg.id, None)
                self.bot.add_view(view)

        try:
            member = interaction.guild.get_member(business["owner_id"])
            if member:
                await member.send(embed=styled_embed(
                    "Business Approved ✅",
                    f"**{business['name']}** has been approved!\n\n"
                    f"It starts **private**. Use **Set Salary** to configure your CEO salary, "
                    f"then press **🏢 Work** daily to earn revenue into the company wallet, "
                    f"and **💰 Claim Salary** to pay yourself. Launch an IPO when you're ready to go public.",
                    color=SUCCESS
                ))
        except Exception:
            pass

        await interaction.response.edit_message(
            embed=styled_embed("Approved", f"**{business['name']}** is now live.", color=SUCCESS),
            view=None
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        app = await get_application(self.app_id)
        await reject_application(self.app_id)
        try:
            member = interaction.guild.get_member(app["owner_id"])
            if member:
                await member.send(embed=styled_embed(
                    "Business Rejected",
                    f"Your application for **{app['name']}** was not approved.",
                    color=DANGER
                ))
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=styled_embed("Rejected", "Application rejected.", color=DANGER), view=None
        )


class ExpansionReviewView(discord.ui.View):
    def __init__(self, proposal_id: int, estimated_revenue: float, owner_id: int):
        super().__init__(timeout=180)
        self.proposal_id = proposal_id
        self.estimated_revenue = estimated_revenue
        self.owner_id = owner_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ExpansionDecisionModal(self.proposal_id, "approved", self.estimated_revenue, self.owner_id)
        )

    @discord.ui.button(label="✏️ Modify & Approve", style=discord.ButtonStyle.primary)
    async def modify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ExpansionDecisionModal(self.proposal_id, "modified", self.estimated_revenue, self.owner_id)
        )

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ExpansionDecisionModal(self.proposal_id, "denied", self.estimated_revenue, self.owner_id)
        )


class ExpansionDecisionModal(discord.ui.Modal, title="Expansion Decision"):
    reason = discord.ui.TextInput(
        label="Roleplay Reason / Admin Note",
        style=discord.TextStyle.paragraph,
        placeholder="Explain your decision in roleplay terms...",
        max_length=500
    )
    revenue_override = discord.ui.TextInput(
        label="Revenue Increase (leave blank to use estimate or 0 to deny)",
        placeholder="e.g. 300  (only used for approve/modify)",
        required=False,
        max_length=16
    )

    def __init__(self, proposal_id: int, decision: str, estimated_revenue: float, owner_id: int):
        super().__init__()
        self.proposal_id = proposal_id
        self.decision = decision
        self.estimated_revenue = estimated_revenue
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        approved_rev = None
        if self.decision in ("approved", "modified"):
            rev_str = self.revenue_override.value.strip()
            if rev_str:
                try:
                    approved_rev = float(rev_str.replace(",", ""))
                except ValueError:
                    await interaction.followup.send("Invalid revenue amount.", ephemeral=True)
                    return
            else:
                approved_rev = self.estimated_revenue

        final_status = "approved" if self.decision != "denied" else "denied"

        # Fetch proposal title BEFORE resolve (the JOIN may break after cascade deletes)
        pool = get_pool()
        proposal = await pool.fetchrow("SELECT title FROM expansion_proposals WHERE id = $1", self.proposal_id)
        proposal_title = proposal["title"] if proposal else f"#{self.proposal_id}"

        await resolve_expansion(self.proposal_id, final_status, self.reason.value, approved_rev)

        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        sym = guild_row["currency_symbol"]

        # DM the business owner
        try:
            member = interaction.guild.get_member(self.owner_id)
            if member:
                if final_status == "approved":
                    msg_body = (
                        f"Your expansion **{proposal_title}** was **approved**!\n\n"
                        f"**Revenue Added:** {sym}{approved_rev:,.2f}\n\n"
                        f"**Admin Note:** _{self.reason.value}_"
                    )
                    color = SUCCESS
                else:
                    msg_body = (
                        f"Your expansion **{proposal_title}** was **denied**.\n\n"
                        f"**Admin Note:** _{self.reason.value}_"
                    )
                    color = DANGER
                await member.send(embed=styled_embed("Expansion Decision", msg_body, color=color))
        except Exception:
            pass

        if final_status == "approved":
            result_text = f"approved (+{sym}{approved_rev:,.2f})"
        else:
            result_text = "denied"

        await interaction.followup.send(
            embed=styled_embed("Decision Recorded", f"Proposal #{self.proposal_id} {result_text}.", color=SUCCESS),
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Businesses(bot))

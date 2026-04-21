import discord
from discord import app_commands
from discord.ext import commands
from utils.helpers import admin_check, ensure_guild, get_guild, styled_embed, ACCENT, SUCCESS, DANGER, WARNING
from db.queries.wallets import (
    get_or_create_wallet, add_cash, transfer_cash_to_digital,
    transfer_digital_to_cash, admin_grant, admin_deduct
)
from db.queries.businesses import get_businesses_by_owner
from db.connection import get_pool
from datetime import datetime, timezone

WORK_COOLDOWN_MINUTES = 60

# ── Job definitions ───────────────────────────────────────────────────────────
# Each job: min_xp to unlock, base_reward, xp_per_work, description

JOBS = {
    "unemployed": {
        "label": "Unemployed",
        "min_xp": 0,
        "reward": 50.0,
        "xp_gain": 10,
        "description": "No job yet. Work odd jobs for small pay.",
        "emoji": "🪣",
    },
    "janitor": {
        "label": "Janitor",
        "min_xp": 0,
        "reward": 80.0,
        "xp_gain": 12,
        "description": "Clean up around the city.",
        "emoji": "🧹",
    },
    "delivery_driver": {
        "label": "Delivery Driver",
        "min_xp": 100,
        "reward": 120.0,
        "xp_gain": 15,
        "description": "Deliver packages across town.",
        "emoji": "🚚",
    },
    "mechanic": {
        "label": "Mechanic",
        "min_xp": 250,
        "reward": 180.0,
        "xp_gain": 20,
        "description": "Fix vehicles at the local garage.",
        "emoji": "🔧",
    },
    "programmer": {
        "label": "Programmer",
        "min_xp": 500,
        "reward": 250.0,
        "xp_gain": 25,
        "description": "Write software for companies.",
        "emoji": "💻",
    },
    "doctor": {
        "label": "Doctor",
        "min_xp": 1000,
        "reward": 400.0,
        "xp_gain": 35,
        "description": "Treat patients at the hospital.",
        "emoji": "🩺",
    },
    "lawyer": {
        "label": "Lawyer",
        "min_xp": 1500,
        "reward": 500.0,
        "xp_gain": 40,
        "description": "Represent clients in court.",
        "emoji": "⚖️",
    },
    "ceo": {
        "label": "CEO",
        "min_xp": 3000,
        "reward": 800.0,
        "xp_gain": 60,
        "description": "Run a major corporation.",
        "emoji": "🏢",
    },
}

XP_LEVEL_THRESHOLDS = [0, 100, 250, 500, 1000, 1500, 3000]


def xp_to_level(xp: int) -> int:
    level = 0
    for threshold in XP_LEVEL_THRESHOLDS:
        if xp >= threshold:
            level += 1
    return level


async def get_or_create_xp(guild_id: int, user_id: int) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM user_experience WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id
    )
    if not row:
        row = await pool.fetchrow(
            """INSERT INTO user_experience (guild_id, user_id)
               VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING *""",
            guild_id, user_id
        )
        if not row:
            row = await pool.fetchrow(
                "SELECT * FROM user_experience WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id
            )
    return dict(row)


# ── Work handler (shared by menu button) ─────────────────────────────────────

async def handle_work(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    xp_row = await get_or_create_xp(interaction.guild_id, interaction.user.id)

    now = datetime.now(timezone.utc)
    if xp_row["last_work"]:
        last = xp_row["last_work"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds() / 60
        if elapsed < WORK_COOLDOWN_MINUTES:
            remaining = int(WORK_COOLDOWN_MINUTES - elapsed)
            embed = styled_embed("On Cooldown",
                f"You can work again in **{remaining} minutes**.", color=WARNING)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

    job_key = xp_row["job"] if xp_row["job"] in JOBS else "unemployed"
    job = JOBS[job_key]
    reward = job["reward"]
    xp_gain = job["xp_gain"]

    await add_cash(interaction.guild_id, interaction.user.id, reward)
    new_xp = xp_row["xp"] + xp_gain
    await pool.execute(
        "UPDATE user_experience SET xp = $1, last_work = NOW() WHERE guild_id = $2 AND user_id = $3",
        new_xp, interaction.guild_id, interaction.user.id
    )

    sym = guild_row["currency_symbol"]
    level = xp_to_level(new_xp)
    embed = styled_embed(
        f"{job['emoji']} Work Complete",
        f"You worked as a **{job['label']}** and earned **{sym}{reward:,.2f}**.\n"
        f"XP: **{new_xp}** (+{xp_gain})  |  Level **{level}**\n\n"
        f"Next shift available in {WORK_COOLDOWN_MINUTES} minutes.",
        color=SUCCESS
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── Jobs picker view ──────────────────────────────────────────────────────────

class JobPickerView(discord.ui.View):
    def __init__(self, xp: int, current_job: str, guild_row: dict):
        super().__init__(timeout=90)
        self.xp = xp
        self.current_job = current_job
        self.guild_row = guild_row
        self._build_select()

    def _build_select(self):
        options = []
        for key, job in JOBS.items():
            if key == "unemployed":
                continue
            unlocked = self.xp >= job["min_xp"]
            label = f"{job['emoji']} {job['label']}"
            if not unlocked:
                label += f" (need {job['min_xp']} XP)"
            desc = f"Pay: {job['reward']} | XP/work: +{job['xp_gain']} | {job['description']}"
            options.append(discord.SelectOption(
                label=label[:100],
                value=key,
                description=desc[:100],
                default=(key == self.current_job),
                emoji=None  # already in label
            ))
        select = discord.ui.Select(
            placeholder="Choose your job...",
            options=options,
            min_values=1,
            max_values=1
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        job_key = interaction.data["values"][0]
        job = JOBS[job_key]
        xp_row = await get_or_create_xp(interaction.guild_id, interaction.user.id)

        if xp_row["xp"] < job["min_xp"]:
            await interaction.response.send_message(
                f"You need **{job['min_xp']} XP** to become a {job['label']}. "
                f"You have **{xp_row['xp']} XP**.",
                ephemeral=True
            )
            return

        pool = get_pool()
        await pool.execute(
            "UPDATE user_experience SET job = $1 WHERE guild_id = $2 AND user_id = $3",
            job_key, interaction.guild_id, interaction.user.id
        )
        embed = styled_embed(
            f"Job Changed: {job['emoji']} {job['label']}",
            f"You are now working as a **{job['label']}**.\n"
            f"Pay per shift: **{self.guild_row['currency_symbol']}{job['reward']:,.2f}**\n"
            f"XP per shift: **+{job['xp_gain']}**\n\n"
            f"_{job['description']}_",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Wallet detail view ────────────────────────────────────────────────────────

class WalletTypeView(discord.ui.View):
    def __init__(self, guild_row: dict):
        super().__init__(timeout=60)
        self.guild_row = guild_row

    @discord.ui.button(label="Cash Wallet", style=discord.ButtonStyle.secondary)
    async def cash_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        wallet = await get_or_create_wallet(interaction.guild_id, interaction.user.id)
        g = self.guild_row
        sym = g["currency_symbol"]
        usd = float(wallet["cash_balance"]) / float(g["usd_rate"])
        embed = styled_embed(
            "Cash Wallet",
            f"**Balance:** {sym}{wallet['cash_balance']:,.2f}\n"
            f"**USD Equivalent:** ${usd:,.2f}\n\n"
            f"Cash is physical currency — earned from work and daily claims.",
            color=ACCENT
        )
        await interaction.response.send_message(embed=embed, view=CashActionsView(g), ephemeral=True)

    @discord.ui.button(label="Digital Wallet", style=discord.ButtonStyle.secondary)
    async def digital_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        wallet = await get_or_create_wallet(interaction.guild_id, interaction.user.id)
        g = self.guild_row
        sym = g["currency_symbol"]
        usd = float(wallet["digital_balance"]) / float(g["usd_rate"])
        embed = styled_embed(
            "Digital Wallet",
            f"**Balance:** {sym}{wallet['digital_balance']:,.2f}\n"
            f"**USD Equivalent:** ${usd:,.2f}\n\n"
            f"Digital currency is used for stock trading and transfers.",
            color=ACCENT
        )
        await interaction.response.send_message(embed=embed, view=DigitalActionsView(g), ephemeral=True)


class CashActionsView(discord.ui.View):
    def __init__(self, guild_row: dict):
        super().__init__(timeout=120)
        self.guild_row = guild_row

    @discord.ui.button(label="Convert to Digital", style=discord.ButtonStyle.primary)
    async def convert(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConvertModal(self.guild_row, direction="to_digital"))


class DigitalActionsView(discord.ui.View):
    def __init__(self, guild_row: dict):
        super().__init__(timeout=120)
        self.guild_row = guild_row

    @discord.ui.button(label="Convert to Cash", style=discord.ButtonStyle.primary)
    async def convert(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConvertModal(self.guild_row, direction="to_cash"))


class ConvertModal(discord.ui.Modal, title="Convert Currency"):
    amount = discord.ui.TextInput(label="Amount", placeholder="e.g. 100", min_length=1, max_length=20)

    def __init__(self, guild_row: dict, direction: str):
        super().__init__()
        self.guild_row = guild_row
        self.direction = direction

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amt = float(self.amount.value.replace(",", ""))
            if amt <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return

        if self.direction == "to_digital":
            ok = await transfer_cash_to_digital(interaction.guild_id, interaction.user.id, amt)
        else:
            ok = await transfer_digital_to_cash(interaction.guild_id, interaction.user.id, amt)

        sym = self.guild_row["currency_symbol"]
        if ok:
            dest = "digital" if self.direction == "to_digital" else "cash"
            embed = styled_embed("Conversion Successful",
                f"{sym}{amt:,.2f} moved to your {dest} wallet.", color=SUCCESS)
        else:
            embed = styled_embed("Insufficient Funds",
                "You don't have enough to complete this transfer.", color=DANGER)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Business list view ────────────────────────────────────────────────────────

class BusinessMenuView(discord.ui.View):
    def __init__(self, guild_row: dict):
        super().__init__(timeout=60)
        self.guild_row = guild_row

    @discord.ui.button(label="My Businesses", style=discord.ButtonStyle.secondary)
    async def my_businesses(self, interaction: discord.Interaction, button: discord.ui.Button):
        businesses = await get_businesses_by_owner(interaction.guild_id, interaction.user.id)
        if not businesses:
            embed = styled_embed("Your Businesses", "You don't own any businesses yet.", color=WARNING)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        sym = self.guild_row["currency_symbol"]
        desc = ""
        for b in businesses:
            status = "Public 📈" if b["is_public"] else "Private 🔒"
            desc += f"**{b['name']}** — {b['industry']} ({status})\n{b['description']}\nRevenue: {sym}{b['revenue']:,.2f}\n\n"
        embed = styled_embed("Your Businesses", desc.strip(), color=ACCENT)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Apply for Business", style=discord.ButtonStyle.primary)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BusinessApplicationModal())


class BusinessApplicationModal(discord.ui.Modal, title="Business Application"):
    name = discord.ui.TextInput(label="Business Name", max_length=64)
    industry = discord.ui.TextInput(label="Industry", placeholder="e.g. Tech, Retail, Finance", max_length=64)
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        max_length=300,
        placeholder="Describe what your business does..."
    )

    async def on_submit(self, interaction: discord.Interaction):
        from db.queries.businesses import create_application
        app_id = await create_application(
            interaction.guild_id,
            interaction.user.id,
            self.name.value,
            self.description.value,
            self.industry.value
        )
        embed = styled_embed(
            "Application Submitted",
            f"Your business **{self.name.value}** has been submitted for admin review.\n"
            f"Application ID: `#{app_id}`\n\n"
            f"You'll be notified once it's reviewed.",
            color=SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        pool = get_pool()
        guild_row = await pool.fetchrow(
            "SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id
        )
        if guild_row and guild_row["admin_role_id"]:
            role = interaction.guild.get_role(guild_row["admin_role_id"])
            if role:
                try:
                    channel = interaction.channel
                    notify = styled_embed(
                        "New Business Application",
                        f"**{self.name.value}** by {interaction.user.mention}\n"
                        f"Industry: {self.industry.value}\n"
                        f"ID: `#{app_id}`\n\n"
                        f"Use `/review_application {app_id}` to approve or reject.",
                        color=WARNING
                    )
                    await channel.send(content=role.mention, embed=notify)
                except Exception:
                    pass


# ── Main player menu view ─────────────────────────────────────────────────────

class PlayerMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 Wallet", style=discord.ButtonStyle.secondary, custom_id="menu:wallet")
    async def wallet_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        embed = styled_embed("Wallet", "Select which wallet you'd like to view.", color=ACCENT)
        await interaction.response.send_message(embed=embed, view=WalletTypeView(guild_row), ephemeral=True)

    @discord.ui.button(label="🏢 Businesses", style=discord.ButtonStyle.secondary, custom_id="menu:businesses")
    async def businesses_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        embed = styled_embed("Businesses", "Manage your businesses or apply for a new one.", color=ACCENT)
        await interaction.response.send_message(embed=embed, view=BusinessMenuView(guild_row), ephemeral=True)

    @discord.ui.button(label="⚒️ Work", style=discord.ButtonStyle.primary, custom_id="menu:work")
    async def work_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_guild(interaction.guild_id)
        await handle_work(interaction)

    @discord.ui.button(label="🎯 Jobs", style=discord.ButtonStyle.secondary, custom_id="menu:jobs")
    async def jobs_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        xp_row = await get_or_create_xp(interaction.guild_id, interaction.user.id)
        xp = xp_row["xp"]
        level = xp_to_level(xp)
        current_job = xp_row["job"] if xp_row["job"] in JOBS else "unemployed"
        job = JOBS[current_job]

        embed = styled_embed(
            "🎯 Jobs",
            f"**Current Job:** {job['emoji']} {job['label']}\n"
            f"**XP:** {xp}  |  **Level:** {level}\n\n"
            f"Select a job below to switch. Jobs with higher XP requirements pay more.\n"
            f"You earn XP every time you work.",
            color=ACCENT
        )
        await interaction.response.send_message(
            embed=embed,
            view=JobPickerView(xp, current_job, guild_row),
            ephemeral=True
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

class Menu(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(PlayerMenuView())

    @app_commands.command(name="post_menu", description="Post the player menu embed in the configured channel.")
    async def post_menu(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        if not guild_row["menu_channel_id"]:
            await interaction.response.send_message(
                "No menu channel set. Use `/setup_menu_channel` first.", ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(guild_row["menu_channel_id"])
        if not channel:
            await interaction.response.send_message("Menu channel not found.", ephemeral=True)
            return

        sym = guild_row["currency_symbol"]
        name = guild_row["currency_name"]
        usd_rate = guild_row["usd_rate"]

        embed = styled_embed(
            interaction.guild.name + " — Economy",
            f"**Currency:** {name} ({sym})\n"
            f"**Rate:** 1 USD = {usd_rate} {name}\n\n"
            f"Use the buttons below to manage your wallet, businesses, work, and career.",
            color=ACCENT
        )
        embed.add_field(name="💰 Wallet", value="View cash & digital balances and convert between them.", inline=False)
        embed.add_field(name="🏢 Businesses", value="Check your businesses or apply to start a new one.", inline=False)
        embed.add_field(name="⚒️ Work", value=f"Work your current job to earn {sym} and gain XP.", inline=False)
        embed.add_field(name="🎯 Jobs", value="Browse and switch jobs based on your XP level.", inline=False)

        msg = await channel.send(embed=embed, view=PlayerMenuView())
        await pool.execute(
            "UPDATE guilds SET menu_message_id = $1 WHERE guild_id = $2",
            msg.id, interaction.guild_id
        )
        await interaction.response.send_message("Menu posted.", ephemeral=True)

    @app_commands.command(name="admin_grant", description="Grant currency to a user.")
    @app_commands.describe(user="Target user", amount="Amount to grant", wallet="cash or digital")
    @app_commands.choices(wallet=[
        app_commands.Choice(name="Cash", value="cash"),
        app_commands.Choice(name="Digital", value="digital"),
    ])
    async def admin_grant_cmd(self, interaction: discord.Interaction, user: discord.Member, amount: float, wallet: str = "cash"):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        await admin_grant(interaction.guild_id, user.id, amount, wallet)
        sym = guild_row["currency_symbol"]
        embed = styled_embed("Currency Granted",
            f"Granted {sym}{amount:,.2f} to {user.mention}'s {wallet} wallet.", color=SUCCESS)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="admin_deduct", description="Deduct currency from a user.")
    @app_commands.describe(user="Target user", amount="Amount to deduct", wallet="cash or digital")
    @app_commands.choices(wallet=[
        app_commands.Choice(name="Cash", value="cash"),
        app_commands.Choice(name="Digital", value="digital"),
    ])
    async def admin_deduct_cmd(self, interaction: discord.Interaction, user: discord.Member, amount: float, wallet: str = "cash"):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        ok = await admin_deduct(interaction.guild_id, user.id, amount, wallet)
        sym = guild_row["currency_symbol"]
        if ok:
            embed = styled_embed("Currency Deducted",
                f"Deducted {sym}{amount:,.2f} from {user.mention}'s {wallet} wallet.", color=SUCCESS)
        else:
            embed = styled_embed("Insufficient Funds",
                f"{user.mention} doesn't have enough in their {wallet} wallet.", color=DANGER)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Menu(bot))

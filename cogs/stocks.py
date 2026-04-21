import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
from utils.helpers import admin_check, ensure_guild, styled_embed, ACCENT, SUCCESS, DANGER, WARNING
from utils.graphs import generate_market_overview, generate_business_chart
from db.queries.stocks import (
    get_all_stocks, get_stock_by_ticker, get_price_history,
    tick_all_stocks, buy_stock, sell_stock, get_holdings,
    create_stock, delete_stock
)
from db.queries.wallets import get_or_create_wallet
from db.connection import get_pool

TICK_INTERVAL_MINUTES = 5
MAX_STOCKS_PER_BOARD = 25  # 5x5 grid per message


# ── Stock trading view ────────────────────────────────────────────────────────

class StockMarketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buy Stock", style=discord.ButtonStyle.success, custom_id="stock:buy")
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BuyStockModal())

    @discord.ui.button(label="Sell Stock", style=discord.ButtonStyle.danger, custom_id="stock:sell")
    async def sell_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SellStockModal())

    @discord.ui.button(label="My Portfolio", style=discord.ButtonStyle.secondary, custom_id="stock:portfolio")
    async def portfolio_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await show_portfolio(interaction)

    @discord.ui.button(label="Stock Info", style=discord.ButtonStyle.secondary, custom_id="stock:info")
    async def info_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StockInfoModal())


class BuyStockModal(discord.ui.Modal, title="Buy Stock"):
    ticker = discord.ui.TextInput(label="Ticker", placeholder="e.g. APPL", max_length=8)
    shares = discord.ui.TextInput(label="Shares", placeholder="e.g. 10", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        stock = await get_stock_by_ticker(interaction.guild_id, self.ticker.value.upper())
        if not stock:
            await interaction.followup.send(
                embed=styled_embed("Not Found", f"Ticker `{self.ticker.value.upper()}` not found.", color=DANGER),
                ephemeral=True
            )
            return
        # Block trading pre-IPO business stocks
        if stock["stock_type"] == "business" and not stock["ipo_completed"]:
            await interaction.followup.send(
                embed=styled_embed("IPO Pending", "This stock's IPO has not completed yet.", color=WARNING),
                ephemeral=True
            )
            return
        try:
            num_shares = float(self.shares.value.replace(",", ""))
            if num_shares <= 0:
                raise ValueError
        except ValueError:
            await interaction.followup.send("Invalid share amount.", ephemeral=True)
            return

        total_cost = float(stock["current_price"]) * num_shares
        wallet = await get_or_create_wallet(interaction.guild_id, interaction.user.id)
        if float(wallet["digital_balance"]) < total_cost:
            sym = guild_row["currency_symbol"]
            await interaction.followup.send(
                embed=styled_embed("Insufficient Funds",
                    f"You need **{sym}{total_cost:,.2f}** in your digital wallet.\n"
                    f"Current balance: **{sym}{wallet['digital_balance']:,.2f}**", color=DANGER),
                ephemeral=True
            )
            return

        await pool.execute(
            "UPDATE wallets SET digital_balance = digital_balance - $1 WHERE guild_id = $2 AND user_id = $3",
            total_cost, interaction.guild_id, interaction.user.id
        )
        await buy_stock(interaction.guild_id, interaction.user.id, stock["id"], num_shares)
        sym = guild_row["currency_symbol"]
        embed = styled_embed("Purchase Successful",
            f"Bought **{num_shares} {stock['ticker']}** shares\n"
            f"Price per share: {sym}{stock['current_price']:,.4f}\n"
            f"Total spent: {sym}{total_cost:,.2f}", color=SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)


class SellStockModal(discord.ui.Modal, title="Sell Stock"):
    ticker = discord.ui.TextInput(label="Ticker", placeholder="e.g. APPL", max_length=8)
    shares = discord.ui.TextInput(label="Shares", placeholder="e.g. 10", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        stock = await get_stock_by_ticker(interaction.guild_id, self.ticker.value.upper())
        if not stock:
            await interaction.followup.send(
                embed=styled_embed("Not Found", f"Ticker `{self.ticker.value.upper()}` not found.", color=DANGER),
                ephemeral=True
            )
            return
        try:
            num_shares = float(self.shares.value.replace(",", ""))
            if num_shares <= 0:
                raise ValueError
        except ValueError:
            await interaction.followup.send("Invalid share amount.", ephemeral=True)
            return

        ok = await sell_stock(interaction.guild_id, interaction.user.id, stock["id"], num_shares)
        if not ok:
            await interaction.followup.send(
                embed=styled_embed("Insufficient Shares", "You don't have enough shares to sell.", color=DANGER),
                ephemeral=True
            )
            return

        total_value = float(stock["current_price"]) * num_shares
        await pool.execute(
            "UPDATE wallets SET digital_balance = digital_balance + $1 WHERE guild_id = $2 AND user_id = $3",
            total_value, interaction.guild_id, interaction.user.id
        )
        sym = guild_row["currency_symbol"]
        embed = styled_embed("Sale Successful",
            f"Sold **{num_shares} {stock['ticker']}** shares\n"
            f"Price per share: {sym}{stock['current_price']:,.4f}\n"
            f"Total received: {sym}{total_value:,.2f}", color=SUCCESS)
        await interaction.followup.send(embed=embed, ephemeral=True)


class StockInfoModal(discord.ui.Modal, title="Stock Info"):
    ticker = discord.ui.TextInput(label="Ticker", placeholder="e.g. APPL", max_length=8)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        stock = await get_stock_by_ticker(interaction.guild_id, self.ticker.value.upper())
        if not stock:
            await interaction.followup.send(
                embed=styled_embed("Not Found", f"Ticker `{self.ticker.value.upper()}` not found.", color=DANGER),
                ephemeral=True
            )
            return
        history = await get_price_history(stock["id"], limit=60)
        sym = guild_row["currency_symbol"]
        prices = [float(r["price"]) for r in history]
        high = max(prices) if prices else stock["current_price"]
        low = min(prices) if prices else stock["current_price"]

        ipo_line = ""
        if stock["stock_type"] == "business" and stock["ipo_price"]:
            ipo_line = f"\n**IPO Price:** {sym}{stock['ipo_price']:,.4f}"

        embed = styled_embed(
            f"{stock['ticker']} — {stock['name']}",
            f"**Type:** {stock['stock_type'].title()}\n"
            f"**Current Price:** {sym}{stock['current_price']:,.4f}\n"
            f"**High (recent):** {sym}{high:,.4f}\n"
            f"**Low (recent):** {sym}{low:,.4f}{ipo_line}",
            color=ACCENT
        )
        chart_buf = generate_business_chart(
            stock["ticker"], stock["name"],
            [(r["price"], r["recorded_at"]) for r in history]
        )
        file = discord.File(chart_buf, filename="chart.png")
        embed.set_image(url="attachment://chart.png")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


async def show_portfolio(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    pool = get_pool()
    guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
    holdings = await get_holdings(interaction.guild_id, interaction.user.id)
    if not holdings:
        await interaction.followup.send(
            embed=styled_embed("Portfolio", "You don't own any stocks yet.", color=WARNING),
            ephemeral=True
        )
        return
    sym = guild_row["currency_symbol"]
    total_value = sum(float(h["shares"]) * float(h["current_price"]) for h in holdings)
    desc = ""
    for h in holdings:
        val = float(h["shares"]) * float(h["current_price"])
        desc += f"**{h['ticker']}** — {h['shares']:.2f} shares @ {sym}{h['current_price']:,.4f} = {sym}{val:,.2f}\n"
    desc += f"\n**Total Value:** {sym}{total_value:,.2f}"
    embed = styled_embed("Your Portfolio", desc, color=ACCENT)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class StockMarket(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(StockMarketView())
        self.stock_tick.start()

    def cog_unload(self):
        self.stock_tick.cancel()

    @tasks.loop(minutes=TICK_INTERVAL_MINUTES)
    async def stock_tick(self):
        pool = get_pool()
        guilds = await pool.fetch("SELECT * FROM guilds WHERE stock_channel_id IS NOT NULL")
        for guild_row in guilds:
            guild_id = guild_row["guild_id"]
            await tick_all_stocks(guild_id)
            await self._refresh_market_board(guild_row)

    @stock_tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    async def _refresh_market_board(self, guild_row: dict):
        guild = self.bot.get_guild(guild_row["guild_id"])
        if not guild:
            return
        channel = guild.get_channel(guild_row["stock_channel_id"])
        if not channel:
            return

        stocks = await get_all_stocks(guild_row["guild_id"], public_only=True)
        if not stocks:
            return

        stocks_data = []
        for s in stocks:
            history = await get_price_history(s["id"], limit=30)
            stocks_data.append({
                "ticker": s["ticker"],
                "name": s["name"],
                "current_price": float(s["current_price"]),
                "history": [(float(r["price"]), r["recorded_at"]) for r in history]
            })

        sym = guild_row["currency_symbol"]
        name = guild_row["currency_name"]
        usd_rate = guild_row["usd_rate"]

        # Delete all old board messages stored in stock_message_id (we store first page id)
        pool = get_pool()
        msg_id = guild_row["stock_message_id"]
        if msg_id:
            # Delete all messages we previously posted (walk channel history to clean up)
            try:
                old_msg = await channel.fetch_message(msg_id)
                await old_msg.delete()
            except Exception:
                pass
            # Also try to delete overflow pages stored as pinned or via metadata
            # Simple: delete last N messages in channel that were from bot
            try:
                async for msg in channel.history(limit=50):
                    if msg.author.id == self.bot.user.id:
                        await msg.delete()
            except Exception:
                pass

        # Split into pages of MAX_STOCKS_PER_BOARD
        pages = [stocks_data[i:i + MAX_STOCKS_PER_BOARD]
                 for i in range(0, len(stocks_data), MAX_STOCKS_PER_BOARD)]
        total_pages = len(pages)
        first_msg_id = None

        for page_idx, page_stocks in enumerate(pages):
            lines = []
            for s in page_stocks:
                prices = [p for p, _ in s["history"]][::-1]
                if len(prices) >= 2:
                    chg = (prices[-1] - prices[0]) / max(prices[0], 0.0001) * 100
                    arrow = "▲" if chg >= 0 else "▼"
                    lines.append(f"`{s['ticker']:<6}` {sym}{s['current_price']:<10.4f} {arrow} {chg:+.2f}%")
                else:
                    lines.append(f"`{s['ticker']:<6}` {sym}{s['current_price']:<10.4f}")

            page_label = f"Page {page_idx + 1}/{total_pages}" if total_pages > 1 else ""
            embed = styled_embed(
                guild.name + " — Stock Market" + (f"  ({page_label})" if page_label else ""),
                "\n".join(lines) if lines else "No stocks listed yet.",
                color=ACCENT
            )
            if page_idx == 0:
                embed.add_field(name="Currency", value=f"{name} ({sym})", inline=True)
                embed.add_field(name="USD Rate", value=f"1 USD = {usd_rate} {name}", inline=True)
            embed.set_footer(text=f"Updates every {TICK_INTERVAL_MINUTES} minutes  |  Economy System")

            chart_buf = generate_market_overview(page_stocks)
            file = discord.File(chart_buf, filename=f"market_{page_idx}.png")
            embed.set_image(url=f"attachment://market_{page_idx}.png")

            # Only first page gets the trading view buttons
            view = StockMarketView() if page_idx == 0 else None
            new_msg = await channel.send(embed=embed, view=view, file=file)
            if page_idx == 0:
                first_msg_id = new_msg.id

        if first_msg_id:
            await pool.execute(
                "UPDATE guilds SET stock_message_id = $1 WHERE guild_id = $2",
                first_msg_id, guild_row["guild_id"]
            )

    @app_commands.command(name="post_stockmarket", description="Post the live stock market board.")
    async def post_stockmarket(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pool = get_pool()
        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        if not guild_row["stock_channel_id"]:
            await interaction.response.send_message(
                "No stock channel set. Use `/setup_stock_channel` first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self._refresh_market_board(guild_row)
        await interaction.followup.send("Stock market board posted.", ephemeral=True)

    @app_commands.command(name="add_simulated_stock", description="Add a simulated stock to the market.")
    @app_commands.describe(ticker="Ticker symbol (e.g. TECH)", name="Full stock name", price="Starting price")
    async def add_simulated_stock(self, interaction: discord.Interaction, ticker: str, name: str, price: float = 10.0):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        stock = await create_stock(
            interaction.guild_id, ticker.upper(), name,
            stock_type="simulated", initial_price=price
        )
        if not stock:
            await interaction.response.send_message(
                f"Ticker `{ticker.upper()}` already exists.", ephemeral=True
            )
            return
        embed = styled_embed("Stock Added",
            f"**{ticker.upper()}** — {name} added at {price:.4f}.", color=SUCCESS)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="remove_simulated_stock", description="Remove a simulated stock from the market.")
    @app_commands.describe(ticker="Ticker symbol to remove (simulated stocks only)")
    async def remove_simulated_stock(self, interaction: discord.Interaction, ticker: str):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        ok = await delete_stock(interaction.guild_id, ticker.upper())
        if not ok:
            await interaction.response.send_message(
                f"Ticker `{ticker.upper()}` not found or is a business stock (cannot be removed this way).",
                ephemeral=True
            )
            return
        embed = styled_embed("Stock Removed",
            f"Simulated stock **{ticker.upper()}** has been removed from the market.\n"
            f"All holdings of this stock have been cleared.",
            color=SUCCESS)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="market_event", description="Trigger a market event that affects all stock prices.")
    @app_commands.describe(
        title="Event title",
        description="What happened",
        impact="Percentage impact (-50 to +50)"
    )
    async def market_event(self, interaction: discord.Interaction, title: str, description: str, impact: float):
        if not await admin_check(interaction):
            return
        impact = max(-50.0, min(50.0, impact))
        multiplier = 1.0 + (impact / 100.0)
        await tick_all_stocks(interaction.guild_id, event_multiplier=multiplier)

        pool = get_pool()
        await pool.execute(
            """INSERT INTO market_events (guild_id, title, description, impact, triggered_by)
               VALUES ($1, $2, $3, $4, $5)""",
            interaction.guild_id, title, description, impact, interaction.user.id
        )

        guild_row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", interaction.guild_id)
        await self._refresh_market_board(guild_row)

        sign = "+" if impact >= 0 else ""
        color = SUCCESS if impact >= 0 else DANGER
        embed = styled_embed(
            f"Market Event: {title}",
            f"{description}\n\n**Market Impact:** {sign}{impact:.1f}%",
            color=color
        )

        if guild_row["stock_channel_id"]:
            channel = interaction.guild.get_channel(guild_row["stock_channel_id"])
            if channel:
                await channel.send(embed=embed)

        await interaction.response.send_message("Market event triggered.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StockMarket(bot))

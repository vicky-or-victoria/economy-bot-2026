from __future__ import annotations

import asyncio
import random
import math
import discord
from discord import app_commands
from discord.ext import commands

from db.queries.casino import (
    get_chips, add_chips, set_chips,
    get_cash, transfer_cash_to_chips, cashout_chips,
    add_to_house_pot, get_house_pot,
    get_cooldown_seconds, stamp_cooldown,
    get_casino_settings, set_casino_field,
)
from utils.helpers import (
    is_admin, admin_check, ensure_guild,
    styled_embed, styled_embed_formal,
    ACCENT, SUCCESS, DANGER, WARNING,
)


# ══════════════════════════════════════════════════════════════════════════════
#  GUARD HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def casino_guard(interaction: discord.Interaction, bet: float | None = None) -> dict | None:
    """
    Run all pre-game checks. Returns the guild settings dict on pass, None on fail.
    Failures respond ephemerally so the game channel stays clean.
    """
    settings = await get_casino_settings(interaction.guild_id)

    if not settings.get("casino_enabled", True):
        await interaction.response.send_message(
            embed=styled_embed(
                "🚫 Casino Closed",
                "The G.R.E.T.A. Casino Floor is currently closed by order of Universalis authorities.",
                color=DANGER
            ), ephemeral=True
        )
        return None

    floor_id = settings.get("casino_floor_channel_id")
    if floor_id and interaction.channel_id != floor_id:
        await interaction.response.send_message(
            embed=styled_embed(
                "Wrong Channel",
                f"Games may only be played in <#{floor_id}>.",
                color=WARNING
            ), ephemeral=True
        )
        return None

    if bet is not None:
        chips = await get_chips(interaction.guild_id, interaction.user.id)
        if chips < bet:
            await interaction.response.send_message(
                embed=styled_embed(
                    "Insufficient Chips",
                    f"You only have **{chips:,.2f}** chips. Visit the Chip Exchange to top up.",
                    color=WARNING
                ), ephemeral=True
            )
            return None

        max_bet = settings.get("casino_max_bet")
        if max_bet and bet > float(max_bet):
            await interaction.response.send_message(
                embed=styled_embed(
                    "Bet Exceeds Table Limit",
                    f"Your wager of **{bet:,.2f}** chips exceeds the current table limit of **{float(max_bet):,.2f}** chips.",
                    color=WARNING
                ), ephemeral=True
            )
            return None

        cooldown = int(settings.get("casino_cooldown", 5))
        remaining = await get_cooldown_seconds(interaction.guild_id, interaction.user.id, cooldown)
        if remaining > 0:
            await interaction.response.send_message(
                embed=styled_embed(
                    "Slow Down",
                    f"G.R.E.T.A. requires a brief pause between wagers. Please wait **{remaining:.1f}s**.",
                    color=WARNING
                ), ephemeral=True
            )
            return None

    return settings


async def apply_tax_and_pay(
    interaction: discord.Interaction,
    settings: dict,
    bet: float,
    gross_win: float,
) -> tuple[float, float]:
    """
    Deduct bet, apply winnings tax, credit net win.
    Returns (net_win_chips, tax_taken).
    Also stamps the cooldown.
    """
    tax_rate = float(settings.get("casino_tax_rate", 25.0))
    tax = round(gross_win * tax_rate / 100, 2)
    net  = round(gross_win - tax, 2)

    await add_chips(interaction.guild_id, interaction.user.id, net - bet)
    await add_to_house_pot(interaction.guild_id, tax)
    await stamp_cooldown(interaction.guild_id, interaction.user.id)
    return net, tax


async def apply_loss(interaction: discord.Interaction, bet: float) -> None:
    await add_chips(interaction.guild_id, interaction.user.id, -bet)
    await stamp_cooldown(interaction.guild_id, interaction.user.id)


def win_embed(title: str, desc: str) -> discord.Embed:
    return styled_embed(f"🏆 {title}", desc, color=SUCCESS)

def lose_embed(title: str, desc: str) -> discord.Embed:
    return styled_embed(f"💸 {title}", desc, color=DANGER)

def neutral_embed(title: str, desc: str) -> discord.Embed:
    return styled_embed(title, desc, color=ACCENT)


# ══════════════════════════════════════════════════════════════════════════════
#  CHIP EXCHANGE MENU
# ══════════════════════════════════════════════════════════════════════════════

class ChipAmountModal(discord.ui.Modal):
    amount_input = discord.ui.TextInput(
        label="Amount",
        placeholder="Enter amount (e.g. 500)",
        max_length=20
    )

    def __init__(self, action: str):
        super().__init__(title=f"{'Buy' if action == 'buy' else 'Cash Out'} Chips")
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.amount_input.value.replace(",", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Please enter a valid positive number.", ephemeral=True)
            return

        settings = await get_casino_settings(interaction.guild_id)
        sym = settings.get("currency_symbol", "C")

        if self.action == "buy":
            try:
                new_cash, new_chips = await transfer_cash_to_chips(
                    interaction.guild_id, interaction.user.id, amount
                )
                await interaction.response.send_message(
                    embed=styled_embed(
                        "Chips Purchased",
                        f"You exchanged **{sym}{amount:,.2f}** for **{amount:,.2f} chips**.\n\n"
                        f"**Chip Balance:** {new_chips:,.2f}\n"
                        f"**Wallet Balance:** {sym}{new_cash:,.2f}",
                        color=SUCCESS
                    ), ephemeral=True
                )
            except ValueError:
                await interaction.response.send_message(
                    embed=styled_embed("Insufficient Funds",
                        f"You don't have **{sym}{amount:,.2f}** in your wallet.", color=DANGER),
                    ephemeral=True
                )

        else:  # cashout
            try:
                received, fee, new_chips = await cashout_chips(
                    interaction.guild_id, interaction.user.id, amount
                )
                await add_to_house_pot(interaction.guild_id, fee)
                await interaction.response.send_message(
                    embed=styled_embed(
                        "Chips Cashed Out",
                        f"You cashed out **{amount:,.2f} chips**.\n\n"
                        f"**G.R.E.T.A. Fee (5%):** {fee:,.2f} chips\n"
                        f"**Cash Received:** {sym}{received:,.2f}\n"
                        f"**Remaining Chips:** {new_chips:,.2f}\n\n"
                        f"*A tithe remitted to G.R.E.T.A. in accordance with Universalis taxation law.*",
                        color=SUCCESS
                    ), ephemeral=True
                )
            except ValueError:
                await interaction.response.send_message(
                    embed=styled_embed("Insufficient Chips",
                        f"You don't have **{amount:,.2f}** chips to cash out.", color=DANGER),
                    ephemeral=True
                )


class ChipExchangeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🪙 Buy Chips", style=discord.ButtonStyle.success, custom_id="chip:buy")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_casino_settings(interaction.guild_id)
        ex_id = settings.get("chip_exchange_channel_id")
        if ex_id and interaction.channel_id != ex_id:
            await interaction.response.send_message(
                f"Chip exchange is only available in <#{ex_id}>.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ChipAmountModal("buy"))

    @discord.ui.button(label="💵 Cash Out", style=discord.ButtonStyle.danger, custom_id="chip:cashout")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await get_casino_settings(interaction.guild_id)
        ex_id = settings.get("chip_exchange_channel_id")
        if ex_id and interaction.channel_id != ex_id:
            await interaction.response.send_message(
                f"Chip exchange is only available in <#{ex_id}>.", ephemeral=True
            )
            return
        await interaction.response.send_modal(ChipAmountModal("cashout"))

    @discord.ui.button(label="📊 My Balance", style=discord.ButtonStyle.secondary, custom_id="chip:balance")
    async def balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        chips = await get_chips(interaction.guild_id, interaction.user.id)
        cash  = await get_cash(interaction.guild_id, interaction.user.id)
        settings = await get_casino_settings(interaction.guild_id)
        sym = settings.get("currency_symbol", "C")
        await interaction.response.send_message(
            embed=styled_embed(
                "Your G.R.E.T.A. Casino Balance",
                f"**Chips:** {chips:,.2f}\n"
                f"**Wallet:** {sym}{cash:,.2f}",
                color=ACCENT
            ), ephemeral=True
        )


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: COINFLIP
# ══════════════════════════════════════════════════════════════════════════════

COIN_FLIP_FRAMES = ["🌀", "🪙", "🌀", "🪙", "🌀"]
COIN_HEADS_ART  = "⬆️ **HEADS** ⬆️"
COIN_TAILS_ART  = "⬇️ **TAILS** ⬇️"

class CoinflipView(discord.ui.View):
    def __init__(self, bet: float, settings: dict):
        super().__init__(timeout=60)
        self.bet      = bet
        self.settings = settings
        self.resolved = False

    async def on_timeout(self):
        if not self.resolved:
            for item in self.children:
                item.disabled = True

    async def resolve(self, interaction: discord.Interaction, chosen: str):
        if self.resolved:
            await interaction.response.defer()
            return
        self.resolved = True
        for item in self.children:
            item.disabled = True

        # Acknowledge immediately so we can edit
        spinning_embed = neutral_embed(
            "🪙 Coinflip — Flipping...",
            f"You chose **{chosen}**\n\n"
            f"🌀  *The coin spins through the air...*  🌀\n\n"
            f"Bet: **{self.bet:,.2f}** chips"
        )
        await interaction.response.edit_message(embed=spinning_embed, view=self)
        message = await interaction.original_response()

        # Animate the flip
        flip_states = [
            ("🪙 Coinflip — Flipping...", "🌀  *tumbling...*  🌀"),
            ("🪙 Coinflip — Flipping...", "🟡  *it's in the air...*  🟡"),
            ("🪙 Coinflip — Flipping...", "🌀  *almost there...*  🌀"),
            ("🪙 Coinflip — Flipping...", "🟡  *landing...*  🟡"),
        ]
        for title, state in flip_states:
            await asyncio.sleep(0.7)
            e = neutral_embed(title, f"You chose **{chosen}**\n\n{state}\n\nBet: **{self.bet:,.2f}** chips")
            try:
                await message.edit(embed=e, view=self)
            except Exception:
                break

        await asyncio.sleep(0.8)
        result = random.choice(["Heads", "Tails"])
        won    = chosen == result
        side_art = COIN_HEADS_ART if result == "Heads" else COIN_TAILS_ART
        emoji    = "🟡" if result == "Heads" else "⚪"

        if won:
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, self.bet * 2)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                "🪙 Coinflip — You Win!",
                f"```\n  ╔══════════════╗\n  ║  {emoji}  {result.upper():<6}  {emoji}  ║\n  ╚══════════════╝\n```\n"
                f"The coin landed on **{result}**! 🎉\n\n"
                f"**Your Pick:** {chosen}  ✅\n"
                f"**Bet:** {self.bet:,.2f} chips\n"
                f"**Gross Win:** {self.bet * 2:,.2f} chips\n"
                f"**Tax (G.R.E.T.A. {self.settings.get('casino_tax_rate', 25)}%):** {tax:,.2f} chips\n"
                f"**Net Win:** +{net:,.2f} chips\n"
                f"**Balance:** {chips:,.2f} chips"
            )
        else:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "🪙 Coinflip — Bad Luck",
                f"```\n  ╔══════════════╗\n  ║  {emoji}  {result.upper():<6}  {emoji}  ║\n  ╚══════════════╝\n```\n"
                f"The coin landed on **{result}**.\n\n"
                f"**Your Pick:** {chosen}  ❌\n"
                f"**Lost:** {self.bet:,.2f} chips\n"
                f"**Balance:** {chips:,.2f} chips"
            )
        try:
            await message.edit(embed=embed, view=self)
        except Exception:
            pass

    @discord.ui.button(label="🪙 Heads", style=discord.ButtonStyle.primary)
    async def heads(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "Heads")

    @discord.ui.button(label="🪙 Tails", style=discord.ButtonStyle.secondary)
    async def tails(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "Tails")


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: DICE OVER/UNDER
# ══════════════════════════════════════════════════════════════════════════════

class DiceView(discord.ui.View):
    def __init__(self, bet: float, settings: dict):
        super().__init__(timeout=60)
        self.bet      = bet
        self.settings = settings
        self.resolved = False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def resolve(self, interaction: discord.Interaction, choice: str):
        if self.resolved:
            await interaction.response.defer()
            return
        self.resolved = True
        for item in self.children:
            item.disabled = True
        roll   = random.randint(1, 6)
        # Over = 4,5,6 | Under = 1,2,3
        won    = (choice == "over" and roll >= 4) or (choice == "under" and roll <= 3)
        payout = self.bet * 1.8
        if won:
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, payout)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                f"Dice — Rolled {roll}! You Win!",
                f"You called **{'Over 3' if choice == 'over' else 'Under 4'}** and rolled a **{roll}**! 🎲\n\n"
                f"**Gross Win:** {payout:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips\n"
                f"**New Balance:** {chips:,.2f} chips"
            )
        else:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                f"Dice — Rolled {roll}",
                f"You called **{'Over 3' if choice == 'over' else 'Under 4'}** but rolled a **{roll}**.\n\n"
                f"**Lost:** {self.bet:,.2f} chips\n"
                f"**New Balance:** {chips:,.2f} chips"
            )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⬆️ Over 3", style=discord.ButtonStyle.primary)
    async def over(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "over")

    @discord.ui.button(label="⬇️ Under 4", style=discord.ButtonStyle.danger)
    async def under(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "under")


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: HI-LO  (up to 5 chains, 1.9x per correct step)
# ══════════════════════════════════════════════════════════════════════════════

CARD_NAMES = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
CARD_VALUES = {c: i for i, c in enumerate(CARD_NAMES)}

class HiLoView(discord.ui.View):
    def __init__(self, bet: float, settings: dict, current_card: str, chain: int = 0, multiplier: float = 1.0):
        super().__init__(timeout=60)
        self.bet          = bet
        self.settings     = settings
        self.current_card = current_card
        self.chain        = chain
        self.multiplier   = multiplier

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def resolve(self, interaction: discord.Interaction, choice: str):
        for item in self.children:
            item.disabled = True
        next_card = random.choice(CARD_NAMES)
        cur_val   = CARD_VALUES[self.current_card]
        nxt_val   = CARD_VALUES[next_card]
        won       = (choice == "higher" and nxt_val > cur_val) or \
                    (choice == "lower"  and nxt_val < cur_val)
        # Tie always loses
        if nxt_val == cur_val:
            won = False

        if not won:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                f"Hi-Lo — {next_card}",
                f"Card was **{next_card}** (you called {'higher' if choice == 'higher' else 'lower'} from **{self.current_card}**).\n\n"
                f"**Lost:** {self.bet:,.2f} chips\n"
                f"**New Balance:** {chips:,.2f} chips"
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return

        new_multiplier = round(self.multiplier * 1.9, 4)
        new_chain      = self.chain + 1

        if new_chain >= 5:
            # Max chain reached — auto cash out
            gross = round(self.bet * new_multiplier, 2)
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                f"Hi-Lo — Max Chain! 🔥",
                f"Card was **{next_card}** — correct! You've hit the 5-chain limit!\n\n"
                f"**Multiplier:** {new_multiplier:.2f}x\n"
                f"**Gross Win:** {gross:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips\n"
                f"**New Balance:** {chips:,.2f} chips"
            )
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            # Offer to continue or cash out
            gross_if_cashout = round(self.bet * new_multiplier, 2)
            embed = neutral_embed(
                f"Hi-Lo — {next_card} ✅ (Chain {new_chain}/5)",
                f"Card was **{next_card}** — correct!\n\n"
                f"**Current Multiplier:** {new_multiplier:.2f}x\n"
                f"**Cash Out Now:** ~{gross_if_cashout:,.2f} chips (before tax)\n\n"
                f"Keep going or take your winnings?"
            )
            view = HiLoActiveView(self.bet, self.settings, next_card, new_chain, new_multiplier)
            await interaction.response.edit_message(embed=embed, view=view)


class HiLoActiveView(discord.ui.View):
    def __init__(self, bet, settings, current_card, chain, multiplier):
        super().__init__(timeout=60)
        self.bet          = bet
        self.settings     = settings
        self.current_card = current_card
        self.chain        = chain
        self.multiplier   = multiplier

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="⬆️ Higher", style=discord.ButtonStyle.success)
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HiLoView(self.bet, self.settings, self.current_card, self.chain, self.multiplier)
        await view.resolve(interaction, "higher")

    @discord.ui.button(label="⬇️ Lower", style=discord.ButtonStyle.danger)
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HiLoView(self.bet, self.settings, self.current_card, self.chain, self.multiplier)
        await view.resolve(interaction, "lower")

    @discord.ui.button(label="💰 Cash Out", style=discord.ButtonStyle.secondary)
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        gross    = round(self.bet * self.multiplier, 2)
        net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
        chips    = await get_chips(interaction.guild_id, interaction.user.id)
        embed = win_embed(
            "Hi-Lo — Cashed Out",
            f"You walked away after **{self.chain}** correct chain(s).\n\n"
            f"**Multiplier:** {self.multiplier:.2f}x\n"
            f"**Gross Win:** {gross:,.2f} chips\n"
            f"**Tax:** {tax:,.2f} chips\n"
            f"**Net Win:** {net:,.2f} chips\n"
            f"**New Balance:** {chips:,.2f} chips"
        )
        await interaction.response.edit_message(embed=embed, view=self)


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: KENO
# ══════════════════════════════════════════════════════════════════════════════

KENO_PAYOUTS = {0: 0, 1: 0.5, 2: 1.0, 3: 2.0, 4: 5.0, 5: 10.0,
                6: 25.0, 7: 60.0, 8: 150.0, 9: 300.0, 10: 500.0}

class KenoModal(discord.ui.Modal, title="Keno — Pick Your Numbers"):
    numbers_input = discord.ui.TextInput(
        label="Pick 1–10 numbers (1–40), comma-separated",
        placeholder="e.g. 3, 7, 15, 22, 38",
        max_length=100
    )

    def __init__(self, bet: float, settings: dict):
        super().__init__()
        self.bet      = bet
        self.settings = settings

    async def on_submit(self, interaction: discord.Interaction):
        try:
            picks = list({int(x.strip()) for x in self.numbers_input.value.split(",")})
            if not (1 <= len(picks) <= 10) or any(n < 1 or n > 40 for n in picks):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Please enter 1–10 unique numbers between 1 and 40.", ephemeral=True
            )
            return

        drawn   = sorted(random.sample(range(1, 41), 20))
        matches = [n for n in picks if n in drawn]
        payout  = self.bet * KENO_PAYOUTS.get(len(matches), 0)

        drawn_str   = " ".join(f"**{n}**" if n in picks else str(n) for n in drawn)
        picks_str   = " ".join(str(p) for p in sorted(picks))

        if payout > 0:
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, payout)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                f"Keno — {len(matches)} Match{'es' if len(matches) != 1 else ''}!",
                f"**Your Numbers:** {picks_str}\n"
                f"**Drawn:** {drawn_str}\n\n"
                f"**Matches:** {len(matches)}  |  **Multiplier:** {KENO_PAYOUTS[len(matches)]}x\n"
                f"**Gross Win:** {payout:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips\n"
                f"**New Balance:** {chips:,.2f} chips"
            )
        else:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                f"Keno — {len(matches)} Match{'es' if len(matches) != 1 else ''}",
                f"**Your Numbers:** {picks_str}\n"
                f"**Drawn:** {drawn_str}\n\n"
                f"**Matches:** {len(matches)}\n"
                f"**Lost:** {self.bet:,.2f} chips\n"
                f"**New Balance:** {chips:,.2f} chips"
            )
        await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: WHEEL OF FORTUNE
# ══════════════════════════════════════════════════════════════════════════════

WHEEL_SEGMENTS = [
    ("💀 BUST",   0.0,  10),
    ("0.5x",      0.5,  15),
    ("1.5x",      1.5,  30),
    ("2x",        2.0,  25),
    ("3x",        3.0,  15),
    ("5x ⭐",     5.0,   5),
]

WHEEL_DISPLAY_ORDER = ["💀 BUST", "0.5x", "1.5x", "2x", "3x", "5x ⭐"]

def spin_wheel():
    segments, weights = zip(*[(s, w) for s, _, w in WHEEL_SEGMENTS])
    chosen = random.choices(segments, weights=weights, k=1)[0]
    mult   = next(m for s, m, _ in WHEEL_SEGMENTS if s == chosen)
    return chosen, mult

def wheel_art(pointer_index: int, highlight: str | None = None) -> str:
    lines = []
    for i, seg in enumerate(WHEEL_DISPLAY_ORDER):
        is_ptr = (i == pointer_index)
        is_win = (highlight is not None and seg == highlight)
        arrow  = "▶ " if is_ptr else "  "
        marker = " ◀◀" if is_win else ""
        lines.append(f"{arrow}{seg}{marker}")
    return "```\n" + "\n".join(lines) + "\n```"


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: BLACKJACK
# ══════════════════════════════════════════════════════════════════════════════

DECK = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"] * 4

def bj_value(hand: list[str]) -> int:
    total, aces = 0, 0
    for c in hand:
        if c in ("J","Q","K"):
            total += 10
        elif c == "A":
            total += 11
            aces  += 1
        else:
            total += int(c)
    while total > 21 and aces:
        total -= 10
        aces  -= 1
    return total

def bj_hand_str(hand: list[str]) -> str:
    return " ".join(hand)


class BlackjackView(discord.ui.View):
    def __init__(self, bet: float, settings: dict, player: list, dealer: list, deck: list, doubled: bool = False):
        super().__init__(timeout=60)
        self.bet      = bet
        self.settings = settings
        self.player   = player
        self.dealer   = dealer
        self.deck     = deck
        self.doubled  = doubled

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    def build_embed(self, title: str = "🃏 Blackjack") -> discord.Embed:
        return neutral_embed(
            title,
            f"**Your Hand:** {bj_hand_str(self.player)} = **{bj_value(self.player)}**\n"
            f"**Dealer Shows:** {self.dealer[0]} + 🂠\n\n"
            f"Bet: **{self.bet:,.2f}** chips"
        )

    async def finish(self, interaction: discord.Interaction):
        """Dealer draws, determine winner."""
        for item in self.children:
            item.disabled = True

        while bj_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())

        pv = bj_value(self.player)
        dv = bj_value(self.dealer)
        bust_dealer = dv > 21

        if pv > 21:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "Blackjack — Bust!",
                f"**Your Hand:** {bj_hand_str(self.player)} = {pv} (BUST)\n"
                f"**Dealer:** {bj_hand_str(self.dealer)} = {dv}\n\n"
                f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        elif bust_dealer or pv > dv:
            mult  = 2.5 if len(self.player) == 2 and pv == 21 else 2.0
            gross = self.bet * mult
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            label    = "Blackjack — Natural 21! 🎉" if mult == 2.5 else "Blackjack — You Win!"
            embed = win_embed(
                label,
                f"**Your Hand:** {bj_hand_str(self.player)} = {pv}\n"
                f"**Dealer:** {bj_hand_str(self.dealer)} = {'BUST' if bust_dealer else dv}\n\n"
                f"**Gross Win:** {gross:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        elif pv == dv:
            await stamp_cooldown(interaction.guild_id, interaction.user.id)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = neutral_embed(
                "Blackjack — Push",
                f"**Your Hand:** {bj_hand_str(self.player)} = {pv}\n"
                f"**Dealer:** {bj_hand_str(self.dealer)} = {dv}\n\n"
                "It's a tie — your bet has been returned.\n"
                f"**Balance:** {chips:,.2f} chips"
            )
        else:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "Blackjack — Dealer Wins",
                f"**Your Hand:** {bj_hand_str(self.player)} = {pv}\n"
                f"**Dealer:** {bj_hand_str(self.dealer)} = {dv}\n\n"
                f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="👊 Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.append(self.deck.pop())
        if bj_value(self.player) >= 21:
            await self.finish(interaction)
        else:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🛑 Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction)

    @discord.ui.button(label="✌️ Double Down", style=discord.ButtonStyle.success)
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.player) != 2:
            await interaction.response.send_message("Double Down is only available on your first two cards.", ephemeral=True)
            return
        chips = await get_chips(interaction.guild_id, interaction.user.id)
        if chips < self.bet * 2:
            await interaction.response.send_message("Not enough chips to double down.", ephemeral=True)
            return
        self.bet      *= 2
        self.doubled   = True
        self.player.append(self.deck.pop())
        await self.finish(interaction)


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: BACCARAT
# ══════════════════════════════════════════════════════════════════════════════

def bac_value(hand: list[str]) -> int:
    total = 0
    for c in hand:
        if c in ("J","Q","K","10"):
            pass
        elif c == "A":
            total += 1
        else:
            total += int(c)
    return total % 10

class BaccaratView(discord.ui.View):
    def __init__(self, bet: float, settings: dict):
        super().__init__(timeout=60)
        self.bet      = bet
        self.settings = settings
        self.resolved = False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def resolve(self, interaction: discord.Interaction, choice: str):
        if self.resolved:
            await interaction.response.defer()
            return
        self.resolved = True
        for item in self.children:
            item.disabled = True
        deck   = DECK.copy()
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        banker = [deck.pop(), deck.pop()]

        # Third card rules (simplified)
        if bac_value(player) <= 5:
            player.append(deck.pop())
        if bac_value(banker) <= 5:
            banker.append(deck.pop())

        pv, bv = bac_value(player), bac_value(banker)
        if pv > bv:
            winner = "player"
        elif bv > pv:
            winner = "banker"
        else:
            winner = "tie"

        if choice == winner:
            if choice == "tie":
                payout = self.bet * 8
            elif choice == "banker":
                payout = self.bet * 1.95
            else:
                payout = self.bet * 2
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, payout)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                f"Baccarat — {winner.title()} Wins! 🎉",
                f"**Player:** {bj_hand_str(player)} = {pv}\n"
                f"**Banker:** {bj_hand_str(banker)} = {bv}\n\n"
                f"**Gross Win:** {payout:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        else:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                f"Baccarat — {winner.title()} Wins",
                f"**Player:** {bj_hand_str(player)} = {pv}\n"
                f"**Banker:** {bj_hand_str(banker)} = {bv}\n\n"
                f"You called **{choice}**.\n"
                f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="👤 Player", style=discord.ButtonStyle.primary)
    async def player_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "player")

    @discord.ui.button(label="🏦 Banker", style=discord.ButtonStyle.secondary)
    async def banker_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "banker")

    @discord.ui.button(label="🤝 Tie", style=discord.ButtonStyle.success)
    async def tie_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "tie")


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: THREE CARD POKER
# ══════════════════════════════════════════════════════════════════════════════

def tcp_rank(hand: list[str]) -> tuple[int, list[int]]:
    """Returns (rank, tiebreakers). Higher rank = better hand."""
    vals  = sorted([CARD_VALUES[c] for c in hand], reverse=True)
    suits = [c[-1] if len(c) > 1 else "x" for c in hand]  # placeholder — suits not tracked
    # Simplified: no suits → no flushes/straight flushes, use value-based only
    is_straight = (vals[0] - vals[1] == 1 and vals[1] - vals[2] == 1)
    counts      = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    freq = sorted(counts.values(), reverse=True)
    if freq[0] == 3:
        return (4, vals)  # Three of a Kind
    elif is_straight:
        return (3, vals)  # Straight
    elif freq[0] == 2:
        return (2, vals)  # Pair
    return (1, vals)      # High Card

TCP_PAYOUTS = {4: 5.0, 3: 4.0, 2: 2.0, 1: 2.0}  # three-of-kind, straight, pair, win

class ThreeCardPokerView(discord.ui.View):
    def __init__(self, bet: float, settings: dict, player_hand: list, dealer_hand: list):
        super().__init__(timeout=60)
        self.bet         = bet
        self.settings    = settings
        self.player_hand = player_hand
        self.dealer_hand = dealer_hand

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def resolve(self, interaction: discord.Interaction, action: str):
        for item in self.children:
            item.disabled = True
        pr, pv = tcp_rank(self.player_hand)
        dr, dv = tcp_rank(self.dealer_hand)

        player_wins = pr > dr or (pr == dr and pv > dv)
        ph_str = bj_hand_str(self.player_hand)
        dh_str = bj_hand_str(self.dealer_hand)

        if action == "fold":
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "Three Card Poker — Folded",
                f"**Your Hand:** {ph_str}\n"
                f"**Dealer:** {dh_str}\n\n"
                f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        elif player_wins:
            mult   = TCP_PAYOUTS.get(pr, 2.0)
            payout = self.bet * mult
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, payout)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            ranks    = ["", "High Card", "Pair", "Straight", "Three of a Kind"]
            embed = win_embed(
                f"Three Card Poker — {ranks[pr]}!",
                f"**Your Hand:** {ph_str} ({ranks[pr]})\n"
                f"**Dealer:** {dh_str} ({ranks[dr]})\n\n"
                f"**Multiplier:** {mult}x\n"
                f"**Gross Win:** {payout:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        else:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            ranks = ["", "High Card", "Pair", "Straight", "Three of a Kind"]
            embed = lose_embed(
                "Three Card Poker — Dealer Wins",
                f"**Your Hand:** {ph_str} ({ranks[pr]})\n"
                f"**Dealer:** {dh_str} ({ranks[dr]})\n\n"
                f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="✅ Play", style=discord.ButtonStyle.success)
    async def play_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "play")

    @discord.ui.button(label="🚪 Fold", style=discord.ButtonStyle.danger)
    async def fold_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, "fold")


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: PICK A NUMBER
# ══════════════════════════════════════════════════════════════════════════════

class PickNumberView(discord.ui.View):
    def __init__(self, bet: float, settings: dict):
        super().__init__(timeout=60)
        self.bet      = bet
        self.settings = settings
        for i in range(1, 11):
            btn = discord.ui.Button(
                label=str(i),
                style=discord.ButtonStyle.secondary,
                custom_id=f"pick:{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    def _make_callback(self, number: int):
        async def callback(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            house = random.randint(1, 10)
            if number == house:
                gross    = self.bet * 8
                net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
                chips    = await get_chips(interaction.guild_id, interaction.user.id)
                embed = win_embed(
                    "Pick a Number — Exact Match! 🎯",
                    f"You picked **{number}**, house drew **{house}**!\n\n"
                    f"**Gross Win:** {gross:,.2f} chips\n"
                    f"**Tax:** {tax:,.2f} chips\n"
                    f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
                )
            else:
                await apply_loss(interaction, self.bet)
                chips = await get_chips(interaction.guild_id, interaction.user.id)
                embed = lose_embed(
                    "Pick a Number — No Match",
                    f"You picked **{number}**, house drew **{house}**.\n\n"
                    f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
                )
            await interaction.response.edit_message(embed=embed, view=self)
        return callback


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: HORSE RACING
# ══════════════════════════════════════════════════════════════════════════════

HORSES = [
    ("🐴 Faithful Stride",  1.5, 30),
    ("🐎 Golden Covenant",  2.5, 22),
    ("🏇 Righteous Run",    3.5, 18),
    ("🐴 Sovereign Grace",  5.0, 14),
    ("🐎 Blessed Gambit",   6.5,  9),
    ("🏇 Providence",       8.0,  7),
]

TRACK_LENGTH = 20  # characters wide

def build_race_track(positions: dict[str, float], horse_names: list[str], chosen: str, winner: str | None = None) -> str:
    """Build an ASCII race track. positions maps name→progress (0-TRACK_LENGTH)."""
    lines = ["```"]
    lines.append("🏁 FINISH" + " " * (TRACK_LENGTH - 3) + "🚩 START")
    lines.append("─" * (TRACK_LENGTH + 12))
    for name in horse_names:
        pos   = int(positions.get(name, 0))  # cast float→int for string multiplication
        label = name.split(" ", 1)[1] if " " in name else name  # strip emoji
        emoji = name.split(" ")[0]
        # left = finish side, right = start side; horse moves left
        track = "." * (TRACK_LENGTH - pos) + emoji + "." * pos
        flag  = ""
        if winner and name == winner:
            flag = " 🏆"
        elif winner and name == chosen and name != winner:
            flag = " ✗"
        lines.append(f"|{track}| {label[:14]:<14}{flag}")
    lines.append("─" * (TRACK_LENGTH + 12))
    lines.append("```")
    return "\n".join(lines)

class HorseRacingView(discord.ui.View):
    def __init__(self, bet: float, settings: dict):
        super().__init__(timeout=90)
        self.bet      = bet
        self.settings = settings
        self.racing   = False
        for i, (name, odds, _) in enumerate(HORSES):
            btn = discord.ui.Button(
                label=f"{name} ({odds}x)",
                style=discord.ButtonStyle.primary,
                row=i // 3  # 3 per row → row 0 for horses 0-2, row 1 for horses 3-5
            )
            btn.callback = self._make_callback(name, odds)
            self.add_item(btn)

    async def on_timeout(self):
        if not self.racing:
            for item in self.children:
                item.disabled = True

    def _make_callback(self, horse_name: str, odds: float):
        async def callback(interaction: discord.Interaction):
            if self.racing:
                await interaction.response.defer()
                return
            self.racing = True
            for item in self.children:
                item.disabled = True

            names_list = [n for n, _, _ in HORSES]
            _, _, weights_list = zip(*HORSES)
            winner = random.choices(names_list, weights=list(weights_list), k=1)[0]

            # Initial response — gates open
            positions = {n: 0 for n in names_list}
            track_str = build_race_track(positions, names_list, horse_name)
            start_embed = neutral_embed(
                "🏇 Horse Racing — They're Off!",
                f"**Your pick:** {horse_name}\n\n{track_str}\n*🎺 The gates fly open!*"
            )
            await interaction.response.edit_message(embed=start_embed, view=self)
            message = await interaction.original_response()

            # Simulate the race — winner guaranteed to finish first
            # Each horse has a "speed" jitter; winner gets a slight edge
            speeds = {}
            for n in names_list:
                base = random.uniform(0.8, 1.4)
                if n == winner:
                    base = random.uniform(1.2, 1.6)  # winner runs hotter
                speeds[n] = base

            # Run frames until winner crosses finish
            frame_count = 0
            commentary = [
                "🎺 *They burst from the gates!*",
                "💨 *The field spreads out!*",
                "📢 *Thundering hooves fill the air!*",
                "🎙️ *A horse is making a move!*",
                "📣 *The crowd roars!*",
                "🏁 *The finish line is in sight!*",
            ]
            while True:
                await asyncio.sleep(1.0)
                # Advance all horses
                for n in names_list:
                    step = random.uniform(0.5, speeds[n] * 2)
                    positions[n] = min(TRACK_LENGTH, positions[n] + step)
                # Check if winner has crossed
                if positions[winner] >= TRACK_LENGTH:
                    # Snap winner to finish, ensure others haven't crossed
                    positions[winner] = TRACK_LENGTH
                    for n in names_list:
                        if n != winner and positions[n] >= TRACK_LENGTH:
                            positions[n] = TRACK_LENGTH - 1
                    break

                comment = commentary[min(frame_count, len(commentary) - 1)]
                frame_count += 1
                track_str = build_race_track(positions, names_list, horse_name)
                live_embed = neutral_embed(
                    "🏇 Horse Racing — In Progress!",
                    f"**Your pick:** {horse_name}\n\n{track_str}\n{comment}"
                )
                try:
                    await message.edit(embed=live_embed, view=self)
                except Exception:
                    break

            # Final frame — show winner
            await asyncio.sleep(0.6)
            won = (horse_name == winner)
            track_final = build_race_track(positions, names_list, horse_name, winner=winner)

            if won:
                gross    = self.bet * odds
                net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
                chips    = await get_chips(interaction.guild_id, interaction.user.id)
                result_embed = win_embed(
                    "🏆 Horse Racing — Your Horse Won!",
                    f"{track_final}\n"
                    f"**Winner:** {winner}\n"
                    f"**Your Pick:** {horse_name} ✅  ({odds}x)\n\n"
                    f"**Gross Win:** {gross:,.2f} chips\n"
                    f"**Tax:** {tax:,.2f} chips\n"
                    f"**Net Win:** +{net:,.2f} chips\n"
                    f"**Balance:** {chips:,.2f} chips"
                )
            else:
                await apply_loss(interaction, self.bet)
                chips = await get_chips(interaction.guild_id, interaction.user.id)
                result_embed = lose_embed(
                    "🐎 Horse Racing — Better Luck Next Time",
                    f"{track_final}\n"
                    f"**Winner:** {winner}\n"
                    f"**Your Pick:** {horse_name} ❌\n\n"
                    f"**Lost:** {self.bet:,.2f} chips\n"
                    f"**Balance:** {chips:,.2f} chips"
                )
            try:
                await message.edit(embed=result_embed, view=self)
            except Exception:
                pass
        return callback


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: SLOTS
# ══════════════════════════════════════════════════════════════════════════════

SLOT_SYMBOLS = ["🍋","🍊","🍇","🍒","⭐","💎","7️⃣"]
SLOT_WEIGHTS  = [   25,   20,   18,  15,   10,    7,    5]
SLOT_PAYOUTS  = {
    "🍋": 1.5, "🍊": 1.8, "🍇": 2.0, "🍒": 2.5,
    "⭐": 3.0, "💎": 5.0, "7️⃣": 50.0
}

def spin_slots():
    reels = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    if reels[0] == reels[1] == reels[2]:
        return reels, SLOT_PAYOUTS[reels[0]]
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        return reels, 1.5
    return reels, 0.0

def slot_display(r1: str, r2: str, r3: str, spinning: list[bool] | None = None) -> str:
    """
    Render a slot machine display that works with double-width emoji.
    Uses plain pipe separators instead of box-drawing chars around emoji cells,
    which avoids alignment breakage from emoji being 2 columns wide.
    """
    if spinning is None:
        spinning = [False, False, False]
    rand_syms = random.choices(SLOT_SYMBOLS, k=3)
    d1 = rand_syms[0] if spinning[0] else r1
    d2 = rand_syms[1] if spinning[1] else r2
    d3 = rand_syms[2] if spinning[2] else r3
    return f"🎰  {d1}  |  {d2}  |  {d3}  🎰"


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: MINESWEEPER
# ══════════════════════════════════════════════════════════════════════════════

MINE_GRID  = 16   # 4×4
MINE_COUNT = 4
MINE_MULTI = 1.25  # per safe reveal

class MinesweeperView(discord.ui.View):
    def __init__(self, bet: float, settings: dict, mines: set, revealed: set, multiplier: float = 1.0):
        super().__init__(timeout=60)
        self.bet        = bet
        self.settings   = settings
        self.mines      = mines
        self.revealed   = revealed
        self.multiplier = multiplier
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        for i in range(MINE_GRID):
            if i in self.revealed:
                btn = discord.ui.Button(label="✅", style=discord.ButtonStyle.success, disabled=True, row=i // 4)
            elif i in self.mines and len(self.revealed) == MINE_GRID - MINE_COUNT:
                btn = discord.ui.Button(label="💣", style=discord.ButtonStyle.danger, disabled=True, row=i // 4)
            else:
                btn = discord.ui.Button(label="🟦", style=discord.ButtonStyle.secondary, custom_id=f"mine:{i}", row=i // 4)
                btn.callback = self._make_tile_callback(i)
            self.add_item(btn)
        cash_btn = discord.ui.Button(label=f"💰 Cash Out ({self.multiplier:.2f}x)", style=discord.ButtonStyle.primary, row=4)
        cash_btn.callback = self._cashout_callback
        self.add_item(cash_btn)

    def _make_tile_callback(self, tile: int):
        async def callback(interaction: discord.Interaction):
            if tile in self.mines:
                # Hit a mine
                for item in self.children:
                    item.disabled = True
                await apply_loss(interaction, self.bet)
                chips = await get_chips(interaction.guild_id, interaction.user.id)
                # Reveal all mines
                mine_str = " ".join(f"Tile {m+1}" for m in sorted(self.mines))
                embed = lose_embed(
                    "💣 Minesweeper — BOOM!",
                    f"You hit a mine on tile **{tile+1}**!\n\n"
                    f"**Mines were at:** {mine_str}\n"
                    f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
                )
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                self.revealed.add(tile)
                self.multiplier = round(self.multiplier * MINE_MULTI, 4)
                safe_left = (MINE_GRID - MINE_COUNT) - len(self.revealed)
                if safe_left == 0:
                    # Auto cashout — all safe tiles revealed
                    await self._cashout_callback(interaction)
                    return
                gross = round(self.bet * self.multiplier, 2)
                embed = neutral_embed(
                    f"💎 Minesweeper — Safe! ({len(self.revealed)} revealed)",
                    f"Tile **{tile+1}** was safe!\n\n"
                    f"**Current Multiplier:** {self.multiplier:.2f}x\n"
                    f"**Cash Out Value:** ~{gross:,.2f} chips (before tax)\n"
                    f"**Safe Tiles Remaining:** {safe_left}"
                )
                new_view = MinesweeperView(self.bet, self.settings, self.mines, self.revealed, self.multiplier)
                await interaction.response.edit_message(embed=embed, view=new_view)
        return callback

    async def _cashout_callback(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        gross    = round(self.bet * self.multiplier, 2)
        net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
        chips    = await get_chips(interaction.guild_id, interaction.user.id)
        embed = win_embed(
            "💎 Minesweeper — Cashed Out!",
            f"You revealed **{len(self.revealed)}** safe tile(s) and walked away!\n\n"
            f"**Multiplier:** {self.multiplier:.2f}x\n"
            f"**Gross Win:** {gross:,.2f} chips\n"
            f"**Tax:** {tax:,.2f} chips\n"
            f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: WAR
# ══════════════════════════════════════════════════════════════════════════════

class WarView(discord.ui.View):
    def __init__(self, bet: float, settings: dict, player_card: str, dealer_card: str):
        super().__init__(timeout=60)
        self.bet         = bet
        self.settings    = settings
        self.player_card = player_card
        self.dealer_card = dealer_card

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def resolve(self, interaction: discord.Interaction, go_to_war: bool):
        for item in self.children:
            item.disabled = True
        pv = CARD_VALUES[self.player_card]
        dv = CARD_VALUES[self.dealer_card]

        if pv > dv:
            gross    = self.bet * 2
            net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                "War — You Win!",
                f"**Your Card:** {self.player_card}  vs  **Dealer:** {self.dealer_card}\n\n"
                f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        elif pv < dv:
            await apply_loss(interaction, self.bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "War — Dealer Wins",
                f"**Your Card:** {self.player_card}  vs  **Dealer:** {self.dealer_card}\n\n"
                f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        else:
            # Tie — go to war
            if not go_to_war:
                await apply_loss(interaction, self.bet)
                chips = await get_chips(interaction.guild_id, interaction.user.id)
                embed = lose_embed(
                    "War — Surrender",
                    f"Both drew **{self.player_card}**. You surrendered.\n\n"
                    f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
                )
            else:
                # Each draws again
                deck  = DECK.copy()
                random.shuffle(deck)
                pc2, dc2 = deck.pop(), deck.pop()
                pv2, dv2 = CARD_VALUES[pc2], CARD_VALUES[dc2]
                if pv2 >= dv2:
                    gross    = self.bet * 4
                    net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
                    chips    = await get_chips(interaction.guild_id, interaction.user.id)
                    embed = win_embed(
                        "War — Victory in Battle! ⚔️",
                        f"**War Cards:** {pc2} vs {dc2}\n\n"
                        f"**Gross Win:** {gross:,.2f} chips\n"
                        f"**Tax:** {tax:,.2f} chips\n"
                        f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
                    )
                else:
                    await apply_loss(interaction, self.bet)
                    chips = await get_chips(interaction.guild_id, interaction.user.id)
                    embed = lose_embed(
                        "War — Defeated in Battle ⚔️",
                        f"**War Cards:** {pc2} vs {dc2}\n\n"
                        f"**Lost:** {self.bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
                    )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⚔️ Go to War", style=discord.ButtonStyle.danger)
    async def war(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, True)

    @discord.ui.button(label="🏳️ Surrender", style=discord.ButtonStyle.secondary)
    async def surrender(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.resolve(interaction, False)


# ══════════════════════════════════════════════════════════════════════════════
#  GAME: CRASH
# ══════════════════════════════════════════════════════════════════════════════

def generate_crash_point() -> float:
    """House edge ~4%. Returns crash multiplier (can be <1x — instant bust ~4% of the time)."""
    r = random.random()
    if r < 0.04:
        return 1.0  # instant crash
    return round(0.99 / (1 - r), 2)

class CrashView(discord.ui.View):
    def __init__(self, bet: float, settings: dict, crash_point: float, current: float = 1.0):
        super().__init__(timeout=60)
        self.bet         = bet
        self.settings    = settings
        self.crash_point = crash_point
        self.current     = current
        self.cashed_out  = False
        self.crashed     = False

    async def on_timeout(self):
        if not self.cashed_out and not self.crashed:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(label="💰 Cash Out", style=discord.ButtonStyle.success, custom_id="crash:cashout")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cashed_out or self.crashed:
            await interaction.response.send_message("Already resolved.", ephemeral=True)
            return
        self.cashed_out = True
        for item in self.children:
            item.disabled = True
        gross    = round(self.bet * self.current, 2)
        net, tax = await apply_tax_and_pay(interaction, self.settings, self.bet, gross)
        chips    = await get_chips(interaction.guild_id, interaction.user.id)
        embed = win_embed(
            f"💥 Crash — Cashed Out at {self.current:.2f}x!",
            f"**Multiplier:** {self.current:.2f}x (crashed at {self.crash_point:.2f}x)\n\n"
            f"**Gross Win:** {gross:,.2f} chips\n"
            f"**Tax:** {tax:,.2f} chips\n"
            f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
        )
        await interaction.response.edit_message(embed=embed, view=self)


# ══════════════════════════════════════════════════════════════════════════════
#  CASINO COG
# ══════════════════════════════════════════════════════════════════════════════

class Casino(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Admin setup commands ──────────────────────────────────────────────────

    @app_commands.command(name="setup_chip_exchange", description="Set the chip exchange channel.")
    async def setup_chip_exchange(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        await set_casino_field(interaction.guild_id, "chip_exchange_channel_id", channel.id)
        # Post the persistent exchange menu
        embed = styled_embed(
            "🪙 G.R.E.T.A. Chip Exchange",
            "Welcome to the G.R.E.T.A. Chip Exchange.\n\n"
            "Purchase chips to play on the Casino Floor, or cash out your winnings back to your wallet.\n\n"
            "**Buy Rate:** 1:1 (wallet → chips)\n"
            "**Cash-Out Fee:** G.R.E.T.A. retains 5% of all chip redemptions\n\n"
            "*God. Reliant. Ethical. Trust. & Assurance.*",
            color=ACCENT
        )
        await channel.send(embed=embed, view=ChipExchangeView())
        await interaction.response.send_message(
            embed=styled_embed("Chip Exchange Set", f"Chip exchange is now active in {channel.mention}.", color=SUCCESS),
            ephemeral=True
        )

    @app_commands.command(name="setup_casino_floor", description="Set the casino floor channel.")
    async def setup_casino_floor(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        await set_casino_field(interaction.guild_id, "casino_floor_channel_id", channel.id)
        await interaction.response.send_message(
            embed=styled_embed("Casino Floor Set", f"The casino floor is now {channel.mention}.", color=SUCCESS),
            ephemeral=True
        )

    @app_commands.command(name="casino_toggle", description="Open or close the casino.")
    async def casino_toggle(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        settings = await get_casino_settings(interaction.guild_id)
        new_state = not settings.get("casino_enabled", True)
        await set_casino_field(interaction.guild_id, "casino_enabled", new_state)
        if new_state:
            embed = styled_embed("🎰 Casino Opened", "The G.R.E.T.A. Casino Floor is now open for business.", color=SUCCESS)
        else:
            embed = styled_embed("🚫 Casino Closed", "The G.R.E.T.A. Casino Floor is closed by order of Universalis authorities.", color=DANGER)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="casino_set_max_bet", description="Set the maximum bet per game (0 = no limit).")
    async def casino_set_max_bet(self, interaction: discord.Interaction, amount: float):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        val = None if amount <= 0 else amount
        await set_casino_field(interaction.guild_id, "casino_max_bet", val)
        label = f"{amount:,.2f} chips" if val else "No limit"
        await interaction.response.send_message(
            embed=styled_embed("Max Bet Updated", f"Table limit set to **{label}**.", color=SUCCESS),
            ephemeral=True
        )

    @app_commands.command(name="casino_set_tax", description="Set the casino winnings tax rate (default 25%).")
    async def casino_set_tax(self, interaction: discord.Interaction, percent: float):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        if not (0 <= percent <= 100):
            await interaction.response.send_message("Tax rate must be between 0 and 100.", ephemeral=True)
            return
        await set_casino_field(interaction.guild_id, "casino_tax_rate", percent)
        await interaction.response.send_message(
            embed=styled_embed("Tax Rate Updated", f"Casino winnings tax set to **{percent}%**.", color=SUCCESS),
            ephemeral=True
        )

    @app_commands.command(name="casino_set_cooldown", description="Set the cooldown between games in seconds (default 5).")
    async def casino_set_cooldown(self, interaction: discord.Interaction, seconds: int):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        if seconds < 0:
            await interaction.response.send_message("Cooldown must be 0 or more seconds.", ephemeral=True)
            return
        await set_casino_field(interaction.guild_id, "casino_cooldown", seconds)
        await interaction.response.send_message(
            embed=styled_embed("Cooldown Updated", f"Game cooldown set to **{seconds}s**.", color=SUCCESS),
            ephemeral=True
        )

    @app_commands.command(name="casino_house_pot", description="View or withdraw the G.R.E.T.A. house pot.")
    async def casino_house_pot_cmd(self, interaction: discord.Interaction):
        if not await admin_check(interaction):
            return
        await ensure_guild(interaction.guild_id)
        pot      = await get_house_pot(interaction.guild_id)
        settings = await get_casino_settings(interaction.guild_id)
        sym      = settings.get("currency_symbol", "C")
        await interaction.response.send_message(
            embed=styled_embed(
                "🏦 G.R.E.T.A. House Pot",
                f"The house pot currently holds **{pot:,.2f} chips**.\n\n"
                f"This comprises winnings tax and cashout fees collected by G.R.E.T.A.",
                color=ACCENT
            ),
            view=HousePotWithdrawView(pot, sym),
            ephemeral=True
        )

    # ── Game commands ─────────────────────────────────────────────────────────

    @app_commands.command(name="coinflip", description="Flip a coin — pick Heads or Tails.")
    async def coinflip(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        embed = neutral_embed(
            "🪙 Coinflip",
            f"{interaction.user.mention} placed a bet of **{bet:,.2f}** chips.\n\nHeads or Tails?"
        )
        await interaction.response.send_message(embed=embed, view=CoinflipView(bet, settings))

    @app_commands.command(name="dice", description="Roll a die — pick Over 3 or Under 4.")
    async def dice(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        embed = neutral_embed(
            "🎲 Dice Roll",
            f"{interaction.user.mention} placed a bet of **{bet:,.2f}** chips.\n\n"
            "Will the die land **Over 3** (4–6) or **Under 4** (1–3)?\n"
            "Pays **1.8x** on win."
        )
        await interaction.response.send_message(embed=embed, view=DiceView(bet, settings))

    @app_commands.command(name="hilo", description="Guess if the next card is higher or lower. Chain up to 5x for 1.9x each.")
    async def hilo(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        card  = random.choice(CARD_NAMES)
        embed = neutral_embed(
            "🃏 Hi-Lo",
            f"{interaction.user.mention} placed a bet of **{bet:,.2f}** chips.\n\n"
            f"**Current Card:** {card}\n\n"
            "Is the next card Higher or Lower? Each correct guess multiplies your winnings by **1.9x** (up to 5 chains)."
        )
        await interaction.response.send_message(embed=embed, view=HiLoView(bet, settings, card))

    @app_commands.command(name="keno", description="Pick 1–10 numbers from 1–40. More matches = bigger payout.")
    async def keno(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        await interaction.response.send_modal(KenoModal(bet, settings))

    @app_commands.command(name="wheel", description="Spin the Wheel of Fortune.")
    async def wheel(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        segment, mult = spin_wheel()
        final_idx = WHEEL_DISPLAY_ORDER.index(segment)

        # Show spinning animation
        spin_embed = neutral_embed(
            "🌀 Wheel of Fortune — Spinning...",
            f"{interaction.user.mention} bet **{bet:,.2f}** chips\n\n"
            f"{wheel_art(0)}\n*🌀 The wheel is spinning...*"
        )
        await interaction.response.send_message(embed=spin_embed)
        message = await interaction.original_response()

        # Animate the pointer sweeping around, slowing down near the result
        n = len(WHEEL_DISPLAY_ORDER)
        # Do 2 full rotations then land on final_idx
        spin_sequence = list(range(n)) * 2 + list(range(final_idx + 1))
        delays = [0.15] * (len(spin_sequence) - 6) + [0.25, 0.35, 0.45, 0.55, 0.65, 0.75]
        delays = delays[-len(spin_sequence):]  # align
        # pad if needed
        while len(delays) < len(spin_sequence):
            delays.insert(0, 0.15)

        for i, (ptr, delay) in enumerate(zip(spin_sequence, delays)):
            await asyncio.sleep(delay)
            label = "🌀 Slowing down..." if i > len(spin_sequence) - 5 else "🌀 Spinning..."
            e = neutral_embed(
                "🌀 Wheel of Fortune — Spinning...",
                f"{interaction.user.mention} bet **{bet:,.2f}** chips\n\n"
                f"{wheel_art(ptr)}\n*{label}*"
            )
            try:
                await message.edit(embed=e)
            except Exception:
                break

        await asyncio.sleep(0.5)

        if mult == 0.0:
            await apply_loss(interaction, bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "🌀 Wheel of Fortune — BUST! 💀",
                f"{interaction.user.mention}\n\n"
                f"{wheel_art(final_idx, highlight=segment)}\n"
                f"The wheel landed on **{segment}**!\n\n"
                f"**Lost:** {bet:,.2f} chips\n"
                f"**Balance:** {chips:,.2f} chips"
            )
        else:
            gross    = bet * mult
            net, tax = await apply_tax_and_pay(interaction, settings, bet, gross)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            embed = win_embed(
                f"🌀 Wheel of Fortune — {segment}! 🎉",
                f"{interaction.user.mention}\n\n"
                f"{wheel_art(final_idx, highlight=segment)}\n"
                f"The wheel landed on **{segment}**!\n\n"
                f"**Multiplier:** {mult}x\n"
                f"**Gross Win:** {gross:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** +{net:,.2f} chips\n"
                f"**Balance:** {chips:,.2f} chips"
            )
        try:
            await message.edit(embed=embed)
        except Exception:
            pass

    @app_commands.command(name="blackjack", description="Classic Blackjack — beat the dealer.")
    async def blackjack(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        deck = DECK.copy()
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]
        view   = BlackjackView(bet, settings, player, dealer, deck)
        embed  = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="baccarat", description="Baccarat — bet on Player, Banker, or Tie.")
    async def baccarat(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        embed = neutral_embed(
            "🎴 Baccarat",
            f"{interaction.user.mention} placed **{bet:,.2f}** chips.\n\n"
            "Bet on **Player** (2x), **Banker** (1.95x), or **Tie** (8x)."
        )
        await interaction.response.send_message(embed=embed, view=BaccaratView(bet, settings))

    @app_commands.command(name="threecardpoker", description="Three Card Poker — beat the dealer's hand.")
    async def threecardpoker(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        deck = DECK.copy()
        random.shuffle(deck)
        player_hand = [deck.pop(), deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop(), deck.pop()]
        embed = neutral_embed(
            "🃏 Three Card Poker",
            f"{interaction.user.mention} placed **{bet:,.2f}** chips.\n\n"
            f"**Your Hand:** {bj_hand_str(player_hand)}\n"
            f"**Dealer:** 🂠 🂠 🂠\n\n"
            "Play or Fold?"
        )
        await interaction.response.send_message(
            embed=embed, view=ThreeCardPokerView(bet, settings, player_hand, dealer_hand)
        )

    @app_commands.command(name="pickanumber", description="Pick a number 1–10. Exact match pays 8x.")
    async def pickanumber(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        embed = neutral_embed(
            "🎯 Pick a Number",
            f"{interaction.user.mention} placed **{bet:,.2f}** chips.\n\n"
            "Pick a number from **1 to 10**. Exact match pays **8x**!"
        )
        await interaction.response.send_message(embed=embed, view=PickNumberView(bet, settings))

    @app_commands.command(name="horserace", description="Pick a horse and watch the race!")
    async def horserace(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        horses_str = "\n".join(f"**{n}** — {o}x" for n, o, _ in HORSES)
        embed = neutral_embed(
            "🐎 Horse Racing",
            f"{interaction.user.mention} placed **{bet:,.2f}** chips.\n\n"
            f"**The Field:**\n{horses_str}\n\n"
            "Pick your horse!"
        )
        await interaction.response.send_message(embed=embed, view=HorseRacingView(bet, settings))

    @app_commands.command(name="slots", description="Pull the slots — match symbols to win.")
    async def slots(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return

        reels, mult = spin_slots()

        # ── Spin animation ────────────────────────────────────────────────────
        spin_embed = neutral_embed(
            "🎰 Slots — Spinning...",
            f"{interaction.user.mention} bet **{bet:,.2f}** chips\n\n"
            f"{slot_display('❓', '❓', '❓', [True, True, True])}\n"
            "*🎰 Pulling the lever...*"
        )
        await interaction.response.send_message(embed=spin_embed)
        message = await interaction.original_response()

        # Fast spin frames — all reels blurring
        for _ in range(3):
            await asyncio.sleep(0.25)
            e = neutral_embed(
                "🎰 Slots — Spinning...",
                f"{interaction.user.mention} bet **{bet:,.2f}** chips\n\n"
                f"{slot_display('❓', '❓', '❓', [True, True, True])}\n"
                "*🌀 Spinning...*"
            )
            try:
                await message.edit(embed=e)
            except Exception:
                break

        # Reel-stop animation: left reel stops, then middle, then right
        spin_stages = [
            ([False, True,  True],  reels[0], "❓",      "❓",      f"*⏸ {reels[0]} — first reel locked!*"),
            ([False, False, True],  reels[0], reels[1], "❓",      f"*⏸ {reels[1]} — second reel locked!*"),
        ]
        for spinning_flags, r1, r2, r3, label in spin_stages:
            await asyncio.sleep(0.4)
            e = neutral_embed(
                "🎰 Slots — Spinning...",
                f"{interaction.user.mention} bet **{bet:,.2f}** chips\n\n"
                f"{slot_display(r1, r2, r3, spinning_flags)}\n{label}"
            )
            try:
                await message.edit(embed=e)
            except Exception:
                break

        await asyncio.sleep(0.45)

        # ── Final result ──────────────────────────────────────────────────────
        reel_str = " | ".join(reels)
        if mult == 0.0:
            await apply_loss(interaction, bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "🎰 Slots — No Match",
                f"{interaction.user.mention}\n\n"
                f"{slot_display(reels[0], reels[1], reels[2])}\n\n"
                f"**Lost:** {bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        else:
            gross    = bet * mult
            net, tax = await apply_tax_and_pay(interaction, settings, bet, gross)
            chips    = await get_chips(interaction.guild_id, interaction.user.id)
            label = "🎰 Slots — JACKPOT! 🎉" if mult == 50.0 else f"🎰 Slots — {mult}x!"
            embed = win_embed(
                label,
                f"{interaction.user.mention}\n\n"
                f"{slot_display(reels[0], reels[1], reels[2])}\n\n"
                f"**Multiplier:** {mult}x\n"
                f"**Gross Win:** {gross:,.2f} chips\n"
                f"**Tax:** {tax:,.2f} chips\n"
                f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
        try:
            await message.edit(embed=embed)
        except Exception:
            pass

    @app_commands.command(name="minesweeper", description="Reveal safe tiles and cash out before hitting a mine!")
    async def minesweeper(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        mines = set(random.sample(range(MINE_GRID), MINE_COUNT))
        embed = neutral_embed(
            "💣 Minesweeper",
            f"{interaction.user.mention} placed **{bet:,.2f}** chips.\n\n"
            f"**Grid:** 4×4  |  **Mines:** {MINE_COUNT}\n"
            f"Each safe tile multiplies your bet by **{MINE_MULTI}x**. Cash out anytime!"
        )
        await interaction.response.send_message(embed=embed, view=MinesweeperView(bet, settings, mines, set()))

    @app_commands.command(name="war", description="Draw a card — highest card wins. Tie goes to War!")
    async def war(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        deck = DECK.copy()
        random.shuffle(deck)
        pc, dc = deck.pop(), deck.pop()
        pv, dv = CARD_VALUES[pc], CARD_VALUES[dc]
        if pv != dv:
            # Immediate resolution — no tie
            won = pv > dv
            if won:
                gross    = bet * 2
                net, tax = await apply_tax_and_pay(interaction, settings, bet, gross)
                chips    = await get_chips(interaction.guild_id, interaction.user.id)
                embed = win_embed(
                    "🃏 War — You Win!",
                    f"{interaction.user.mention}\n\n"
                    f"**Your Card:** {pc}  vs  **Dealer:** {dc}\n\n"
                    f"**Net Win:** {net:,.2f} chips | **Balance:** {chips:,.2f} chips"
                )
            else:
                await apply_loss(interaction, bet)
                chips = await get_chips(interaction.guild_id, interaction.user.id)
                embed = lose_embed(
                    "🃏 War — Dealer Wins",
                    f"{interaction.user.mention}\n\n"
                    f"**Your Card:** {pc}  vs  **Dealer:** {dc}\n\n"
                    f"**Lost:** {bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
                )
            await interaction.response.send_message(embed=embed)
        else:
            embed = neutral_embed(
                "🃏 War — It's a Tie! ⚔️",
                f"{interaction.user.mention}\n\n"
                f"Both drew **{pc}**! You may go to **War** (double or bust) or **Surrender**."
            )
            await interaction.response.send_message(embed=embed, view=WarView(bet, settings, pc, dc))

    @app_commands.command(name="crash", description="Ride the multiplier — cash out before it crashes!")
    async def crash(self, interaction: discord.Interaction, bet: float):
        settings = await casino_guard(interaction, bet)
        if not settings:
            return
        crash_point = generate_crash_point()

        if crash_point <= 1.0:
            await apply_loss(interaction, bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            embed = lose_embed(
                "💥 Crash — Instant Bust!",
                f"{interaction.user.mention} — The market crashed immediately!\n\n"
                f"**Crash Point:** {crash_point:.2f}x\n"
                f"**Lost:** {bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
            await interaction.response.send_message(embed=embed)
            return

        # Post the live embed and start the climb
        view  = CrashView(bet, settings, crash_point, current=1.0)
        embed = neutral_embed(
            "💥 Crash — In Progress",
            f"{interaction.user.mention} bet **{bet:,.2f}** chips.\n\n"
            f"**Multiplier:** 1.00x ⬆️\n\n"
            "Cash out before the crash!"
        )
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()

        # Climb loop
        current = 1.0
        while current < crash_point and not view.cashed_out:
            await asyncio.sleep(1.5)
            current = round(current * random.uniform(1.05, 1.20), 2)
            current = min(current, crash_point)
            view.current = current
            if view.cashed_out:
                break
            live_embed = neutral_embed(
                "💥 Crash — In Progress",
                f"{interaction.user.mention} bet **{bet:,.2f}** chips.\n\n"
                f"**Multiplier:** {current:.2f}x ⬆️\n\n"
                "Cash out before the crash!"
            )
            try:
                await message.edit(embed=live_embed, view=view)
            except Exception:
                break

        if not view.cashed_out:
            view.crashed = True
            for item in view.children:
                item.disabled = True
            await apply_loss(interaction, bet)
            chips = await get_chips(interaction.guild_id, interaction.user.id)
            crash_embed = lose_embed(
                f"💥 Crash — CRASHED at {crash_point:.2f}x!",
                f"{interaction.user.mention} didn't cash out in time!\n\n"
                f"**Crash Point:** {crash_point:.2f}x\n"
                f"**Lost:** {bet:,.2f} chips | **Balance:** {chips:,.2f} chips"
            )
            try:
                await message.edit(embed=crash_embed, view=view)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  HOUSE POT WITHDRAW VIEW
# ══════════════════════════════════════════════════════════════════════════════

class HousePotWithdrawView(discord.ui.View):
    def __init__(self, pot: float, sym: str):
        super().__init__(timeout=60)
        self.pot = pot
        self.sym = sym

    @discord.ui.button(label="💰 Withdraw to Treasury", style=discord.ButtonStyle.success)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await admin_check(interaction):
            return
        from db.queries.casino import drain_house_pot
        from db.queries.wallets import add_balance
        drained = self.pot
        await drain_house_pot(interaction.guild_id)
        button.disabled = True
        await interaction.response.edit_message(
            embed=styled_embed(
                "House Pot Withdrawn",
                f"**{drained:,.2f} chips** worth of house earnings have been noted.\n"
                f"*(Manual transfer to treasury wallet not yet automated — record this amount.)*",
                color=SUCCESS
            ),
            view=self
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SETUP
# ══════════════════════════════════════════════════════════════════════════════

async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))

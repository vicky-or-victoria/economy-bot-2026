from __future__ import annotations

from db.connection import get_pool


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _ensure_wallet(guild_id: int, user_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """INSERT INTO wallets (guild_id, user_id)
           VALUES ($1, $2)
           ON CONFLICT DO NOTHING""",
        guild_id, user_id,
    )


async def _ensure_guild(guild_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
        guild_id,
    )


# ── Chip balance ──────────────────────────────────────────────────────────────

async def get_chips(guild_id: int, user_id: int) -> float:
    pool = get_pool()
    await _ensure_wallet(guild_id, user_id)
    row = await pool.fetchrow(
        "SELECT chips FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id,
    )
    return float(row["chips"]) if row else 0.0


async def add_chips(guild_id: int, user_id: int, amount: float) -> float:
    pool = get_pool()
    await _ensure_wallet(guild_id, user_id)
    row = await pool.fetchrow(
        """UPDATE wallets
           SET chips = chips + $1
           WHERE guild_id = $2 AND user_id = $3
           RETURNING chips""",
        amount, guild_id, user_id,
    )
    return float(row["chips"]) if row else 0.0


async def set_chips(guild_id: int, user_id: int, amount: float) -> None:
    pool = get_pool()
    await _ensure_wallet(guild_id, user_id)
    await pool.execute(
        "UPDATE wallets SET chips = $1 WHERE guild_id = $2 AND user_id = $3",
        amount, guild_id, user_id,
    )


# ── Cash balance ──────────────────────────────────────────────────────────────

async def get_cash(guild_id: int, user_id: int) -> float:
    pool = get_pool()
    await _ensure_wallet(guild_id, user_id)
    row = await pool.fetchrow(
        "SELECT cash_balance FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id,
    )
    return float(row["cash_balance"]) if row else 0.0


async def transfer_cash_to_chips(guild_id: int, user_id: int, amount: float) -> tuple[float, float]:
    """Deduct amount from cash_balance and credit chips.
    Returns (new_cash, new_chips). Raises ValueError if insufficient funds."""
    pool = get_pool()
    await _ensure_wallet(guild_id, user_id)
    row = await pool.fetchrow(
        "SELECT cash_balance FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id,
    )
    if not row or float(row["cash_balance"]) < amount:
        raise ValueError("Insufficient cash balance.")
    row = await pool.fetchrow(
        """UPDATE wallets
           SET cash_balance = cash_balance - $1,
               chips        = chips        + $1
           WHERE guild_id = $2 AND user_id = $3
           RETURNING cash_balance, chips""",
        amount, guild_id, user_id,
    )
    return float(row["cash_balance"]), float(row["chips"])


async def cashout_chips(guild_id: int, user_id: int, amount: float) -> tuple[float, float, float]:
    """Cash out chips to cash_balance with a 5% G.R.E.T.A. fee.
    Returns (cash_received, fee_taken, new_chips). Raises ValueError if insufficient chips."""
    pool = get_pool()
    await _ensure_wallet(guild_id, user_id)
    row = await pool.fetchrow(
        "SELECT chips FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id,
    )
    if not row or float(row["chips"]) < amount:
        raise ValueError("Insufficient chips.")
    fee = round(amount * 0.05, 2)
    received = round(amount - fee, 2)
    row = await pool.fetchrow(
        """UPDATE wallets
           SET chips        = chips        - $1,
               cash_balance = cash_balance + $2
           WHERE guild_id = $3 AND user_id = $4
           RETURNING chips""",
        amount, received, guild_id, user_id,
    )
    return received, fee, float(row["chips"])


# ── House pot ─────────────────────────────────────────────────────────────────

async def get_house_pot(guild_id: int) -> float:
    pool = get_pool()
    await _ensure_guild(guild_id)
    row = await pool.fetchrow(
        "SELECT casino_house_pot FROM guilds WHERE guild_id = $1",
        guild_id,
    )
    return float(row["casino_house_pot"]) if row else 0.0


async def add_to_house_pot(guild_id: int, amount: float) -> None:
    pool = get_pool()
    await _ensure_guild(guild_id)
    await pool.execute(
        "UPDATE guilds SET casino_house_pot = casino_house_pot + $1 WHERE guild_id = $2",
        amount, guild_id,
    )


async def drain_house_pot(guild_id: int) -> float:
    """Zero out the house pot and return the amount drained."""
    pool = get_pool()
    await _ensure_guild(guild_id)
    row = await pool.fetchrow(
        "SELECT casino_house_pot FROM guilds WHERE guild_id = $1",
        guild_id,
    )
    pot = float(row["casino_house_pot"]) if row else 0.0
    await pool.execute(
        "UPDATE guilds SET casino_house_pot = 0 WHERE guild_id = $1",
        guild_id,
    )
    return pot


# ── Cooldowns ─────────────────────────────────────────────────────────────────

async def get_cooldown_seconds(guild_id: int, user_id: int, cooldown: int) -> float:
    """Returns remaining cooldown in seconds (0.0 if ready to play)."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT last_played FROM casino_cooldowns WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id,
    )
    if not row:
        return 0.0
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    elapsed = (now - row["last_played"]).total_seconds()
    return max(0.0, cooldown - elapsed)


async def stamp_cooldown(guild_id: int, user_id: int) -> None:
    """Record that the user just played."""
    pool = get_pool()
    await pool.execute(
        """INSERT INTO casino_cooldowns (guild_id, user_id, last_played)
           VALUES ($1, $2, NOW())
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET last_played = NOW()""",
        guild_id, user_id,
    )


# ── Casino settings ───────────────────────────────────────────────────────────

async def get_casino_settings(guild_id: int) -> dict:
    """Return guild-level casino settings as a plain dict."""
    pool = get_pool()
    await _ensure_guild(guild_id)
    row = await pool.fetchrow(
        """SELECT casino_enabled, chip_exchange_channel_id, casino_floor_channel_id,
                  casino_max_bet, casino_tax_rate, casino_cooldown,
                  casino_house_pot, currency_symbol
           FROM guilds WHERE guild_id = $1""",
        guild_id,
    )
    return dict(row) if row else {}


async def set_casino_field(guild_id: int, field: str, value) -> None:
    """Update a single guild casino setting by column name."""
    allowed = {
        "casino_enabled", "chip_exchange_channel_id", "casino_floor_channel_id",
        "casino_max_bet", "casino_tax_rate", "casino_cooldown",
    }
    if field not in allowed:
        raise ValueError(f"Field '{field}' is not an allowed casino setting.")
    pool = get_pool()
    await _ensure_guild(guild_id)
    await pool.execute(
        f"UPDATE guilds SET {field} = $1 WHERE guild_id = $2",
        value, guild_id,
    )

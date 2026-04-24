from __future__ import annotations
from db.connection import get_pool
from datetime import datetime, timezone


# ── Chip balance ──────────────────────────────────────────────────────────────

async def get_chips(guild_id: int, user_id: int) -> float:
    pool = get_pool()
    await pool.execute(
        "INSERT INTO wallets (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        guild_id, user_id
    )
    row = await pool.fetchrow(
        "SELECT chips FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id
    )
    return float(row["chips"]) if row else 0.0


async def add_chips(guild_id: int, user_id: int, amount: float) -> float:
    """Add chips (positive) or deduct chips (negative). Returns new balance."""
    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO wallets (guild_id, user_id, chips)
           VALUES ($1, $2, $3)
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET chips = wallets.chips + $3
           RETURNING chips""",
        guild_id, user_id, amount
    )
    return float(row["chips"])


async def set_chips(guild_id: int, user_id: int, amount: float) -> None:
    pool = get_pool()
    await pool.execute(
        """INSERT INTO wallets (guild_id, user_id, chips)
           VALUES ($1, $2, $3)
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET chips = $3""",
        guild_id, user_id, amount
    )


# ── Wallet (cash balance) for chip buy/cashout ────────────────────────────────

async def get_cash(guild_id: int, user_id: int) -> float:
    pool = get_pool()
    await pool.execute(
        "INSERT INTO wallets (guild_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        guild_id, user_id
    )
    row = await pool.fetchrow(
        "SELECT cash_balance FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id
    )
    return float(row["cash_balance"]) if row else 0.0


async def transfer_cash_to_chips(guild_id: int, user_id: int, amount: float) -> tuple[float, float]:
    """Deduct cash, add chips 1:1. Returns (new_cash, new_chips)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """UPDATE wallets
                   SET cash_balance = cash_balance - $3,
                       chips        = chips        + $3
                   WHERE guild_id = $1 AND user_id = $2
                     AND cash_balance >= $3
                   RETURNING cash_balance, chips""",
                guild_id, user_id, amount
            )
    if not row:
        raise ValueError("Insufficient cash balance.")
    return float(row["cash_balance"]), float(row["chips"])


async def cashout_chips(guild_id: int, user_id: int, chips: float, fee_pct: float = 5.0) -> tuple[float, float, float]:
    """
    Convert chips to cash, deducting G.R.E.T.A.'s cashout fee.
    Returns (cash_received, fee_taken, new_chips).
    """
    fee      = round(chips * fee_pct / 100, 2)
    received = round(chips - fee, 2)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """UPDATE wallets
                   SET chips        = chips        - $3,
                       cash_balance = cash_balance + $4
                   WHERE guild_id = $1 AND user_id = $2
                     AND chips >= $3
                   RETURNING chips""",
                guild_id, user_id, chips, received
            )
    if not row:
        raise ValueError("Insufficient chips.")
    return received, fee, float(row["chips"])


# ── House pot ─────────────────────────────────────────────────────────────────

async def add_to_house_pot(guild_id: int, amount: float) -> float:
    pool = get_pool()
    row = await pool.fetchrow(
        """UPDATE guilds SET casino_house_pot = casino_house_pot + $2
           WHERE guild_id = $1
           RETURNING casino_house_pot""",
        guild_id, amount
    )
    return float(row["casino_house_pot"]) if row else 0.0


async def get_house_pot(guild_id: int) -> float:
    pool = get_pool()
    row = await pool.fetchrow("SELECT casino_house_pot FROM guilds WHERE guild_id = $1", guild_id)
    return float(row["casino_house_pot"]) if row else 0.0


async def drain_house_pot(guild_id: int) -> float:
    """Zero out the house pot. Returns the amount drained."""
    pool = get_pool()
    row = await pool.fetchrow(
        """UPDATE guilds SET casino_house_pot = 0
           WHERE guild_id = $1
           RETURNING casino_house_pot""",
        guild_id
    )
    # casino_house_pot is now 0; return what it was
    pot = await pool.fetchval("SELECT casino_house_pot FROM guilds WHERE guild_id = $1", guild_id)
    return float(row["casino_house_pot"]) if row else 0.0


# ── Cooldown ──────────────────────────────────────────────────────────────────

async def get_cooldown_seconds(guild_id: int, user_id: int, cooldown: int) -> float:
    """Returns seconds remaining on cooldown, or 0 if free to play."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT last_played FROM casino_cooldowns WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id
    )
    if not row:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - row["last_played"]).total_seconds()
    remaining = cooldown - elapsed
    return max(0.0, remaining)


async def stamp_cooldown(guild_id: int, user_id: int) -> None:
    pool = get_pool()
    await pool.execute(
        """INSERT INTO casino_cooldowns (guild_id, user_id, last_played)
           VALUES ($1, $2, NOW())
           ON CONFLICT (guild_id, user_id)
           DO UPDATE SET last_played = NOW()""",
        guild_id, user_id
    )


# ── Guild casino settings ─────────────────────────────────────────────────────

async def get_casino_settings(guild_id: int) -> dict:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM guilds WHERE guild_id = $1", guild_id)
    return dict(row) if row else {}


async def set_casino_field(guild_id: int, field: str, value) -> None:
    """Generic setter for any casino-related guild column."""
    pool = get_pool()
    await pool.execute(
        f"UPDATE guilds SET {field} = $2 WHERE guild_id = $1",
        guild_id, value
    )

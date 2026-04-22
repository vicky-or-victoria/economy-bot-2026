import random
import math
from db.connection import get_pool


async def create_stock(guild_id: int, ticker: str, name: str, stock_type: str = "simulated",
                       business_id: int = None, initial_price: float = 10.0,
                       ipo_price: float = None) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO stocks (guild_id, ticker, name, stock_type, business_id, current_price, ipo_price, ipo_completed)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (guild_id, ticker) DO NOTHING
           RETURNING *""",
        guild_id, ticker, name, stock_type, business_id, initial_price,
        ipo_price or initial_price,
        stock_type == "simulated"
    )
    if row:
        await record_price(row["id"], initial_price)
    return dict(row) if row else None


async def complete_ipo(stock_id: int):
    pool = get_pool()
    await pool.execute("UPDATE stocks SET ipo_completed = TRUE WHERE id = $1", stock_id)


async def get_all_stocks(guild_id: int, public_only: bool = True) -> list:
    pool = get_pool()
    if public_only:
        return await pool.fetch(
            """SELECT * FROM stocks
               WHERE guild_id = $1
                 AND (stock_type = 'simulated' OR ipo_completed = TRUE)
               ORDER BY ticker""",
            guild_id
        )
    return await pool.fetch("SELECT * FROM stocks WHERE guild_id = $1 ORDER BY ticker", guild_id)


async def get_stock(stock_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM stocks WHERE id = $1", stock_id)
    return dict(row) if row else None


async def get_stock_by_ticker(guild_id: int, ticker: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM stocks WHERE guild_id = $1 AND ticker = $2",
        guild_id, ticker.upper()
    )
    return dict(row) if row else None


async def get_stock_by_business(business_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM stocks WHERE business_id = $1", business_id)
    return dict(row) if row else None


async def record_price(stock_id: int, price: float):
    pool = get_pool()
    await pool.execute("INSERT INTO stock_history (stock_id, price) VALUES ($1, $2)", stock_id, price)


async def get_price_history(stock_id: int, limit: int = 50) -> list:
    pool = get_pool()
    return await pool.fetch(
        """SELECT price, recorded_at FROM stock_history
           WHERE stock_id = $1 ORDER BY recorded_at DESC LIMIT $2""",
        stock_id, limit
    )


async def update_price(stock_id: int, new_price: float):
    pool = get_pool()
    await pool.execute("UPDATE stocks SET current_price = $1 WHERE id = $2", new_price, stock_id)
    await record_price(stock_id, new_price)


async def delete_stock(guild_id: int, ticker: str) -> bool:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, stock_type FROM stocks WHERE guild_id = $1 AND ticker = $2",
        guild_id, ticker.upper()
    )
    if not row or row["stock_type"] != "simulated":
        return False
    await pool.execute("DELETE FROM stocks WHERE id = $1", row["id"])
    return True


async def tick_all_stocks(guild_id: int, event_multiplier: float = 1.0):
    pool = get_pool()
    stocks = await get_all_stocks(guild_id, public_only=True)
    for stock in stocks:
        drift = 0.001
        volatility = 0.03
        shock = random.gauss(0, 1)
        change = math.exp(drift + volatility * shock) * event_multiplier
        new_price = max(0.01, float(stock["current_price"]) * change)
        await update_price(stock["id"], round(new_price, 4))


async def get_holdings(guild_id: int, user_id: int) -> list:
    pool = get_pool()
    return await pool.fetch(
        """SELECT sh.*, s.ticker, s.name, s.current_price, s.business_id
           FROM stock_holdings sh
           JOIN stocks s ON s.id = sh.stock_id
           WHERE sh.guild_id = $1 AND sh.user_id = $2""",
        guild_id, user_id
    )


async def get_holders_of_stock(stock_id: int) -> list:
    """Return all holders of a given stock with share counts."""
    pool = get_pool()
    return await pool.fetch(
        """SELECT sh.user_id, sh.shares, sh.guild_id
           FROM stock_holdings sh
           WHERE sh.stock_id = $1 AND sh.shares > 0""",
        stock_id
    )


async def get_total_shares(stock_id: int) -> float:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT COALESCE(SUM(shares), 0) AS total FROM stock_holdings WHERE stock_id = $1",
        stock_id
    )
    return float(row["total"])


async def buy_stock(guild_id: int, user_id: int, stock_id: int, shares: float, price_per_share: float):
    pool = get_pool()
    existing = await pool.fetchrow(
        "SELECT shares, avg_buy_price FROM stock_holdings WHERE guild_id = $1 AND user_id = $2 AND stock_id = $3",
        guild_id, user_id, stock_id
    )
    if existing:
        old_shares = float(existing["shares"])
        old_avg = float(existing["avg_buy_price"])
        new_shares = old_shares + shares
        new_avg = ((old_shares * old_avg) + (shares * price_per_share)) / new_shares
        await pool.execute(
            """UPDATE stock_holdings SET shares = $1, avg_buy_price = $2
               WHERE guild_id = $3 AND user_id = $4 AND stock_id = $5""",
            new_shares, new_avg, guild_id, user_id, stock_id
        )
    else:
        await pool.execute(
            """INSERT INTO stock_holdings (guild_id, user_id, stock_id, shares, avg_buy_price)
               VALUES ($1, $2, $3, $4, $5)""",
            guild_id, user_id, stock_id, shares, price_per_share
        )


async def sell_stock(guild_id: int, user_id: int, stock_id: int, shares: float) -> tuple[bool, float, float]:
    """
    Returns (success, avg_buy_price, shares_sold).
    avg_buy_price is needed for profit tax calculation.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT shares, avg_buy_price FROM stock_holdings WHERE guild_id = $1 AND user_id = $2 AND stock_id = $3",
        guild_id, user_id, stock_id
    )
    if not row or float(row["shares"]) < shares:
        return False, 0.0, 0.0
    avg_buy = float(row["avg_buy_price"])
    new_shares = float(row["shares"]) - shares
    if new_shares <= 0:
        await pool.execute(
            "DELETE FROM stock_holdings WHERE guild_id = $1 AND user_id = $2 AND stock_id = $3",
            guild_id, user_id, stock_id
        )
    else:
        await pool.execute(
            "UPDATE stock_holdings SET shares = $1 WHERE guild_id = $2 AND user_id = $3 AND stock_id = $4",
            new_shares, guild_id, user_id, stock_id
        )
    return True, avg_buy, shares

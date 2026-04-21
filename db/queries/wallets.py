from db.connection import get_pool


async def get_or_create_wallet(guild_id: int, user_id: int) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM wallets WHERE guild_id = $1 AND user_id = $2",
        guild_id, user_id
    )
    if not row:
        row = await pool.fetchrow(
            """INSERT INTO wallets (guild_id, user_id)
               VALUES ($1, $2)
               ON CONFLICT DO NOTHING
               RETURNING *""",
            guild_id, user_id
        )
        if not row:
            row = await pool.fetchrow(
                "SELECT * FROM wallets WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id
            )
    return dict(row)


async def add_cash(guild_id: int, user_id: int, amount: float):
    pool = get_pool()
    await get_or_create_wallet(guild_id, user_id)
    await pool.execute(
        "UPDATE wallets SET cash_balance = cash_balance + $1 WHERE guild_id = $2 AND user_id = $3",
        amount, guild_id, user_id
    )


async def transfer_cash_to_digital(guild_id: int, user_id: int, amount: float) -> bool:
    pool = get_pool()
    wallet = await get_or_create_wallet(guild_id, user_id)
    if wallet["cash_balance"] < amount:
        return False
    await pool.execute(
        """UPDATE wallets
           SET cash_balance = cash_balance - $1,
               digital_balance = digital_balance + $1
           WHERE guild_id = $2 AND user_id = $3""",
        amount, guild_id, user_id
    )
    return True


async def transfer_digital_to_cash(guild_id: int, user_id: int, amount: float) -> bool:
    pool = get_pool()
    wallet = await get_or_create_wallet(guild_id, user_id)
    if wallet["digital_balance"] < amount:
        return False
    await pool.execute(
        """UPDATE wallets
           SET digital_balance = digital_balance - $1,
               cash_balance = cash_balance + $1
           WHERE guild_id = $2 AND user_id = $3""",
        amount, guild_id, user_id
    )
    return True


async def admin_grant(guild_id: int, user_id: int, amount: float, wallet_type: str = "cash"):
    pool = get_pool()
    await get_or_create_wallet(guild_id, user_id)
    col = "cash_balance" if wallet_type == "cash" else "digital_balance"
    await pool.execute(
        f"UPDATE wallets SET {col} = {col} + $1 WHERE guild_id = $2 AND user_id = $3",
        amount, guild_id, user_id
    )


async def admin_deduct(guild_id: int, user_id: int, amount: float, wallet_type: str = "cash") -> bool:
    pool = get_pool()
    wallet = await get_or_create_wallet(guild_id, user_id)
    col = "cash_balance" if wallet_type == "cash" else "digital_balance"
    if wallet[col] < amount:
        return False
    await pool.execute(
        f"UPDATE wallets SET {col} = {col} - $1 WHERE guild_id = $2 AND user_id = $3",
        amount, guild_id, user_id
    )
    return True

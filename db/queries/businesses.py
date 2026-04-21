from db.connection import get_pool


async def create_application(guild_id: int, owner_id: int, name: str, description: str, industry: str) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO business_applications (guild_id, owner_id, name, description, industry)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        guild_id, owner_id, name, description, industry
    )
    return row["id"]


async def get_pending_applications(guild_id: int) -> list:
    pool = get_pool()
    return await pool.fetch(
        "SELECT * FROM business_applications WHERE guild_id = $1 AND status = 'pending' ORDER BY created_at",
        guild_id
    )


async def get_application(app_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM business_applications WHERE id = $1", app_id)
    return dict(row) if row else None


async def approve_application(app_id: int) -> dict:
    pool = get_pool()
    await pool.execute(
        "UPDATE business_applications SET status = 'approved' WHERE id = $1", app_id
    )
    app = await pool.fetchrow("SELECT * FROM business_applications WHERE id = $1", app_id)
    row = await pool.fetchrow(
        """INSERT INTO businesses (guild_id, owner_id, name, description, industry, is_public)
           VALUES ($1, $2, $3, $4, $5, FALSE) RETURNING *""",
        app["guild_id"], app["owner_id"], app["name"], app["description"], app["industry"]
    )
    return dict(row)


async def reject_application(app_id: int):
    pool = get_pool()
    await pool.execute(
        "UPDATE business_applications SET status = 'rejected' WHERE id = $1", app_id
    )


async def get_businesses_by_owner(guild_id: int, owner_id: int) -> list:
    pool = get_pool()
    return await pool.fetch(
        "SELECT * FROM businesses WHERE guild_id = $1 AND owner_id = $2 ORDER BY created_at",
        guild_id, owner_id
    )


async def get_business(business_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM businesses WHERE id = $1", business_id)
    return dict(row) if row else None


async def get_all_businesses(guild_id: int) -> list:
    pool = get_pool()
    return await pool.fetch(
        "SELECT * FROM businesses WHERE guild_id = $1 ORDER BY created_at",
        guild_id
    )


async def update_business_message(business_id: int, message_id: int, thread_id: int = None):
    pool = get_pool()
    await pool.execute(
        "UPDATE businesses SET post_message_id = $1, post_thread_id = $2 WHERE id = $3",
        message_id, thread_id, business_id
    )


async def set_business_public(business_id: int, is_public: bool):
    pool = get_pool()
    await pool.execute(
        "UPDATE businesses SET is_public = $1 WHERE id = $2",
        is_public, business_id
    )


async def update_daily(business_id: int, revenue_gain: float):
    pool = get_pool()
    await pool.execute(
        """UPDATE businesses
           SET last_daily = NOW(), revenue = revenue + $1
           WHERE id = $2""",
        revenue_gain, business_id
    )


async def update_work(business_id: int, revenue_gain: float):
    pool = get_pool()
    await pool.execute(
        """UPDATE businesses
           SET last_work = NOW(), revenue = revenue + $1
           WHERE id = $2""",
        revenue_gain, business_id
    )

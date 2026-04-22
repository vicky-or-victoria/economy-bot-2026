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
    await pool.execute("UPDATE business_applications SET status = 'approved' WHERE id = $1", app_id)
    app = await pool.fetchrow("SELECT * FROM business_applications WHERE id = $1", app_id)
    row = await pool.fetchrow(
        """INSERT INTO businesses (guild_id, owner_id, name, description, industry, is_public)
           VALUES ($1, $2, $3, $4, $5, FALSE) RETURNING *""",
        app["guild_id"], app["owner_id"], app["name"], app["description"], app["industry"]
    )
    return dict(row)


async def reject_application(app_id: int):
    pool = get_pool()
    await pool.execute("UPDATE business_applications SET status = 'rejected' WHERE id = $1", app_id)


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
    return await pool.fetch("SELECT * FROM businesses WHERE guild_id = $1 ORDER BY created_at", guild_id)


async def update_business_message(business_id: int, message_id: int, thread_id: int = None):
    pool = get_pool()
    await pool.execute(
        "UPDATE businesses SET post_message_id = $1, post_thread_id = $2 WHERE id = $3",
        message_id, thread_id, business_id
    )


async def set_business_public(business_id: int, is_public: bool):
    pool = get_pool()
    await pool.execute("UPDATE businesses SET is_public = $1 WHERE id = $2", is_public, business_id)


async def set_ceo_salary(business_id: int, salary: float):
    pool = get_pool()
    await pool.execute("UPDATE businesses SET ceo_salary = $1 WHERE id = $2", salary, business_id)


async def claim_daily_salary(business_id: int) -> float:
    """
    Deposits one day of revenue into company_wallet and marks last_daily.
    Returns the salary amount that should be paid to the CEO (before tax).
    """
    pool = get_pool()
    biz = await pool.fetchrow("SELECT * FROM businesses WHERE id = $1", business_id)
    await pool.execute(
        """UPDATE businesses
           SET last_daily = NOW(),
               revenue = revenue + ceo_salary,
               company_wallet = company_wallet + ceo_salary
           WHERE id = $1""",
        business_id
    )
    return float(biz["ceo_salary"])


async def add_company_revenue(business_id: int, amount: float):
    """Add revenue to company wallet (e.g. from approved expansions)."""
    pool = get_pool()
    await pool.execute(
        "UPDATE businesses SET revenue = revenue + $1, company_wallet = company_wallet + $1 WHERE id = $2",
        amount, business_id
    )


async def deduct_company_wallet(business_id: int, amount: float) -> bool:
    """Deduct from company wallet (dividends, salary payouts). Returns False if insufficient."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT company_wallet FROM businesses WHERE id = $1", business_id)
    if not row or float(row["company_wallet"]) < amount:
        return False
    await pool.execute(
        "UPDATE businesses SET company_wallet = company_wallet - $1 WHERE id = $2",
        amount, business_id
    )
    return True


async def work_business(business_id: int) -> tuple[float, bool]:
    """
    Deposits the business's current daily revenue into the company wallet and marks last_work.
    Returns (amount_or_remaining_hours, success).
      - On success:  (revenue_deposited, True)
      - On cooldown: (remaining_hours_float, False)
      - No revenue:  (0.0, False)
    Uses a 20-hour cooldown separate from the CEO salary claim.
    """
    from datetime import datetime, timezone
    pool = get_pool()
    biz = await pool.fetchrow("SELECT * FROM businesses WHERE id = $1", business_id)
    if not biz:
        return 0.0, False

    daily_revenue = float(biz["revenue"])
    if daily_revenue <= 0:
        return 0.0, False

    if biz["last_work"]:
        last = biz["last_work"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        if elapsed < 20:
            remaining_hours = 20 - elapsed
            return remaining_hours, False

    await pool.execute(
        """UPDATE businesses
           SET last_work = NOW(),
               company_wallet = company_wallet + $1
           WHERE id = $2""",
        daily_revenue, business_id
    )
    return daily_revenue, True


async def delete_business(business_id: int) -> dict | None:
    """
    Deletes a business and returns its snapshot before deletion.
    Also deletes associated stocks (stock_holdings cascade from stocks FK).
    Expansion proposals cascade automatically via ON DELETE CASCADE.
    """
    pool = get_pool()
    biz = await pool.fetchrow("SELECT * FROM businesses WHERE id = $1", business_id)
    if not biz:
        return None
    # Remove associated stocks first so stock_holdings cascade properly
    await pool.execute("DELETE FROM stocks WHERE business_id = $1", business_id)
    await pool.execute("DELETE FROM businesses WHERE id = $1", business_id)
    return dict(biz)


async def get_businesses_by_guild(guild_id: int) -> list:
    """Get all businesses in a guild (alias for get_all_businesses, kept for clarity)."""
    pool = get_pool()
    return await pool.fetch(
        "SELECT * FROM businesses WHERE guild_id = $1 ORDER BY created_at",
        guild_id
    )


# ── Expansion proposals ───────────────────────────────────────────────────────

async def create_expansion_proposal(business_id: int, guild_id: int, owner_id: int,
                                     title: str, description: str, estimated_revenue: float) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO expansion_proposals
               (business_id, guild_id, owner_id, title, description, estimated_revenue)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
        business_id, guild_id, owner_id, title, description, estimated_revenue
    )
    return row["id"]


async def get_pending_expansions(guild_id: int) -> list:
    pool = get_pool()
    return await pool.fetch(
        """SELECT ep.*, b.name AS business_name
           FROM expansion_proposals ep
           JOIN businesses b ON b.id = ep.business_id
           WHERE ep.guild_id = $1 AND ep.status = 'pending'
           ORDER BY ep.created_at""",
        guild_id
    )


async def get_expansion(proposal_id: int) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        """SELECT ep.*, b.name AS business_name
           FROM expansion_proposals ep
           JOIN businesses b ON b.id = ep.business_id
           WHERE ep.id = $1""",
        proposal_id
    )
    return dict(row) if row else None


async def resolve_expansion(proposal_id: int, status: str, admin_note: str, approved_revenue: float | None):
    pool = get_pool()
    await pool.execute(
        """UPDATE expansion_proposals
           SET status = $1, admin_note = $2, approved_revenue = $3
           WHERE id = $4""",
        status, admin_note, approved_revenue, proposal_id
    )
    if status == "approved" and approved_revenue:
        proposal = await pool.fetchrow("SELECT business_id FROM expansion_proposals WHERE id = $1", proposal_id)
        await pool.execute(
            "UPDATE businesses SET revenue = revenue + $1, company_wallet = company_wallet + $1 WHERE id = $2",
            approved_revenue, proposal["business_id"]
        )

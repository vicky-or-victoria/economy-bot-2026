import asyncio
import asyncpg
import os

SCHEMA = """
-- Per-guild configuration
CREATE TABLE IF NOT EXISTS guilds (
    guild_id            BIGINT PRIMARY KEY,
    admin_role_id       BIGINT,
    menu_channel_id     BIGINT,
    stock_channel_id    BIGINT,
    business_channel_id BIGINT,
    stock_message_id    BIGINT,
    menu_message_id     BIGINT,
    currency_name       TEXT NOT NULL DEFAULT 'Credits',
    currency_symbol     TEXT NOT NULL DEFAULT 'C',
    usd_rate            NUMERIC(18, 6) NOT NULL DEFAULT 1.0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wallets (
    id              SERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    cash_balance    NUMERIC(18, 2) NOT NULL DEFAULT 0,
    digital_balance NUMERIC(18, 2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_experience (
    id        SERIAL PRIMARY KEY,
    guild_id  BIGINT NOT NULL,
    user_id   BIGINT NOT NULL,
    xp        BIGINT NOT NULL DEFAULT 0,
    job       TEXT NOT NULL DEFAULT 'unemployed',
    last_work TIMESTAMPTZ,
    UNIQUE (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS business_applications (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    owner_id    BIGINT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    industry    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS businesses (
    id              SERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    owner_id        BIGINT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    industry        TEXT NOT NULL,
    post_message_id BIGINT,
    post_thread_id  BIGINT,
    is_public       BOOLEAN NOT NULL DEFAULT FALSE,
    revenue         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    last_daily      TIMESTAMPTZ,
    last_work       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stocks (
    id            SERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    ticker        TEXT NOT NULL,
    name          TEXT NOT NULL,
    stock_type    TEXT NOT NULL DEFAULT 'simulated',
    business_id   INT REFERENCES businesses(id),
    current_price NUMERIC(18, 4) NOT NULL DEFAULT 10.0,
    ipo_price     NUMERIC(18, 4),
    ipo_completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (guild_id, ticker)
);

CREATE TABLE IF NOT EXISTS stock_history (
    id          SERIAL PRIMARY KEY,
    stock_id    INT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    price       NUMERIC(18, 4) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_events (
    id           SERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    impact       NUMERIC(5, 2) NOT NULL DEFAULT 0,
    triggered_by BIGINT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock_holdings (
    id       SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id  BIGINT NOT NULL,
    stock_id INT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    shares   NUMERIC(18, 4) NOT NULL DEFAULT 0,
    UNIQUE (guild_id, user_id, stock_id)
);

CREATE INDEX IF NOT EXISTS idx_wallets_guild_user  ON wallets(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_businesses_guild    ON businesses(guild_id);
CREATE INDEX IF NOT EXISTS idx_stocks_guild        ON stocks(guild_id);
CREATE INDEX IF NOT EXISTS idx_stock_history_stock ON stock_history(stock_id);
CREATE INDEX IF NOT EXISTS idx_stock_history_time  ON stock_history(recorded_at);
CREATE INDEX IF NOT EXISTS idx_user_xp_guild_user  ON user_experience(guild_id, user_id);
"""

MIGRATIONS = [
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS post_thread_id BIGINT",
    "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS ipo_price NUMERIC(18, 4)",
    "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS ipo_completed BOOLEAN NOT NULL DEFAULT FALSE",
]


async def migrate():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set.")
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await conn.execute(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                await conn.execute(stmt)
            except Exception:
                pass
        print("Migration complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())

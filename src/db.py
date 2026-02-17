"""Database layer with SQLite persistence for all application state."""

import aiosqlite
import os
import json
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "automator.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    proxy TEXT,
    status TEXT DEFAULT 'new',  -- new, warming, active, paused, disabled
    warmup_started_at TEXT,
    warmup_completed_at TEXT,
    total_warmup_hours REAL DEFAULT 0,
    warmup_actions_count INTEGER DEFAULT 0,
    daily_send_count INTEGER DEFAULT 0,
    daily_send_date TEXT,
    campaign_day INTEGER DEFAULT 0,
    campaign_day_date TEXT,
    block_count INTEGER DEFAULT 0,
    cooldown_until TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    full_name TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    follower_count INTEGER DEFAULT 0,
    following_count INTEGER DEFAULT 0,
    is_private INTEGER DEFAULT 0,
    has_bio INTEGER DEFAULT 0,
    source_account TEXT DEFAULT '',
    campaign_id INTEGER,
    status TEXT DEFAULT 'pending',  -- pending, messaged, skipped, failed
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER,
    account_username TEXT NOT NULL,
    target_username TEXT NOT NULL,
    message_content TEXT NOT NULL,
    status TEXT DEFAULT 'sent',  -- sent, failed, blocked
    error TEXT,
    sent_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'created',  -- created, warming, scaling, active, paused, completed
    template TEXT NOT NULL,
    niche TEXT DEFAULT '',
    accounts TEXT DEFAULT '[]',  -- JSON array of usernames
    total_sent INTEGER DEFAULT 0,
    total_failed INTEGER DEFAULT 0,
    total_targets INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS warmup_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_username TEXT NOT NULL,
    action_type TEXT NOT NULL,  -- scroll_feed, view_stories, like_post, view_reels, follow_suggested
    details TEXT DEFAULT '',
    performed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proxy_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_url TEXT NOT NULL,
    proxy_ip TEXT,
    account_username TEXT NOT NULL,
    assigned_at TEXT DEFAULT (datetime('now')),
    UNIQUE(proxy_url, account_username)
);

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_username TEXT NOT NULL,
    hour_key TEXT NOT NULL,       -- YYYY-MM-DD-HH
    hour_count INTEGER DEFAULT 0,
    day_key TEXT NOT NULL,        -- YYYY-MM-DD
    day_count INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account_username)
);

CREATE TABLE IF NOT EXISTS message_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_username TEXT NOT NULL,
    message_hash TEXT NOT NULL,
    sent_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

CURRENT_VERSION = 1


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open the database connection and ensure schema exists."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # Set version if not present
        async with self._db.execute("SELECT version FROM schema_version LIMIT 1") as cur:
            row = await cur.fetchone()
            if row is None:
                await self._db.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_VERSION,))
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not connected. Call connect() first."
        return self._db

    # ── Accounts ──────────────────────────────────────────────

    async def add_account(self, username: str, password: str, proxy: str = "") -> int:
        """Add a new Instagram account."""
        await self.db.execute(
            "INSERT OR IGNORE INTO accounts (username, password, proxy) VALUES (?, ?, ?)",
            (username, password, proxy),
        )
        await self.db.commit()
        async with self.db.execute("SELECT id FROM accounts WHERE username = ?", (username,)) as cur:
            row = await cur.fetchone()
            return row["id"] if row else 0

    async def get_account(self, username: str) -> Optional[dict]:
        """Get account by username."""
        async with self.db.execute("SELECT * FROM accounts WHERE username = ?", (username,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_accounts(self) -> list[dict]:
        """Get all accounts."""
        async with self.db.execute("SELECT * FROM accounts ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_account(self, username: str, **kwargs) -> None:
        """Update account fields."""
        if not kwargs:
            return
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [username]
        await self.db.execute(f"UPDATE accounts SET {sets} WHERE username = ?", vals)
        await self.db.commit()

    # ── Targets ───────────────────────────────────────────────

    async def add_target(self, username: str, full_name: str = "", bio: str = "",
                         follower_count: int = 0, following_count: int = 0,
                         is_private: bool = False, source_account: str = "",
                         campaign_id: int = 0) -> None:
        """Add a scraping target."""
        await self.db.execute(
            """INSERT OR IGNORE INTO targets
               (username, full_name, bio, follower_count, following_count, is_private, has_bio, source_account, campaign_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, full_name, bio, follower_count, following_count,
             int(is_private), int(bool(bio.strip())), source_account, campaign_id),
        )
        await self.db.commit()

    async def add_targets_bulk(self, targets: list[dict]) -> int:
        """Add multiple targets at once. Returns count inserted."""
        count = 0
        for t in targets:
            try:
                await self.db.execute(
                    """INSERT OR IGNORE INTO targets
                       (username, full_name, bio, follower_count, following_count, is_private, has_bio, source_account, campaign_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (t.get("username", ""), t.get("full_name", ""), t.get("bio", ""),
                     t.get("follower_count", 0), t.get("following_count", 0),
                     int(t.get("is_private", False)), int(bool(t.get("bio", "").strip())),
                     t.get("source_account", ""), t.get("campaign_id", 0)),
                )
                count += 1
            except Exception:
                pass
        await self.db.commit()
        return count

    async def get_pending_targets(self, campaign_id: int, limit: int = 50) -> list[dict]:
        """Get pending targets for a campaign."""
        async with self.db.execute(
            """SELECT * FROM targets WHERE campaign_id = ? AND status = 'pending'
               ORDER BY id LIMIT ?""",
            (campaign_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_target_status(self, username: str, status: str) -> None:
        """Update target status."""
        await self.db.execute("UPDATE targets SET status = ? WHERE username = ?", (status, username))
        await self.db.commit()

    async def get_all_targets(self, campaign_id: int = 0) -> list[dict]:
        """Get all targets, optionally filtered by campaign."""
        if campaign_id:
            q = "SELECT * FROM targets WHERE campaign_id = ? ORDER BY id"
            async with self.db.execute(q, (campaign_id,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
        async with self.db.execute("SELECT * FROM targets ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def is_target_messaged(self, target_username: str, account_username: str) -> bool:
        """Check if we already messaged this target from this account."""
        async with self.db.execute(
            "SELECT id FROM messages WHERE account_username = ? AND target_username = ? AND status = 'sent'",
            (account_username, target_username),
        ) as cur:
            return (await cur.fetchone()) is not None

    # ── Messages ──────────────────────────────────────────────

    async def log_message(self, campaign_id: int, account_username: str,
                          target_username: str, message_content: str,
                          status: str = "sent", error: str = "") -> None:
        """Log a sent (or failed) DM."""
        await self.db.execute(
            """INSERT INTO messages (campaign_id, account_username, target_username, message_content, status, error)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (campaign_id, account_username, target_username, message_content, status, error),
        )
        await self.db.commit()

    async def get_messages(self, campaign_id: int = 0, account: str = "") -> list[dict]:
        """Get message log with optional filters."""
        q = "SELECT * FROM messages WHERE 1=1"
        params: list = []
        if campaign_id:
            q += " AND campaign_id = ?"
            params.append(campaign_id)
        if account:
            q += " AND account_username = ?"
            params.append(account)
        q += " ORDER BY sent_at DESC"
        async with self.db.execute(q, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_sent_count_today(self, account_username: str) -> int:
        """Get number of messages sent today by an account."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE account_username = ? AND status = 'sent' AND date(sent_at) = ?",
            (account_username, today),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # ── Campaigns ─────────────────────────────────────────────

    async def create_campaign(self, name: str, template: str, niche: str = "",
                              accounts: list[str] | None = None) -> int:
        """Create a new campaign."""
        accts = json.dumps(accounts or [])
        await self.db.execute(
            "INSERT INTO campaigns (name, template, niche, accounts) VALUES (?, ?, ?, ?)",
            (name, template, niche, accts),
        )
        await self.db.commit()
        async with self.db.execute("SELECT last_insert_rowid() as id") as cur:
            row = await cur.fetchone()
            return row["id"] if row else 0

    async def get_campaign(self, campaign_id: int) -> Optional[dict]:
        """Get campaign by ID."""
        async with self.db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_campaigns(self) -> list[dict]:
        """Get all campaigns."""
        async with self.db.execute("SELECT * FROM campaigns ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_campaign(self, campaign_id: int, **kwargs) -> None:
        """Update campaign fields."""
        if not kwargs:
            return
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [campaign_id]
        await self.db.execute(f"UPDATE campaigns SET {sets} WHERE id = ?", vals)
        await self.db.commit()

    # ── Warmup Log ────────────────────────────────────────────

    async def log_warmup_action(self, account_username: str, action_type: str, details: str = "") -> None:
        """Log a warmup action."""
        await self.db.execute(
            "INSERT INTO warmup_log (account_username, action_type, details) VALUES (?, ?, ?)",
            (account_username, action_type, details),
        )
        await self.db.commit()

    async def get_warmup_actions_count(self, account_username: str) -> int:
        """Get total warmup actions for an account."""
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM warmup_log WHERE account_username = ?",
            (account_username,),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # ── Proxy Assignments ────────────────────────────────────

    async def assign_proxy(self, proxy_url: str, proxy_ip: str, account_username: str) -> None:
        """Assign a proxy to an account."""
        await self.db.execute(
            "INSERT OR REPLACE INTO proxy_assignments (proxy_url, proxy_ip, account_username) VALUES (?, ?, ?)",
            (proxy_url, proxy_ip, account_username),
        )
        await self.db.commit()

    async def get_accounts_on_proxy(self, proxy_ip: str) -> int:
        """Count how many accounts share a proxy IP."""
        async with self.db.execute(
            "SELECT COUNT(*) as cnt FROM proxy_assignments WHERE proxy_ip = ?",
            (proxy_ip,),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    async def get_proxy_for_account(self, account_username: str) -> Optional[str]:
        """Get assigned proxy URL for an account."""
        async with self.db.execute(
            "SELECT proxy_url FROM proxy_assignments WHERE account_username = ?",
            (account_username,),
        ) as cur:
            row = await cur.fetchone()
            return row["proxy_url"] if row else None

    # ── Rate Limits ──────────────────────────────────────────

    async def get_rate_limit(self, account_username: str) -> Optional[dict]:
        """Get rate limit record for account."""
        async with self.db.execute(
            "SELECT * FROM rate_limits WHERE account_username = ?", (account_username,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_rate_limit(self, account_username: str, hour_key: str, hour_count: int,
                                 day_key: str, day_count: int) -> None:
        """Insert or update rate limit counters."""
        await self.db.execute(
            """INSERT INTO rate_limits (account_username, hour_key, hour_count, day_key, day_count)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_username) DO UPDATE SET
               hour_key = excluded.hour_key, hour_count = excluded.hour_count,
               day_key = excluded.day_key, day_count = excluded.day_count,
               updated_at = datetime('now')""",
            (account_username, hour_key, hour_count, day_key, day_count),
        )
        await self.db.commit()

    # ── Message History (for variation tracking) ─────────────

    async def add_message_hash(self, account_username: str, message_hash: str) -> None:
        """Track sent message hash for variation enforcement."""
        await self.db.execute(
            "INSERT INTO message_history (account_username, message_hash) VALUES (?, ?)",
            (account_username, message_hash),
        )
        await self.db.commit()

    async def get_last_message_hash(self, account_username: str) -> Optional[str]:
        """Get the most recent message hash for an account."""
        async with self.db.execute(
            "SELECT message_hash FROM message_history WHERE account_username = ? ORDER BY id DESC LIMIT 1",
            (account_username,),
        ) as cur:
            row = await cur.fetchone()
            return row["message_hash"] if row else None

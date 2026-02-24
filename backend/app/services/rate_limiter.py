"""Rate limiter with per-account hourly/daily counters and exponential backoff."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("ig-automator.ratelimit")


class RateLimiter:
    """Enforces per-account hourly and daily DM sending limits."""

    def __init__(self, config: dict, db: any) -> None:
        dm_cfg = config.get("dm", {})
        self.hourly_limit: int = dm_cfg.get("hourly_limit", 10)
        self.daily_limit: int = dm_cfg.get("daily_limit", 50)
        self.db = db

    def _hour_key(self) -> str:
        """Current hour key like 2024-01-15-14."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

    def _day_key(self) -> str:
        """Current day key like 2024-01-15."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def can_send(self, account_username: str) -> bool:
        """Check if the account is within rate limits."""
        record = await self.db.get_rate_limit(account_username)
        hour_key = self._hour_key()
        day_key = self._day_key()

        if record is None:
            return True

        hour_count = record["hour_count"] if record["hour_key"] == hour_key else 0
        day_count = record["day_count"] if record["day_key"] == day_key else 0

        if hour_count >= self.hourly_limit:
            logger.info("Account %s hit hourly limit (%d/%d)", account_username, hour_count, self.hourly_limit)
            return False
        if day_count >= self.daily_limit:
            logger.info("Account %s hit daily limit (%d/%d)", account_username, day_count, self.daily_limit)
            return False
        return True

    async def get_daily_allowance(self, account_username: str, campaign_day: int,
                                   scaling_start: int = 20, scaling_increment: int = 10,
                                   scaling_days: int = 4) -> int:
        """Calculate how many DMs the account can send today based on scaling."""
        if campaign_day <= 0:
            campaign_day = 1
        if campaign_day >= scaling_days:
            limit = self.daily_limit
        else:
            limit = min(scaling_start + (campaign_day - 1) * scaling_increment, self.daily_limit)

        record = await self.db.get_rate_limit(account_username)
        day_key = self._day_key()
        already_sent = 0
        if record and record["day_key"] == day_key:
            already_sent = record["day_count"]

        return max(0, limit - already_sent)

    async def record_send(self, account_username: str) -> None:
        """Increment counters after a successful send."""
        hour_key = self._hour_key()
        day_key = self._day_key()

        record = await self.db.get_rate_limit(account_username)
        if record is None:
            await self.db.upsert_rate_limit(account_username, hour_key, 1, day_key, 1)
            return

        hour_count = (record["hour_count"] + 1) if record["hour_key"] == hour_key else 1
        day_count = (record["day_count"] + 1) if record["day_key"] == day_key else 1
        await self.db.upsert_rate_limit(account_username, hour_key, hour_count, day_key, day_count)

    async def get_remaining(self, account_username: str) -> dict:
        """Get remaining sends for current hour and day."""
        record = await self.db.get_rate_limit(account_username)
        hour_key = self._hour_key()
        day_key = self._day_key()

        if record is None:
            return {"hourly_remaining": self.hourly_limit, "daily_remaining": self.daily_limit}

        hour_count = record["hour_count"] if record["hour_key"] == hour_key else 0
        day_count = record["day_count"] if record["day_key"] == day_key else 0

        return {
            "hourly_remaining": max(0, self.hourly_limit - hour_count),
            "daily_remaining": max(0, self.daily_limit - day_count),
        }

    @staticmethod
    def calculate_cooldown(block_count: int, base_hours: int = 72) -> timedelta:
        """Calculate exponential backoff cooldown duration."""
        # Exponential: 72h, 144h, 288h...
        multiplier = 2 ** max(0, block_count - 1)
        return timedelta(hours=base_hours * multiplier)

    async def wait_seconds_until_can_send(self, account_username: str) -> int:
        """Estimate seconds until the account can send again (0 if ready)."""
        if await self.can_send(account_username):
            return 0

        record = await self.db.get_rate_limit(account_username)
        if record is None:
            return 0

        hour_key = self._hour_key()
        day_key = self._day_key()

        if record["hour_key"] == hour_key and record["hour_count"] >= self.hourly_limit:
            # Wait until next hour
            now = datetime.now(timezone.utc)
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return int((next_hour - now).total_seconds()) + 1

        if record["day_key"] == day_key and record["day_count"] >= self.daily_limit:
            # Wait until next day
            now = datetime.now(timezone.utc)
            next_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return int((next_day - now).total_seconds()) + 1

        return 0

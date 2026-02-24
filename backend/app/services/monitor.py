"""Monitor for ban/block detection, action block handling, and alert notifications."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
from playwright.async_api import Page

logger = logging.getLogger("ig-automator.monitor")

BLOCK_INDICATORS = [
    "we restrict certain activity",
    "action blocked",
    "try again later",
    "we limit how often",
    "temporarily blocked",
    "your account has been temporarily",
    "suspicious activity",
    "challenge_required",
]

CHECKPOINT_INDICATORS = [
    "checkpoint",
    "verify your identity",
    "confirm it's you",
    "security code",
    "unusual login attempt",
    "confirm your identity",
]


class Monitor:
    """Detects Instagram blocks, challenges, and manages account health."""

    def __init__(self, config: dict, db: any) -> None:
        monitor_cfg = config.get("monitor", {})
        self.webhook_url: str = monitor_cfg.get("webhook_url", "")
        self.pause_hours: int = monitor_cfg.get("pause_on_block_hours", 72)
        self.max_blocks: int = monitor_cfg.get("max_blocks_before_disable", 3)
        self.db = db

    async def check_page_for_blocks(self, page: Page) -> dict:
        """Scan the current page for block/challenge indicators.

        Returns:
            {"blocked": bool, "challenge": bool, "message": str}
        """
        result = {"blocked": False, "challenge": False, "message": ""}

        try:
            content = await page.content()
            content_lower = content.lower()

            for indicator in BLOCK_INDICATORS:
                if indicator in content_lower:
                    result["blocked"] = True
                    result["message"] = indicator
                    logger.warning("Block detected: %s", indicator)
                    break

            if not result["blocked"]:
                for indicator in CHECKPOINT_INDICATORS:
                    if indicator in content_lower:
                        result["challenge"] = True
                        result["message"] = indicator
                        logger.warning("Challenge detected: %s", indicator)
                        break

            # Also check for dialog/popup elements
            dialogs = page.locator('[role="dialog"], [role="alertdialog"]')
            if await dialogs.count() > 0:
                dialog_text = await dialogs.first.inner_text()
                dialog_lower = dialog_text.lower()
                for indicator in BLOCK_INDICATORS:
                    if indicator in dialog_lower:
                        result["blocked"] = True
                        result["message"] = indicator
                        break

        except Exception as e:
            logger.error("Error checking page for blocks: %s", e)

        return result

    async def handle_block(self, account_username: str, block_message: str) -> None:
        """Handle a detected block: pause account, set cooldown, increment counter."""
        account = await self.db.get_account(account_username)
        if not account:
            return

        block_count = (account.get("block_count") or 0) + 1
        cooldown_hours = self.pause_hours * (2 ** max(0, block_count - 1))  # exponential
        cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours))

        new_status = "paused"
        if block_count >= self.max_blocks:
            new_status = "disabled"
            logger.error("Account %s disabled after %d blocks", account_username, block_count)

        await self.db.update_account(
            account_username,
            status=new_status,
            block_count=block_count,
            cooldown_until=cooldown_until,
        )

    async def is_account_available(self, account_username: str) -> bool:
        """Check if an account is healthy and not in cooldown."""
        return await self.db.is_account_available(account_username)

    async def handle_challenge(self, account_username: str, challenge_message: str) -> None:
        """Handle a checkpoint/challenge: pause and alert."""
        await self.db.update_account(account_username, status="paused")
        logger.warning("Account %s hit challenge: %s", account_username, challenge_message)
        await self.send_alert(
            f"Account @{account_username} requires verification\n"
            f"Challenge: {challenge_message}\n"
            f"Manual intervention needed."
        )

    async def is_account_in_cooldown(self, account_username: str) -> bool:
        """Check if an account is currently in cooldown."""
        account = await self.db.get_account(account_username)
        if not account:
            return True
        cooldown = account.get("cooldown_until")
        if not cooldown:
            return False
        try:
            cooldown_dt = datetime.fromisoformat(cooldown)
            if cooldown_dt.tzinfo is None:
                cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < cooldown_dt
        except (ValueError, TypeError):
            return False

    async def is_account_available(self, account_username: str) -> bool:
        """Check if account is active and not in cooldown."""
        account = await self.db.get_account(account_username)
        if not account:
            return False
        if account.get("status") in ("disabled", "paused"):
            if await self.is_account_in_cooldown(account_username):
                return False
            # Cooldown expired — reactivate
            if account.get("status") == "paused":
                await self.db.update_account(account_username, status="active")
                logger.info("Account %s cooldown expired, reactivated", account_username)
                return True
            return False
        return account.get("status") in ("active", "warming", "new")

    async def send_alert(self, message: str) -> None:
        """Send an alert via webhook (Discord/Telegram compatible)."""
        if not self.webhook_url:
            logger.info("Alert (no webhook): %s", message)
            return

        try:
            payload: dict
            if "discord" in self.webhook_url.lower():
                payload = {"content": message}
            else:
                # Telegram-style
                payload = {"text": message}

            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status < 300:
                        logger.info("Alert sent successfully")
                    else:
                        logger.warning("Alert webhook returned %d", resp.status)
        except Exception as e:
            logger.error("Failed to send alert: %s", e)

    async def get_account_health_summary(self) -> list[dict]:
        """Get health summary for all accounts."""
        accounts = await self.db.get_all_accounts()
        summaries = []
        for acct in accounts:
            in_cooldown = await self.is_account_in_cooldown(acct["username"])
            summaries.append({
                "username": acct["username"],
                "status": acct["status"],
                "block_count": acct.get("block_count", 0),
                "in_cooldown": in_cooldown,
                "cooldown_until": acct.get("cooldown_until", ""),
            })
        return summaries


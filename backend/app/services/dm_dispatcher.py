"""DM dispatcher: sends direct messages with gradual scaling and safety checks."""

import asyncio
import random
import logging
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import Page

from .session_manager import SessionManager
from .rate_limiter import RateLimiter
from .message_templates import MessageTemplateEngine
from .monitor import Monitor

logger = logging.getLogger("ig-automator.dm")


class DMDispatcher:
    """Core DM sending engine with gradual scaling, delays, and block detection."""

    def __init__(self, config: dict, db: any, session_mgr: SessionManager,
                 rate_limiter: RateLimiter, template_engine: MessageTemplateEngine,
                 monitor: Monitor) -> None:
        dm_cfg = config.get("dm", {})
        self.min_delay: int = dm_cfg.get("min_delay_seconds", 120)
        self.max_delay: int = dm_cfg.get("max_delay_seconds", 420)
        self.scaling_start: int = dm_cfg.get("scaling_start", 20)
        self.scaling_increment: int = dm_cfg.get("scaling_increment", 10)
        self.scaling_days: int = dm_cfg.get("scaling_days", 4)

        self.db = db
        self.session_mgr = session_mgr
        self.rate_limiter = rate_limiter
        self.template_engine = template_engine
        self.monitor = monitor

    async def send_dm(self, account_username: str, target: dict,
                      campaign_id: int, niche: str = "",
                      template_override: str = "") -> bool:
        """Send a single DM to a target user.

        Returns True on success, False on failure/skip.
        """
        target_username = target.get("username", "")
        if not target_username:
            return False

        # Check if already messaged
        if await self.db.is_target_messaged(target_username, account_username):
            logger.info("Skipping %s (already messaged by %s)", target_username, account_username)
            await self.db.update_target_status(target_username, "skipped")
            return False

        # Check rate limits
        if not await self.rate_limiter.can_send(account_username):
            logger.info("Rate limit reached for %s, skipping", account_username)
            return False

        # Check account health
        if not await self.monitor.is_account_available(account_username):
            logger.info("Account %s not available", account_username)
            return False

        page = self.session_mgr.get_page(account_username)
        if not page:
            logger.error("No page for %s", account_username)
            return False

        # Generate personalized message
        message = await self.template_engine.generate_message(
            account_username, target, niche=niche, template_override=template_override
        )

        try:
            # Navigate to target's profile
            await page.goto(f"https://www.instagram.com/{target_username}/",
                           wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(random.randint(2000, 5000))

            # Check for blocks
            block_check = await self.monitor.check_page_for_blocks(page)
            if block_check["blocked"]:
                await self.monitor.handle_block(account_username, block_check["message"])
                await self.db.log_message(campaign_id, account_username, target_username,
                                           message, status="blocked", error=block_check["message"])
                return False

            # Check if profile exists / is accessible
            page_text = await page.inner_text("body")
            if "Sorry, this page isn't available" in page_text:
                logger.info("Profile %s not found, skipping", target_username)
                await self.db.update_target_status(target_username, "skipped")
                return False

            # Click "Message" button
            message_btn = page.locator('div[role="button"]:has-text("Message"), button:has-text("Message")')
            if await message_btn.count() == 0:
                logger.info("No message button for %s (may be private), skipping", target_username)
                await self.db.update_target_status(target_username, "skipped")
                return False

            await message_btn.first.click()
            await page.wait_for_timeout(random.randint(2000, 4000))

            # Check for blocks again on DM page
            block_check = await self.monitor.check_page_for_blocks(page)
            if block_check["blocked"]:
                await self.monitor.handle_block(account_username, block_check["message"])
                await self.db.log_message(campaign_id, account_username, target_username,
                                           message, status="blocked", error=block_check["message"])
                return False

            # Type message in the text area
            textarea = page.locator('textarea[placeholder*="Message"], div[role="textbox"][contenteditable="true"]')
            if await textarea.count() == 0:
                logger.warning("Message textarea not found for %s", target_username)
                await self.db.log_message(campaign_id, account_username, target_username,
                                           message, status="failed", error="textarea not found")
                return False

            # Type with human-like delays
            await textarea.first.click()
            await page.wait_for_timeout(random.randint(500, 1500))

            for char in message:
                await textarea.first.type(char, delay=random.randint(30, 100))
                if random.random() < 0.05:  # 5% chance of a micro-pause
                    await page.wait_for_timeout(random.randint(200, 800))

            await page.wait_for_timeout(random.randint(500, 2000))

            # Send the message
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(random.randint(2000, 4000))

            # Verify no block after sending
            block_check = await self.monitor.check_page_for_blocks(page)
            if block_check["blocked"]:
                await self.monitor.handle_block(account_username, block_check["message"])
                await self.db.log_message(campaign_id, account_username, target_username,
                                           message, status="blocked", error=block_check["message"])
                return False

            # Log success
            await self.db.log_message(campaign_id, account_username, target_username, message, status="sent")
            await self.db.update_target_status(target_username, "messaged")
            await self.rate_limiter.record_send(account_username)

            logger.info("DM sent from %s to %s", account_username, target_username)
            return True

        except Exception as e:
            logger.error("DM send error (%s → %s): %s", account_username, target_username, e)
            await self.db.log_message(campaign_id, account_username, target_username,
                                       message, status="failed", error=str(e))
            return False

    async def send_batch(self, account_username: str, targets: list[dict],
                         campaign_id: int, niche: str = "",
                         template_override: str = "") -> dict:
        """Send DMs to a batch of targets with delays between each.

        Returns stats dict: {"sent": n, "failed": n, "skipped": n}
        """
        stats = {"sent": 0, "failed": 0, "skipped": 0}

        # Get account's campaign day for scaling
        account = await self.db.get_account(account_username)
        if not account:
            return stats
        campaign_day = account.get("campaign_day", 1) or 1

        allowance = await self.rate_limiter.get_daily_allowance(
            account_username, campaign_day,
            self.scaling_start, self.scaling_increment, self.scaling_days
        )

        logger.info("Batch send for %s: day %d, allowance %d, targets %d",
                    account_username, campaign_day, allowance, len(targets))

        for i, target in enumerate(targets):
            if allowance <= 0:
                logger.info("Daily allowance exhausted for %s", account_username)
                stats["skipped"] += len(targets) - i
                break

            if not await self.rate_limiter.can_send(account_username):
                # Wait for hourly limit reset
                wait_secs = await self.rate_limiter.wait_seconds_until_can_send(account_username)
                if wait_secs > 0:
                    logger.info("Hourly limit for %s, waiting %ds", account_username, wait_secs)
                    await asyncio.sleep(wait_secs)

            success = await self.send_dm(account_username, target, campaign_id,
                                          niche=niche, template_override=template_override)
            if success:
                stats["sent"] += 1
                allowance -= 1
            else:
                # Check if account got blocked
                if not await self.monitor.is_account_available(account_username):
                    logger.warning("Account %s no longer available, stopping batch", account_username)
                    stats["skipped"] += len(targets) - i - 1
                    break
                stats["failed"] += 1

            # Delay between DMs
            if i < len(targets) - 1:
                delay = random.randint(self.min_delay, self.max_delay)
                logger.debug("Waiting %ds before next DM", delay)
                await asyncio.sleep(delay)

        return stats

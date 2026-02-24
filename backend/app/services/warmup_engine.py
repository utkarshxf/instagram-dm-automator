"""Warmup engine for human-like account warming before DM campaigns."""

import asyncio
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from playwright.async_api import Page

from .session_manager import SessionManager
from .monitor import Monitor

logger = logging.getLogger("ig-automator.warmup")

# Reusable nav helper — avoids repeating the same goto args everywhere
_NAV_OPTS = {"wait_until": "domcontentloaded", "timeout": 60000}


class WarmupEngine:
    """Simulates human behavior to warm up Instagram accounts before DM campaigns."""

    def __init__(self, config: dict, db: any, session_mgr: SessionManager, monitor: Monitor) -> None:
        warmup_cfg = config.get("warmup", {})
        schedule_cfg = config.get("schedule", {})

        self.duration_hours: int = warmup_cfg.get("duration_hours", 48)
        self.actions_per_session: int = warmup_cfg.get("actions_per_session", 15)
        self.session_gap_minutes: int = warmup_cfg.get("session_gap_minutes", 120)
        self.active_start: int = schedule_cfg.get("active_hours_start", 8)
        self.active_end: int = schedule_cfg.get("active_hours_end", 23)

        self.db = db
        self.session_mgr = session_mgr
        self.monitor = monitor

    def _is_active_hours(self) -> bool:
        """Check if current time is within active hours."""
        now = datetime.now()
        return self.active_start <= now.hour < self.active_end

    async def warmup_account(self, username: str) -> None:
        """Run the full warmup cycle for an account.

        This is a long-running coroutine that spreads actions across the warmup duration.
        """
        account = await self.db.get_account(username)
        if not account:
            logger.error("Account %s not found", username)
            return

        # Mark warmup started
        if not account.get("warmup_started_at"):
            await self.db.update_account(username, status="warming",
                                          warmup_started_at=datetime.now(timezone.utc).isoformat())

        total_hours_done = account.get("total_warmup_hours", 0) or 0
        logger.info("Starting warmup for %s (%.1fh done, target: %dh)", username, total_hours_done, self.duration_hours)

        session_start = datetime.now(timezone.utc)

        while total_hours_done < self.duration_hours:
            if not self._is_active_hours():
                logger.info("Outside active hours for %s, sleeping 30min", username)
                await asyncio.sleep(30 * 60)
                continue

            # Run a warmup session
            actions_done = await self._run_session(username)
            if actions_done < 0:
                # Error or block detected
                logger.warning("Warmup session aborted for %s", username)
                await asyncio.sleep(self.session_gap_minutes * 60)
                continue

            # Update progress
            elapsed = (datetime.now(timezone.utc) - session_start).total_seconds() / 3600
            total_hours_done = (account.get("total_warmup_hours", 0) or 0) + elapsed
            total_actions = (account.get("warmup_actions_count", 0) or 0) + actions_done

            await self.db.update_account(username,
                                          total_warmup_hours=round(total_hours_done, 2),
                                          warmup_actions_count=total_actions)

            logger.info("Warmup progress %s: %.1fh/%dh, %d actions",
                        username, total_hours_done, self.duration_hours, total_actions)

            if total_hours_done >= self.duration_hours:
                break

            # Wait between sessions
            gap = random.randint(
                int(self.session_gap_minutes * 0.8),
                int(self.session_gap_minutes * 1.2),
            )
            logger.info("Warmup session complete for %s, next in %d min", username, gap)
            await asyncio.sleep(gap * 60)

            # Refresh account data
            account = await self.db.get_account(username)
            if not account or account.get("status") in ("paused", "disabled"):
                logger.info("Account %s paused/disabled, stopping warmup", username)
                return
            total_hours_done = account.get("total_warmup_hours", 0) or 0
            session_start = datetime.now(timezone.utc)

        # Mark warmup complete
        await self.db.update_account(username,
                                      status="active",
                                      warmup_completed_at=datetime.now(timezone.utc).isoformat())
        logger.info("Warmup complete for %s!", username)

    async def _run_session(self, username: str) -> int:
        """Run a single warmup session (a batch of human-like actions).

        Returns the number of actions performed, or -1 on error.
        """
        page = self.session_mgr.get_page(username)
        if not page:
            logger.error("No page for %s", username)
            return -1

        actions = [
            self._scroll_feed,
            self._view_stories,
            self._like_posts,
            self._view_reels,
            self._follow_suggested,
        ]

        actions_done = 0
        random.shuffle(actions)

        for action_fn in actions[:self.actions_per_session]:
            try:
                # Check for blocks before each action
                block_check = await self.monitor.check_page_for_blocks(page)
                if block_check["blocked"]:
                    await self.monitor.handle_block(username, block_check["message"])
                    return -1
                if block_check["challenge"]:
                    await self.monitor.handle_challenge(username, block_check["message"])
                    return -1

                success = await action_fn(page, username)
                if success:
                    actions_done += 1

                # Human-like delay between actions
                delay = random.randint(60, 300)
                await asyncio.sleep(delay)

            except Exception as e:
                logger.warning("Warmup action error for %s: %s", username, e)
                await asyncio.sleep(30)

        return actions_done

    async def _scroll_feed(self, page: Page, username: str) -> bool:
        """Scroll the Instagram feed naturally."""
        try:
            await page.goto("https://www.instagram.com/", **_NAV_OPTS)
            await page.wait_for_timeout(random.randint(2000, 5000))

            for _ in range(random.randint(3, 8)):
                await page.evaluate("window.scrollBy(0, window.innerHeight * (0.6 + Math.random() * 0.4))")
                await page.wait_for_timeout(random.randint(1500, 4000))

            await self.db.log_warmup_action(username, "scroll_feed", "Scrolled feed")
            logger.debug("Scrolled feed for %s", username)
            return True
        except Exception as e:
            logger.warning("scroll_feed error for %s: %s", username, e)
            return False

    async def _view_stories(self, page: Page, username: str) -> bool:
        """View stories on the feed."""
        try:
            await page.goto("https://www.instagram.com/", **_NAV_OPTS)
            await page.wait_for_timeout(random.randint(2000, 4000))

            story_buttons = page.locator('div[role="button"] canvas, button[aria-label*="Story"]')
            count = await story_buttons.count()
            if count > 0:
                idx = random.randint(0, min(count - 1, 3))
                await story_buttons.nth(idx).click()
                await page.wait_for_timeout(random.randint(3000, 8000))

                for _ in range(random.randint(1, 4)):
                    await page.wait_for_timeout(random.randint(4000, 10000))
                    try:
                        await page.keyboard.press("ArrowRight")
                    except Exception:
                        break

                try:
                    close_btn = page.locator('button[aria-label="Close"], svg[aria-label="Close"]')
                    if await close_btn.count() > 0:
                        await close_btn.first.click()
                except Exception:
                    await page.goto("https://www.instagram.com/", **_NAV_OPTS)

                await self.db.log_warmup_action(username, "view_stories", "Watched stories")
                logger.debug("Viewed stories for %s", username)
                return True

            return False
        except Exception as e:
            logger.warning("view_stories error for %s: %s", username, e)
            return False

    async def _like_posts(self, page: Page, username: str) -> bool:
        """Like a post on the feed."""
        try:
            await page.goto("https://www.instagram.com/", **_NAV_OPTS)
            await page.wait_for_timeout(random.randint(2000, 5000))

            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(random.randint(1000, 3000))

            like_buttons = page.locator('svg[aria-label="Like"][width="24"], span[class*="like"] button')
            count = await like_buttons.count()
            if count > 0:
                idx = random.randint(0, min(count - 1, 2))
                await like_buttons.nth(idx).click()
                await page.wait_for_timeout(random.randint(1000, 3000))
                await self.db.log_warmup_action(username, "like_post", "Liked a post")
                logger.debug("Liked a post for %s", username)
                return True

            # Fallback: double-tap an article to like
            articles = page.locator("article")
            if await articles.count() > 0:
                await articles.first.dblclick()
                await page.wait_for_timeout(random.randint(1000, 3000))
                await self.db.log_warmup_action(username, "like_post", "Double-tap liked")
                return True

            return False
        except Exception as e:
            logger.warning("like_posts error for %s: %s", username, e)
            return False

    async def _view_reels(self, page: Page, username: str) -> bool:
        """Browse Instagram Reels."""
        try:
            await page.goto("https://www.instagram.com/reels/", **_NAV_OPTS)
            await page.wait_for_timeout(random.randint(3000, 7000))

            for _ in range(random.randint(2, 5)):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(random.randint(5000, 15000))

            await self.db.log_warmup_action(username, "view_reels", "Watched reels")
            logger.debug("Viewed reels for %s", username)
            return True
        except Exception as e:
            logger.warning("view_reels error for %s: %s", username, e)
            return False

    async def _follow_suggested(self, page: Page, username: str) -> bool:
        """Follow a suggested account (sparingly)."""
        try:
            await page.goto("https://www.instagram.com/explore/people/", **_NAV_OPTS)
            await page.wait_for_timeout(random.randint(2000, 5000))

            follow_buttons = page.locator('button:has-text("Follow")')
            count = await follow_buttons.count()
            if count > 0 and random.random() < 0.3:  # Only 30% chance to actually follow
                idx = random.randint(0, min(count - 1, 4))
                await follow_buttons.nth(idx).click()
                await page.wait_for_timeout(random.randint(2000, 5000))
                await self.db.log_warmup_action(username, "follow_suggested", "Followed a suggested user")
                logger.debug("Followed suggested for %s", username)
                return True

            return False
        except Exception as e:
            logger.warning("follow_suggested error for %s: %s", username, e)
            return False

    async def is_warmup_complete(self, username: str) -> bool:
        """Check if an account has completed warmup."""
        account = await self.db.get_account(username)
        if not account:
            return False
        if account.get("warmup_completed_at"):
            return True
        return (account.get("total_warmup_hours") or 0) >= self.duration_hours
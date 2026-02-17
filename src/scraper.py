"""Target scraper: extracts followers/following from Instagram accounts using a separate session."""

import asyncio
import csv
import json
import logging
import os
import random
from typing import Optional

from playwright.async_api import Page, BrowserContext

from .db import Database
from .session_manager import SessionManager

logger = logging.getLogger("ig-automator.scraper")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Scraper:
    """Scrapes Instagram followers/following for lead generation.

    IMPORTANT: Always runs on a SEPARATE browser session with a different
    proxy than sender accounts to avoid association.
    """

    def __init__(self, config: dict, db: Database) -> None:
        scrape_cfg = config.get("scraping", {})
        self.proxy: str = scrape_cfg.get("proxy", "")
        self.targets_per_run: int = scrape_cfg.get("targets_per_run", 500)
        self.filters = scrape_cfg.get("filters", {})
        self.min_followers: int = self.filters.get("min_followers", 100)
        self.max_followers: int = self.filters.get("max_followers", 50000)
        self.require_bio: bool = self.filters.get("has_bio", True)
        self.exclude_private: bool = not self.filters.get("is_private", False)
        self.db = db
        self._session_mgr: Optional[SessionManager] = None

    async def _get_session(self) -> SessionManager:
        """Create a dedicated scraper session (separate from sender accounts)."""
        if self._session_mgr is None:
            self._session_mgr = SessionManager()
            await self._session_mgr.start()
        return self._session_mgr

    async def close(self) -> None:
        """Close the scraper session."""
        if self._session_mgr:
            await self._session_mgr.stop()
            self._session_mgr = None

    async def scrape_followers(self, target_account: str, campaign_id: int = 0,
                                max_count: int = 0) -> list[dict]:
        """Scrape followers of a target Instagram account.

        Uses the Instagram web interface to scroll through the followers dialog.
        Returns a list of user dicts.
        """
        if max_count <= 0:
            max_count = self.targets_per_run

        session = await self._get_session()
        scraper_username = f"_scraper_{target_account}"

        # Create scraper context with dedicated proxy
        proxy_dict = None
        if self.proxy:
            from urllib.parse import urlparse
            parsed = urlparse(self.proxy)
            proxy_dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
            if parsed.username:
                proxy_dict["username"] = parsed.username
            if parsed.password:
                proxy_dict["password"] = parsed.password

        await session.create_context(scraper_username, proxy=proxy_dict)
        page = session.get_page(scraper_username)
        if not page:
            logger.error("Failed to create scraper page")
            return []

        users: list[dict] = []

        try:
            # Navigate to target profile
            await page.goto(f"https://www.instagram.com/{target_account}/",
                           wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(random.randint(2000, 5000))

            # Click followers count link
            followers_link = page.locator(f'a[href="/{target_account}/followers/"]')
            if await followers_link.count() == 0:
                # Try alternate selector
                followers_link = page.locator('a:has-text("followers")')

            if await followers_link.count() == 0:
                logger.warning("Could not find followers link for %s", target_account)
                return users

            await followers_link.first.click()
            await page.wait_for_timeout(random.randint(3000, 5000))

            # Scroll the followers dialog to load more
            dialog = page.locator('div[role="dialog"]')
            if await dialog.count() == 0:
                logger.warning("Followers dialog not found for %s", target_account)
                return users

            scrollable = dialog.locator('div[style*="overflow"]').first

            seen_usernames: set = set()
            stale_count = 0

            while len(users) < max_count and stale_count < 5:
                # Extract visible user items
                user_items = dialog.locator('a[role="link"][href^="/"]')
                count = await user_items.count()
                new_found = 0

                for i in range(count):
                    try:
                        href = await user_items.nth(i).get_attribute("href")
                        if not href or href in ("/", ""):
                            continue
                        uname = href.strip("/").split("/")[0]
                        if uname in seen_usernames or uname == target_account:
                            continue
                        if uname in ("explore", "reels", "accounts", "p", "stories"):
                            continue

                        seen_usernames.add(uname)
                        new_found += 1

                        # Try to get display name from adjacent text
                        parent = user_items.nth(i).locator("..")
                        full_name = ""
                        try:
                            spans = parent.locator("span")
                            if await spans.count() > 0:
                                full_name = await spans.first.inner_text()
                        except Exception:
                            pass

                        users.append({
                            "username": uname,
                            "full_name": full_name,
                            "bio": "",
                            "follower_count": 0,
                            "following_count": 0,
                            "is_private": False,
                            "source_account": target_account,
                            "campaign_id": campaign_id,
                        })

                        if len(users) >= max_count:
                            break

                    except Exception:
                        continue

                if new_found == 0:
                    stale_count += 1
                else:
                    stale_count = 0

                # Scroll down in dialog
                try:
                    await scrollable.evaluate("el => el.scrollTop = el.scrollHeight")
                except Exception:
                    await page.evaluate("""
                        const dialog = document.querySelector('div[role="dialog"]');
                        if (dialog) {
                            const scrollable = dialog.querySelector('div[style*="overflow"]');
                            if (scrollable) scrollable.scrollTop = scrollable.scrollHeight;
                        }
                    """)
                await page.wait_for_timeout(random.randint(2000, 4000))

            logger.info("Scraped %d followers from %s", len(users), target_account)

        except Exception as e:
            logger.error("Scraping error for %s: %s", target_account, e)
        finally:
            await session.close_context(scraper_username)

        return users

    async def enrich_profile(self, page: Page, username: str) -> dict:
        """Visit a profile and extract detailed info."""
        info: dict = {"username": username, "full_name": "", "bio": "",
                      "follower_count": 0, "following_count": 0, "is_private": False}
        try:
            await page.goto(f"https://www.instagram.com/{username}/",
                           wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(random.randint(1000, 3000))

            # Extract meta info from page
            try:
                meta = await page.locator('meta[property="og:description"]').get_attribute("content")
                if meta:
                    # Format: "X Followers, Y Following, Z Posts - BIO"
                    parts = meta.split(" - ", 1)
                    if len(parts) > 1:
                        info["bio"] = parts[1].strip()
                    stats_part = parts[0]
                    import re
                    nums = re.findall(r"([\d,.]+[KkMm]?)\s+(Followers|Following|Posts)", stats_part)
                    for val, label in nums:
                        parsed = self._parse_count(val)
                        if "Follower" in label:
                            info["follower_count"] = parsed
                        elif "Following" in label:
                            info["following_count"] = parsed
            except Exception:
                pass

            # Check privacy
            try:
                body_text = await page.inner_text("body")
                if "This account is private" in body_text or "This Account is Private" in body_text:
                    info["is_private"] = True
            except Exception:
                pass

            # Extract display name
            try:
                header = page.locator("header")
                name_span = header.locator("span").first
                if await name_span.count() > 0:
                    info["full_name"] = await name_span.inner_text()
            except Exception:
                pass

        except Exception as e:
            logger.warning("Profile enrichment failed for %s: %s", username, e)

        return info

    @staticmethod
    def _parse_count(text: str) -> int:
        """Parse follower count text like '1.2K', '15M', '1,234'."""
        text = text.strip().replace(",", "")
        multiplier = 1
        if text.lower().endswith("k"):
            multiplier = 1000
            text = text[:-1]
        elif text.lower().endswith("m"):
            multiplier = 1_000_000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

    def filter_target(self, target: dict) -> bool:
        """Check if a target passes the configured filters."""
        fc = target.get("follower_count", 0)
        if fc and (fc < self.min_followers or fc > self.max_followers):
            return False
        if self.require_bio and not target.get("bio", "").strip() and not target.get("has_bio"):
            return False
        if self.exclude_private and target.get("is_private"):
            return False
        return True

    async def scrape_and_save(self, target_account: str, campaign_id: int = 0) -> int:
        """Scrape followers and save filtered results to the database.

        Returns the number of targets saved.
        """
        raw_users = await self.scrape_followers(target_account, campaign_id)
        filtered = [u for u in raw_users if self.filter_target(u)]
        count = await self.db.add_targets_bulk(filtered)
        logger.info("Saved %d filtered targets from %s (raw: %d)", count, target_account, len(raw_users))
        return count

    async def export_to_csv(self, filepath: str, campaign_id: int = 0) -> int:
        """Export targets to a CSV file."""
        targets = await self.db.get_all_targets(campaign_id)
        if not targets:
            logger.info("No targets to export")
            return 0

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
        fieldnames = ["username", "full_name", "bio", "follower_count", "following_count",
                       "is_private", "status", "source_account", "created_at"]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for t in targets:
                writer.writerow(t)

        logger.info("Exported %d targets to %s", len(targets), filepath)
        return len(targets)

    async def import_from_csv(self, filepath: str, campaign_id: int = 0) -> int:
        """Import targets from a CSV file."""
        if not os.path.exists(filepath):
            logger.error("CSV file not found: %s", filepath)
            return 0

        targets: list[dict] = []
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                targets.append({
                    "username": row.get("username", ""),
                    "full_name": row.get("full_name", ""),
                    "bio": row.get("bio", ""),
                    "follower_count": int(row.get("follower_count", 0) or 0),
                    "following_count": int(row.get("following_count", 0) or 0),
                    "is_private": row.get("is_private", "").lower() in ("true", "1", "yes"),
                    "source_account": row.get("source_account", "csv_import"),
                    "campaign_id": campaign_id,
                })

        count = await self.db.add_targets_bulk(targets)
        logger.info("Imported %d targets from %s", count, filepath)
        return count

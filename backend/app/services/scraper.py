"""Target scraper: extracts followers/following from Instagram accounts using a separate session."""

import asyncio
import csv
import json
import logging
import os
import random
import re
from typing import Optional

from playwright.async_api import Page, BrowserContext

from .session_manager import SessionManager

logger = logging.getLogger("ig-automator.scraper")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Scraper:
    """Scrapes Instagram followers/following for lead generation.

    IMPORTANT: Always runs on a SEPARATE browser session with a different
    proxy than sender accounts to avoid association.
    """

    def __init__(self, config: dict, db: any) -> None:
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
        """Scrape followers of a target Instagram account."""
        if max_count <= 0:
            max_count = self.targets_per_run

        session = await self._get_session()
        scraper_username = f"_scraper_{target_account}"

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
            await page.goto(f"https://www.instagram.com/{target_account}/",
                           wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(2000, 5000))

            followers_link = page.locator(f'a[href="/{target_account}/followers/"]')
            if await followers_link.count() == 0:
                followers_link = page.locator('a:has-text("followers")')

            if await followers_link.count() == 0:
                logger.warning("Could not find followers link for %s", target_account)
                return users

            await followers_link.first.click()
            await page.wait_for_timeout(random.randint(3000, 5000))

            try:
                await page.locator('div[role="dialog"]').wait_for(state="visible", timeout=10000)
            except Exception:
                logger.warning("Followers dialog not found for %s", target_account)
                return users

            dialog = page.locator('div[role="dialog"]')
            seen_usernames: set = set()
            stale_count = 0

            while len(users) < max_count and stale_count < 8:
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
                        if uname in ("explore", "reels", "accounts", "p", "stories", "direct"):
                            continue

                        seen_usernames.add(uname)
                        new_found += 1

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

                await self._scroll_dialog(page)
                wait_ms = random.randint(2500, 4000) if new_found == 0 else random.randint(1500, 3000)
                await page.wait_for_timeout(wait_ms)

            logger.info("Scraped %d followers from %s", len(users), target_account)

        except Exception as e:
            logger.error("Scraping error for %s: %s", target_account, e)
        finally:
            await session.close_context(scraper_username)

        return users

    async def enrich_profile(self, page: Page, username: str) -> dict:
        """Visit a profile and extract detailed info.

        FIX: follower_count was stored under wrong key 'followers' — now correctly
             stored as 'follower_count' so lead_crawler.py can read it.
        FIX: bio was picking up the username/stats line from og:description.
             Now correctly parses only the text AFTER ' - ' in the meta tag,
             and skips the generic 'See Instagram photos...' fallback.
        """
        info: dict = {
            "username": username,
            "full_name": "",
            "bio": "",
            "follower_count": 0,
            "following_count": 0,
            "is_private": False,
            "external_url": None,
        }
        try:
            await page.goto(f"https://www.instagram.com/{username}/",
                           wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(random.randint(1000, 3000))

            # ── Meta tag extraction ──────────────────────────────────────────
            # Instagram og:description format:
            #   "106 Followers, 45 Following, 98 Posts - Actual bio text here"
            #   "106 Followers, 45 Following, 98 Posts - See Instagram photos..."
            try:
                meta = await page.locator('meta[property="og:description"]').get_attribute("content")
                if meta:
                    parts = meta.split(" - ", 1)
                    stats_part = parts[0]

                    # FIX: parse counts and store under correct keys
                    nums = re.findall(r"([\d,.]+[KkMm]?)\s+(Followers?|Following|Posts?)", stats_part)
                    for val, label in nums:
                        parsed = self._parse_count(val)
                        if "Follower" in label:
                            info["follower_count"] = parsed   # was "followers" before — wrong key
                        elif "Following" in label:
                            info["following_count"] = parsed

                    # FIX: bio is only the part after ' - ', and only if it's not the generic fallback
                    if len(parts) > 1:
                        meta_bio = parts[1].strip()
                        if not meta_bio.lower().startswith("see instagram photos"):
                            info["bio"] = meta_bio
            except Exception as e:
                logger.debug(f"Meta extraction failed for {username}: {e}")

            # ── DOM extraction ───────────────────────────────────────────────
            try:
                header = page.locator("header")

                # Full name
                if not info["full_name"]:
                    for sel in ["h2", "h1", "span._aa_c", "span"]:
                        el = header.locator(sel).first
                        if await el.count() > 0:
                            text = (await el.inner_text()).strip()
                            # Skip if it's just the username or looks like a stat line
                            if text and text != username and not re.search(
                                r"\d+\s+(post|follow)", text, re.I
                            ):
                                info["full_name"] = text
                                break

                # External URL
                try:
                    link_el = header.locator(
                        'a[rel~="me"], a[rel="nofollow noopener noreferrer"]'
                    ).first
                    if await link_el.count() > 0:
                        info["external_url"] = await link_el.get_attribute("href")
                except Exception:
                    pass

                # Refined follower count from DOM if meta gave 0
                if info["follower_count"] == 0:
                    for sel in [
                        f'a[href="/{username}/followers/"]',
                        'a:has-text("followers")',
                    ]:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            text = (await el.inner_text()).strip()
                            num_text = text.split()[0]
                            info["follower_count"] = self._parse_count(num_text)
                            if info["follower_count"] > 0:
                                break

                # Private account check
                try:
                    private_el = page.locator(
                        'h2:has-text("This Account is Private"), '
                        'div:has-text("This account is private")'
                    ).first
                    if await private_el.count() > 0:
                        info["is_private"] = True
                except Exception:
                    pass

            except Exception as e:
                logger.debug(f"DOM extraction failed for {username}: {e}")

        except Exception as e:
            logger.warning("Profile enrichment failed for %s: %s", username, e)

        logger.debug(
            f"Enriched {username}: followers={info['follower_count']}, "
            f"bio='{info['bio'][:60]}', full_name='{info['full_name']}'"
        )
        return info

    async def scrape_following(self, page: Page, target_account: str, max_count: int = 100) -> list[str]:
        """Scrape 'following' list from a page already at the target profile.

        FIX: Replaced PageDown keypresses (which scroll the background page, not
             the dialog) with JS-based scroll on the dialog's inner overflow div.
        FIX: max_stale raised from 3 to 8 so slow-loading dialogs don't bail early.
        FIX: Uses wait_for() on the dialog to avoid the 'dialog not found' false-negative
             that happened when click hadn't yet rendered the modal.
        """
        usernames: list[str] = []
        dialog = None
        try:
            # Find and click the following link
            following_link = page.locator(f'a[href="/{target_account}/following/"]')
            if await following_link.count() == 0:
                following_link = page.locator('a').filter(
                    has_text=re.compile(r"^\d[\d,\.KkMm]*\s*following$", re.I)
                )
            if await following_link.count() == 0:
                following_link = page.locator('a:has-text("following")')

            if await following_link.count() == 0:
                logger.warning("Could not find following link for %s", target_account)
                return usernames

            await following_link.first.click()

            # FIX: Wait for dialog to actually appear (up to 10s) instead of
            # just checking count immediately after click.
            try:
                await page.locator('div[role="dialog"]').wait_for(state="visible", timeout=10000)
            except Exception:
                logger.warning("Following dialog not found for %s", target_account)
                return usernames

            dialog = page.locator('div[role="dialog"]')

            # Give initial items time to render
            await page.wait_for_timeout(random.randint(2000, 3000))

            seen_usernames: set = set()
            stale_count = 0
            max_stale = 8  # FIX: was 3 — too aggressive for slow connections

            while len(usernames) < max_count and stale_count < max_stale:
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
                        if uname in ("explore", "reels", "accounts", "p", "stories", "direct"):
                            continue

                        seen_usernames.add(uname)
                        usernames.append(uname)
                        new_found += 1

                        if len(usernames) >= max_count:
                            break
                    except Exception:
                        continue

                if new_found == 0:
                    stale_count += 1
                else:
                    stale_count = 0

                # FIX: scroll the dialog's own overflow container, not the page
                await self._scroll_dialog(page)
                wait_ms = random.randint(2500, 3500) if new_found == 0 else random.randint(1000, 2000)
                await page.wait_for_timeout(wait_ms)

            logger.info("Scraped %d following from %s", len(usernames), target_account)

        except Exception as e:
            logger.error("Scraping following error for %s: %s", target_account, e)

        finally:
            # Always close the dialog so the page is clean for the next call
            if dialog is not None:
                try:
                    close_btn = dialog.locator('svg[aria-label="Close"]').first
                    if await close_btn.count() > 0:
                        await close_btn.click()
                    else:
                        await page.keyboard.press("Escape")
                except Exception:
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                await page.wait_for_timeout(500)

        return usernames

    async def _scroll_dialog(self, page: Page) -> None:
        """Scroll the followers/following dialog to load more items.

        Uses two JS strategies so it works regardless of Instagram's exact DOM
        structure at the time of the call.
        """
        try:
            scrolled = await page.evaluate("""
                () => {
                    const dialog = document.querySelector('div[role="dialog"]');
                    if (!dialog) return false;
                    // Find any scrollable overflow div tall enough to actually scroll
                    const candidates = Array.from(dialog.querySelectorAll('div'));
                    for (const el of candidates) {
                        const style = window.getComputedStyle(el);
                        const ov = style.overflow + style.overflowY;
                        if ((ov.includes('scroll') || ov.includes('auto'))
                                && el.scrollHeight > el.clientHeight + 10) {
                            el.scrollTop = el.scrollHeight;
                            return true;
                        }
                    }
                    return false;
                }
            """)
            if not scrolled:
                # Fallback: scroll the last visible user link into view
                await page.evaluate("""
                    () => {
                        const dialog = document.querySelector('div[role="dialog"]');
                        if (!dialog) return;
                        const links = dialog.querySelectorAll('a[role="link"]');
                        if (links.length > 0) links[links.length - 1].scrollIntoView();
                    }
                """)
        except Exception as e:
            logger.debug(f"Scroll failed: {e}")

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
        """Scrape followers and save filtered results to the database."""
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
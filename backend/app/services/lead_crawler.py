import asyncio
import logging
import random
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import json

from .session_manager import SessionManager
from .proxy_manager import ProxyManager
from .scraper import Scraper
from ..db.mongodb_adapter import MongoDBDatabase

logger = logging.getLogger("ig-automator.lead_crawler")

class LeadCrawler:
    def __init__(self, config: dict, db: MongoDBDatabase, redis_pool: Any, user_id: str):
        self.config = config
        self.db = db
        self.redis = redis_pool
        self.user_id = user_id
        self.session_manager = SessionManager(user_id=user_id)
        self.proxy_manager = ProxyManager(config, db)
        self.scraper = Scraper(config, db)
        self.is_running = False

    async def start(self):
        self.is_running = True
        await self.session_manager.start()

    async def stop(self):
        self.is_running = False
        await self.session_manager.stop()
        await self.scraper.close()

    def _get_queue_key(self, campaign_id: str) -> str:
        return f"lead_queue:{campaign_id}"

    async def push_to_queue(self, campaign_id: str, usernames: List[str]):
        if not usernames:
            return
        # Filter out already visited profiles before pushing to queue
        new_usernames = []
        for username in usernames:
            if not await self.db.is_profile_visited(campaign_id, username):
                new_usernames.append(username)
            else:
                logger.debug(f"Profile {username} already visited, skipping")

        if new_usernames:
            logger.info(f"Enqueuing {len(new_usernames)} profiles for campaign {campaign_id}")
            await self.redis.enqueue_job(
                "process_lead_batch",
                campaign_id=campaign_id,
                user_id=self.user_id,
                usernames=new_usernames,
            )
        else:
            logger.info(f"All {len(usernames)} profiles already visited for campaign {campaign_id}")

    async def run_extraction(self, campaign_id: str):
        campaign = await self.db.get_lead_campaign(campaign_id)
        if not campaign:
            logger.error(f"Lead campaign {campaign_id} not found")
            return

        if campaign["status"] == "stopped":
            return

        await self.db.update_lead_campaign(campaign_id, status="running")

        seed = campaign["seed_profile"]
        await self.push_to_queue(campaign_id, [seed])
        logger.info(f"Started extraction for campaign {campaign_id} with seed {seed}")

    async def process_profile(self, campaign_id: str, username: str, account_username: str):
        """Processes a single profile: enriches it, applies filters, then fetches following."""
        logger.info(f"Processing profile {username} for campaign {campaign_id} using {account_username}")

        if await self.db.is_profile_visited(campaign_id, username):
            logger.info(f"Profile {username} already visited during processing, skipping")
            return

        campaign = await self.db.get_lead_campaign(campaign_id)
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found during processing profile {username}")
            return

        if campaign["status"] in ["paused", "stopped"]:
            logger.info(f"Campaign {campaign_id} is {campaign['status']}, skipping profile {username}")
            return

        await self.db.mark_profile_visited(campaign_id, username)

        # Ensure account context is created and logged in
        ctx = self.session_manager.get_context(account_username)
        if not ctx:
            logger.info(f"Creating browser context for account {account_username}")
            account_data = await self.db.get_account(account_username)
            if not account_data:
                logger.error(f"Account {account_username} not found in DB")
                return

            proxy_url = account_data.get("proxy") or self.proxy_manager.get_scraping_proxy()
            proxy_dict = self.proxy_manager.parse_proxy_for_playwright(proxy_url) if proxy_url else None

            await self.session_manager.create_context(account_username, proxy=proxy_dict)
            if not await self.session_manager.is_logged_in(account_username):
                logger.info(f"Logging in account {account_username}")
                if "password" in account_data:
                    await self.session_manager.login(account_username, account_data["password"])
                else:
                    logger.error(f"Account {account_username} is not logged in and no password available")
                    return

        page = self.session_manager.get_page(account_username)
        if not page:
            logger.error(f"Could not get page for account {account_username}")
            return

        try:
            # Enrich the profile (navigates to it internally)
            logger.debug(f"Enriching profile for {username}")
            profile_data = await self.scraper.enrich_profile(page, username)
            if not profile_data:
                logger.warning(f"Could not fetch data for {username}")
                return

            # Apply campaign filters
            if self._matches_filters(profile_data, campaign["filters"]):
                await self.db.add_lead(campaign_id, {
                    "username": username,
                    "profile_url": f"https://www.instagram.com/{username}/",
                    # FIX: was profile_data.get("followers", 0) — wrong key.
                    # enrich_profile now returns "follower_count" consistently.
                    "follower_count": profile_data.get("follower_count", 0),
                    "bio": profile_data.get("bio", ""),
                    "full_name": profile_data.get("full_name", ""),
                    "is_private": profile_data.get("is_private", False),
                    "external_url": profile_data.get("external_url"),
                })
                logger.info(f"MATCH: Lead found: {username} in campaign {campaign_id}")
            else:
                logger.info(f"SKIP: Profile {username} does not match filters")

            # FIX: Re-navigate to the profile so the following link is present in the DOM.
            # enrich_profile may leave the page in an unknown scroll/dialog state.
            # This must complete (including push_to_queue) before the task exits.
            following = await self._get_following(page, username)
            if following:
                logger.info(f"Found {len(following)} following for {username}, pushing to queue")
                await self.push_to_queue(campaign_id, following)
            else:
                logger.debug(f"No following found for {username}")

        except Exception as e:
            logger.exception(f"Error processing profile {username}: {e}")

    def _matches_filters(self, profile: dict, filters: dict) -> bool:
        # FIX: read from correct key 'follower_count'
        followers = profile.get("follower_count", 0)

        if filters.get("min_followers") and followers < filters["min_followers"]:
            logger.debug(
                f"Filter fail: {profile['username']} has {followers} followers "
                f"(min {filters['min_followers']})"
            )
            return False
        if filters.get("max_followers") and followers > filters["max_followers"]:
            logger.debug(
                f"Filter fail: {profile['username']} has {followers} followers "
                f"(max {filters['max_followers']})"
            )
            return False

        # Bio keywords
        bio = profile.get("bio", "").lower()
        bio_keywords = filters.get("bio_keywords", [])
        if bio_keywords:
            if not any(kw.lower() in bio for kw in bio_keywords):
                logger.debug(
                    f"Filter fail: {profile['username']} bio doesn't contain any required keywords"
                )
                return False

        # Required bio links
        external_url = profile.get("external_url", "") or ""
        required_links = filters.get("required_bio_links", [])
        if required_links:
            if not external_url or not any(
                link.lower() in external_url.lower() for link in required_links
            ):
                logger.debug(
                    f"Filter fail: {profile['username']} bio link '{external_url}' "
                    f"doesn't match required"
                )
                return False

        return True

    async def _get_following(self, page: Any, username: str) -> List[str]:
        """Fetch the following list for a username.

        FIX: Always re-navigates to the profile page first so the following link
        is guaranteed to be in the DOM, regardless of what enrich_profile left
        the page on. This also prevents the race where the worker task finishes
        and closes the browser context before the dialog interaction completes.
        """
        logger.info(f"Fetching following for {username}...")
        try:
            # Navigate fresh to the profile so the following link is present
            await page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await page.wait_for_timeout(random.randint(1500, 3000))

            following = await self.scraper.scrape_following(page, username, max_count=100)
            logger.info(f"Collected {len(following)} following usernames for {username}")
            return following
        except Exception as e:
            logger.error(f"Error fetching following for {username}: {e}")
            return []
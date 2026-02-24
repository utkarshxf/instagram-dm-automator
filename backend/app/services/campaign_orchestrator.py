"""Campaign orchestrator: state machine that ties all modules together."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from .session_manager import SessionManager
from .warmup_engine import WarmupEngine
from .dm_dispatcher import DMDispatcher
from .scraper import Scraper
from .proxy_manager import ProxyManager
from .message_templates import MessageTemplateEngine
from .rate_limiter import RateLimiter
from .monitor import Monitor

logger = logging.getLogger("ig-automator.campaign")

# Campaign states
CREATED = "created"
QUEUED = "queued"
WARMING = "warming"
SCALING = "scaling"
ACTIVE = "active"
PAUSED = "paused"
COMPLETED = "completed"


class CampaignOrchestrator:
    """Orchestrates the full campaign lifecycle: warmup → scaling → active sending."""

    def __init__(self, config: dict, db: any, user_id: str = None) -> None:
        self.config = config
        self.db = db
        self.user_id = user_id
        self.session_mgr = SessionManager(user_id=user_id) if user_id else SessionManager()
        self.proxy_mgr = ProxyManager(config, db)
        self.monitor = Monitor(config, db)
        self.rate_limiter = RateLimiter(config, db)
        self.template_engine = MessageTemplateEngine(config, db)
        self.warmup_engine = WarmupEngine(config, db, self.session_mgr, self.monitor)
        self.dm_dispatcher = DMDispatcher(config, db, self.session_mgr,
                                          self.rate_limiter, self.template_engine, self.monitor)
        self.scraper = Scraper(config, db)

        schedule = config.get("schedule", {})
        self.active_start: int = schedule.get("active_hours_start", 8)
        self.active_end: int = schedule.get("active_hours_end", 23)
        self.days_off: list[str] = schedule.get("days_off", [])

    def _is_work_time(self) -> bool:
        """Check if we're within working hours and not on a day off."""
        now = datetime.now()
        day_name = now.strftime("%A")
        if day_name in self.days_off:
            return False
        return self.active_start <= now.hour < self.active_end

    async def start(self) -> None:
        """Initialize session manager and check proxies."""
        await self.session_mgr.start()
        await self.proxy_mgr.check_all_proxies()

    async def stop(self) -> None:
        """Shut down all sessions."""
        await self.scraper.close()
        await self.session_mgr.stop()

    async def setup_account(self, username: str, password: str, proxy: str = "") -> bool:
        """Set up an account: assign proxy, create browser context, login with fallback."""
        # Assign proxy
        proxy_dict = None
        if proxy:
            assigned = await self.proxy_mgr.assign_proxy_to_account(username, proxy)
            if assigned:
                proxy_dict = self.proxy_mgr.parse_proxy_for_playwright(assigned)

        # Create browser context
        await self.session_mgr.create_context(username, proxy=proxy_dict)

        # Check if already logged in (persistent session)
        if await self.session_mgr.is_logged_in(username):
            logger.info("Account %s already logged in via persistent session", username)
            return True

        # Fallback: Try 2FA login if secret_key is present
        acct = await self.db.get_account(username)
        secret_key = acct.get("secret_key") if acct else None
        if secret_key and password:
            login_success = await self.session_mgr.authenticate_with_secret(username, secret_key, password)
            if login_success:
                logger.info("Account %s logged in via 2FA fallback", username)
                return True
            else:
                logger.warning("2FA login failed for %s, trying password fallback", username)

        # Fallback: Try password login
        if password:
            login_success = await self.session_mgr.login(username, password)
            if login_success:
                logger.info("Account %s logged in via password fallback", username)
                return True
            else:
                logger.error("Password login failed for %s", username)
                return False
        logger.error("No password found for %s, cannot login", username)
        return False

    async def run_campaign(self, campaign_id: str) -> None:
        """Run a campaign through its full lifecycle.

        State machine: CREATED → WARMING → SCALING → ACTIVE → COMPLETED
        """
        campaign = await self.db.get_campaign(campaign_id)
        if not campaign:
            logger.error("Campaign %s not found", campaign_id)
            return

        accounts = campaign.get("accounts", [])
        if not accounts:
            logger.error("Campaign %s has no accounts", campaign_id)
            return

        status = campaign["status"]
        logger.info("Starting campaign %s '%s' (status: %s)", campaign_id, campaign["name"], status)

        try:
            # Set up all accounts
            for acct_username in accounts:
                acct = await self.db.get_account(acct_username)
                if not acct:
                    logger.warning("Account %s not in DB, skipping", acct_username)
                    continue
                await self.setup_account(acct_username, acct["password"], acct.get("proxy", ""))

            # Phase 1: Warmup (if not already done)
            if status in (CREATED, QUEUED, WARMING):
                await self.db.update_campaign(campaign_id, status=WARMING)
                await self._warmup_phase(campaign_id, accounts)

                # Refresh status
                campaign = await self.db.get_campaign(campaign_id)
                if campaign and campaign["status"] == PAUSED:
                    return

            # Phase 2: Scaling + Active sending
            if status in (CREATED, QUEUED, WARMING, SCALING, ACTIVE):
                await self.db.update_campaign(campaign_id, status=SCALING)
                await self._sending_phase(campaign_id, accounts, campaign)

            # Check completion
            targets_left = await self.db.get_pending_targets(campaign_id, limit=1)
            if not targets_left:
                await self.db.update_campaign(campaign_id, status=COMPLETED)
                logger.info("Campaign %s completed!", campaign_id)
                await self.monitor.send_alert(f"✅ Campaign #{campaign_id} completed!")
            else:
                await self.db.update_campaign(campaign_id, status=ACTIVE)

        except asyncio.CancelledError:
            logger.info("Campaign %s cancelled", campaign_id)
            await self.db.update_campaign(campaign_id, status=PAUSED)
        except Exception as e:
            logger.error("Campaign %s error: %s", campaign_id, e)
            await self.db.update_campaign(campaign_id, status=PAUSED)

    async def _warmup_phase(self, campaign_id: str, accounts: list[str]) -> None:
        """Run warmup for all accounts in parallel."""
        logger.info("Starting warmup phase for campaign %s", campaign_id)

        tasks = []
        for username in accounts:
            if not await self.warmup_engine.is_warmup_complete(username):
                tasks.append(self.warmup_engine.warmup_account(username))
            else:
                logger.info("Account %s already warmed up", username)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Verify all warmed
        all_warmed = True
        for username in accounts:
            if not await self.warmup_engine.is_warmup_complete(username):
                all_warmed = False
                logger.warning("Account %s warmup incomplete", username)

        if all_warmed:
            logger.info("All accounts warmed up for campaign %s", campaign_id)

    async def _sending_phase(self, campaign_id: str, accounts: list[str],
                              campaign: dict) -> None:
        """Run the DM sending phase with gradual scaling."""
        logger.info("Starting sending phase for campaign %s", campaign_id)

        niche = campaign.get("niche", "")
        template = campaign.get("template", "")
        day_counter = 0

        while True:
            # Check if paused
            camp = await self.db.get_campaign(campaign_id)
            if camp and camp["status"] == PAUSED:
                logger.info("Campaign %s is paused", campaign_id)
                return

            if not self._is_work_time():
                logger.info("Outside work hours, sleeping 30min")
                await asyncio.sleep(30 * 60)
                continue

            day_counter += 1
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Update campaign day for each account
            for username in accounts:
                acct = await self.db.get_account(username)
                if acct and acct.get("campaign_day_date") != today:
                    new_day = (acct.get("campaign_day") or 0) + 1
                    await self.db.update_account(username, campaign_day=new_day, campaign_day_date=today)

            # Get targets
            targets = await self.db.get_pending_targets(campaign_id, limit=100)
            if not targets:
                logger.info("No more pending targets for campaign %s", campaign_id)
                break

            # Distribute targets across accounts
            available_accounts = []
            for username in accounts:
                if await self.monitor.is_account_available(username):
                    available_accounts.append(username)

            if not available_accounts:
                logger.warning("No available accounts, waiting 1h")
                await asyncio.sleep(3600)
                continue

            # Round-robin distribute
            per_account: dict[str, list] = {u: [] for u in available_accounts}
            for i, target in enumerate(targets):
                acct = available_accounts[i % len(available_accounts)]
                per_account[acct].append(target)

            # Send in parallel
            tasks = []
            for username, acct_targets in per_account.items():
                if acct_targets:
                    tasks.append(
                        self.dm_dispatcher.send_batch(
                            username, acct_targets, campaign_id,
                            niche=niche, template_override=template
                        )
                    )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Aggregate stats
            total_sent = 0
            total_failed = 0
            for r in results:
                if isinstance(r, dict):
                    total_sent += r.get("sent", 0)
                    total_failed += r.get("failed", 0)

            await self.db.update_campaign(
                campaign_id,
                total_sent=(camp.get("total_sent", 0) or 0) + total_sent,
                total_failed=(camp.get("total_failed", 0) or 0) + total_failed,
                status=ACTIVE,
            )

            logger.info("Day %d stats — sent: %d, failed: %d", day_counter, total_sent, total_failed)

            # Check for more targets
            remaining = await self.db.get_pending_targets(campaign_id, limit=1)
            if not remaining:
                break

            # Wait until next day's working hours
            logger.info("Day %d complete for campaign %s, waiting for next active period", day_counter, campaign_id)
            while not self._is_work_time():
                await asyncio.sleep(15 * 60)

    async def pause_campaign(self, campaign_id: str) -> None:
        """Pause a running campaign."""
        await self.db.update_campaign(campaign_id, status=PAUSED)
        logger.info("Campaign %s paused", campaign_id)

    async def resume_campaign(self, campaign_id: str) -> None:
        """Resume a paused campaign."""
        campaign = await self.db.get_campaign(campaign_id)
        if not campaign:
            return
        if campaign["status"] != PAUSED:
            logger.warning("Campaign %s is not paused (status: %s)", campaign_id, campaign["status"])
            return
        await self.db.update_campaign(campaign_id, status=ACTIVE)
        logger.info("Campaign %s resumed", campaign_id)

    async def get_campaign_stats(self, campaign_id: str) -> dict:
        """Get comprehensive campaign statistics."""
        campaign = await self.db.get_campaign(campaign_id)
        if not campaign:
            return {}

        accounts = json.loads(campaign.get("accounts", "[]"))
        messages = await self.db.get_messages(campaign_id=campaign_id)

        sent = sum(1 for m in messages if m["status"] == "sent")
        failed = sum(1 for m in messages if m["status"] == "failed")
        blocked = sum(1 for m in messages if m["status"] == "blocked")

        targets = await self.db.get_all_targets(campaign_id)
        pending = sum(1 for t in targets if t["status"] == "pending")
        messaged = sum(1 for t in targets if t["status"] == "messaged")

        account_health = []
        for username in accounts:
            acct = await self.db.get_account(username)
            if acct:
                remaining = await self.rate_limiter.get_remaining(username)
                account_health.append({
                    "username": username,
                    "status": acct["status"],
                    "campaign_day": acct.get("campaign_day", 0),
                    "block_count": acct.get("block_count", 0),
                    **remaining,
                })

        return {
            "campaign": {
                "id": campaign["id"],
                "name": campaign["name"],
                "status": campaign["status"],
                "created_at": campaign["created_at"],
            },
            "messages": {"sent": sent, "failed": failed, "blocked": blocked, "total": len(messages)},
            "targets": {"total": len(targets), "pending": pending, "messaged": messaged},
            "accounts": account_health,
        }

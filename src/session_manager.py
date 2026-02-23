"""Browser session manager with Playwright stealth, fingerprinting, and cookie persistence."""

import json
import os
import random
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger("ig-automator.session")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

MOBILE_DEVICES = [
    {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
    {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 375, "height": 812},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
        "viewport": {"width": 360, "height": 780},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
]

TIMEZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Asia/Kolkata"]
LOCALES = ["en-US", "en-GB", "en-IN"]


def _cookie_path(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}_cookies.json")


def _fingerprint_path(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}_fingerprint.json")


class SessionManager:
    """Manages Playwright browser sessions with stealth, fingerprinting, and persistence."""

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}

    async def start(self) -> None:
        """Launch the Playwright instance and browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        logger.info("Playwright browser launched")

    async def stop(self) -> None:
        """Close all contexts and the browser."""
        for username in list(self._contexts.keys()):
            await self.close_context(username)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Playwright browser closed")

    def _get_or_create_fingerprint(self, username: str) -> dict:
        """Load or generate a persistent device fingerprint for an account."""
        fp_path = _fingerprint_path(username)
        if os.path.exists(fp_path):
            with open(fp_path, "r") as f:
                return json.load(f)

        device = random.choice(MOBILE_DEVICES)
        fp = {
            "user_agent": device["user_agent"],
            "viewport": device["viewport"],
            "device_scale_factor": device["device_scale_factor"],
            "is_mobile": device["is_mobile"],
            "has_touch": device["has_touch"],
            "timezone": random.choice(TIMEZONES),
            "locale": random.choice(LOCALES),
        }
        os.makedirs(os.path.dirname(fp_path), exist_ok=True)
        with open(fp_path, "w") as f:
            json.dump(fp, f, indent=2)
        logger.info("Generated fingerprint for %s", username)
        return fp

    async def create_context(self, username: str, proxy: Optional[dict] = None) -> BrowserContext:
        """Create an isolated browser context for an account with stealth and fingerprint."""
        assert self._browser is not None, "Call start() first"

        fp = self._get_or_create_fingerprint(username)

        ctx_kwargs: dict = {
            "user_agent": fp["user_agent"],
            "viewport": fp["viewport"],
            "device_scale_factor": fp["device_scale_factor"],
            "is_mobile": fp["is_mobile"],
            "has_touch": fp["has_touch"],
            "locale": fp["locale"],
            "timezone_id": fp["timezone"],
        }
        if proxy:
            ctx_kwargs["proxy"] = proxy

        # Load cookies if available
        cookie_file = _cookie_path(username)
        if os.path.exists(cookie_file):
            with open(cookie_file, "r") as f:
                storage_state = json.load(f)
            ctx_kwargs["storage_state"] = cookie_file
            logger.info("Loaded cookies for %s", username)

        context = await self._browser.new_context(**ctx_kwargs)

        # Apply stealth via playwright-stealth
        try:
            from playwright_stealth import Stealth

            stealth = Stealth()  # can pass options here if needed
            await stealth.apply_stealth_async(context)  # patches all pages in this context

            page = await context.new_page()
            self._pages[username] = page
        except ImportError:
            logger.warning("playwright-stealth not installed; running without stealth patches")
            page = await context.new_page()
            self._pages[username] = page

        self._contexts[username] = context
        logger.info("Created browser context for %s", username)
        return context

    async def save_cookies(self, username: str) -> None:
        """Persist cookies/storage state to disk."""
        ctx = self._contexts.get(username)
        if not ctx:
            return
        cookie_file = _cookie_path(username)
        os.makedirs(os.path.dirname(cookie_file), exist_ok=True)
        storage = await ctx.storage_state()
        with open(cookie_file, "w") as f:
            json.dump(storage, f, indent=2)
        logger.info("Saved cookies for %s", username)

    async def close_context(self, username: str) -> None:
        """Save cookies and close a context."""
        await self.save_cookies(username)
        ctx = self._contexts.pop(username, None)
        self._pages.pop(username, None)
        if ctx:
            await ctx.close()
        logger.info("Closed context for %s", username)

    def get_page(self, username: str) -> Optional[Page]:
        """Get the active page for an account."""
        return self._pages.get(username)

    async def login(self, username: str, password: str) -> bool:
        """Perform Instagram login flow with 2FA prompt support.

        Returns True on success, False on failure.
        """
        page = self.get_page(username)
        if not page:
            logger.error("No page for %s — create context first", username)
            return False

        try:
            await page.goto("https://www.instagram.com/accounts/login/", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(random.randint(2000, 4000))

            # Dismiss cookie banner if present
            try:
                accept_btn = page.locator("button:has-text('Allow'), button:has-text('Accept')")
                if await accept_btn.count() > 0:
                    await accept_btn.first.click()
                    await page.wait_for_timeout(1000)
            except Exception:
                pass

            # Fill credentials
            username_input = page.locator('input[name="username"]')
            await username_input.fill("")
            await username_input.type(username, delay=random.randint(50, 150))
            await page.wait_for_timeout(random.randint(500, 1500))

            password_input = page.locator('input[name="password"]')
            await password_input.fill("")
            await password_input.type(password, delay=random.randint(50, 150))
            await page.wait_for_timeout(random.randint(500, 1500))

            # Submit
            await page.locator('button[type="submit"]').click()
            await page.wait_for_timeout(5000)

            # Check for 2FA / challenge
            content = (await page.content()).lower()
            if "challenge" in page.url or "two_factor" in page.url or "confirm your identity" in content or "suspicious activity" in content:
                logger.warning("Challenge/Identity verification required for %s", username)
                logger.info("Please complete the verification in the browser if possible, or follow the prompts.")
                
                # Try to find a code input field if it's a 2FA challenge
                code_input = page.locator('input[name="verificationCode"], input[name="security_code"]')
                if await code_input.count() > 0:
                    # In a real CLI scenario we'd prompt; here we wait up to 120s
                    try:
                        code = input(f"Enter verification code for {username}: ").strip()
                        if code:
                            await code_input.first.type(code, delay=100)
                            await page.locator('button:has-text("Confirm"), button:has-text("Next"), button[type="submit"]').first.click()
                            await page.wait_for_timeout(5000)
                    except EOFError:
                        logger.error("No input stream available for 2FA code")

            # Check success
            final_content = (await page.content()).lower()
            is_success = "login" not in page.url and "challenge" not in page.url and "confirm your identity" not in final_content
            
            if is_success:
                logger.info("Login successful for %s", username)
                await self.save_cookies(username)
                return True

            logger.error("Login failed for %s (still on login/challenge page)", username)
            return False

        except Exception as e:
            logger.error("Login error for %s: %s", username, e)
            return False

    async def is_logged_in(self, username: str) -> bool:
        """Check if the account session is still valid."""
        page = self.get_page(username)
        if not page:
            return False
        try:
            await page.goto("https://www.instagram.com/", wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(2000)
            return "login" not in page.url
        except Exception:
            return False

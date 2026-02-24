"""Session manager: manages Playwright browser, contexts, and login/auth."""

import logging
from pathlib import Path
from typing import Dict, Optional
from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Error as PlaywrightError,
)

logger = logging.getLogger("ig-automator.session")

# Realistic Chrome user agent to reduce bot detection
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class SessionManager:
    """Manages Playwright browser and per-account persistent sessions."""

    def __init__(self, user_id: str = "default", profiles_base_dir: str = ".profiles") -> None:
        self._playwright = None
        self._contexts: Dict[str, BrowserContext] = {}
        self.profiles_dir = Path(profiles_base_dir) / user_id
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        if self._playwright:
            return
        self._playwright = await async_playwright().start()
        logger.info("Playwright started")

    async def stop(self) -> None:
        for username, ctx in list(self._contexts.items()):
            try:
                await ctx.close()
                logger.info("Closed context for %s", username)
            except Exception as e:
                logger.warning("Error closing context for %s: %s", username, e)
        self._contexts.clear()
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    def get_context(self, username: str) -> Optional[BrowserContext]:
        return self._contexts.get(username)

    def get_page(self, username: str) -> Optional[Page]:
        ctx = self.get_context(username)
        if ctx and ctx.pages:
            return ctx.pages[0]
        return None

    async def create_context(
        self,
        username: str,
        proxy: Optional[dict] = None,
        headless: bool = True,
        viewport: Optional[dict] = None,
    ) -> BrowserContext:
        """Create or attach to a persistent browser context for an account."""
        if not self._playwright:
            raise RuntimeError("Playwright not started. Call SessionManager.start() first.")
        if username in self._contexts:
            return self._contexts[username]
        user_data_dir = str(self.profiles_dir / username)
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-infobars",
            "--disable-extensions",
        ]
        context_kwargs = {
            "user_data_dir": user_data_dir,
            "headless": headless,
            "args": launch_args,
            "viewport": viewport or {"width": 1280, "height": 720},
            "user_agent": _USER_AGENT,
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if proxy:
            context_kwargs["proxy"] = proxy
        ctx = await self._playwright.chromium.launch_persistent_context(**context_kwargs)
        self._contexts[username] = ctx

        # Mask navigator.webdriver to reduce bot detection
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        )

        if not ctx.pages:
            await ctx.new_page()
        logger.info("Created persistent context for %s", username)
        return ctx

    async def is_logged_in(self, username: str) -> bool:
        """
        Check login status using session cookies first (fast, no page navigation).
        Falls back to a lightweight page check only if no session cookie is found.
        """
        ctx = self.get_context(username)
        if not ctx:
            logger.warning(f"[is_logged_in] No context for {username}")
            return False

        # --- Fast path: check for sessionid cookie ---
        try:
            cookies = await ctx.cookies("https://www.instagram.com")
            session_cookie = next(
                (c for c in cookies if c.get("name") == "sessionid" and c.get("value")),
                None,
            )
            if session_cookie:
                logger.info(
                    f"[is_logged_in] Found valid sessionid cookie for {username}"
                )
                return True
            logger.info(
                f"[is_logged_in] No sessionid cookie for {username}, falling back to page check"
            )
        except Exception as e:
            logger.warning(f"[is_logged_in] Cookie check failed for {username}: {e}")

        # --- Slow path: navigate and inspect the page ---
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(
                "https://www.instagram.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(3000)

            login_form = await page.query_selector('input[name="username"]')
            if login_form:
                logger.info(f"[is_logged_in] Login form detected for {username}")
                await page.screenshot(path=f"not_logged_in_{username}.png")
                return False

            selectors = [
                'svg[aria-label="Home"]',
                'svg[aria-label="New post"]',
                'svg[aria-label="Messenger"]',
                'svg[aria-label="Explore"]',
                'svg[aria-label="Reels"]',
                'img[data-testid="user-avatar"]',
                'span:has-text("Home")',
                'span:has-text("Messages")',
                'a[href*="/direct/inbox"]',
            ]
            for sel in selectors:
                el = await page.query_selector(sel)
                if el:
                    logger.info(
                        f"[is_logged_in] Found logged-in selector '{sel}' for {username}"
                    )
                    return True

            logger.warning(
                f"[is_logged_in] No logged-in selectors found for {username}. "
                f"URL: {page.url}, Title: {await page.title()}"
            )
            await page.screenshot(path=f"not_logged_in_{username}.png")
            return False

        except PlaywrightError as e:
            logger.error(f"[is_logged_in] PlaywrightError for {username}: {e}")
            return False
        except Exception as e:
            logger.error(f"[is_logged_in] Exception for {username}: {e}")
            return False

    async def _wait_for_login_page(self, page: Page, username: str) -> bool:
        """
        Navigate to the Instagram login page and wait for the username input to appear.
        Returns True if the login form is ready, False otherwise.
        """
        try:
            await page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="networkidle",
                timeout=60000,
            )
            # Check if we are redirected to the home page (logged in or weird state)
            if "login" not in page.url and await self.is_logged_in(username):
                return True

            # Explicit wait for the username field (up to 20s after navigation)
            try:
                await page.wait_for_selector(
                    'input[name="username"]', state="visible", timeout=15000
                )
                return True
            except PlaywrightError:
                # If direct login fails, try going to home page and clicking login or looking for inputs
                await page.goto("https://www.instagram.com/", wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(5000) # Wait for elements to render
                
                # Check for username input on home page (some variants)
                if await page.query_selector('input[name="username"]'):
                    return True
                
                # Try to find a link to the login page
                login_link = await page.query_selector('a[href*="/login/"]')
                if login_link:
                    await login_link.click()
                    await page.wait_for_selector('input[name="username"]', state="visible", timeout=15000)
                    return True
                
                return False
        except PlaywrightError as e:
            logger.error(
                f"[_wait_for_login_page] Login page did not load for {username}: {e}"
            )
            await page.screenshot(path=f"login_page_error_{username}.png")
            logger.info(f"[_wait_for_login_page] Screenshot saved: login_page_error_{username}.png")
            return False

    async def login(self, username: str, password: str) -> bool:
        """Standard password-only login."""
        ctx = self.get_context(username)
        if not ctx:
            ctx = await self.create_context(username, headless=True)
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            if not await self._wait_for_login_page(page, username):
                logger.error(f"[login] Could not load login page for {username}")
                return False

            await page.fill('input[name="username"]', username)
            await page.fill('input[name="password"]', password)
            await page.wait_for_timeout(500)  # small human-like pause
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(6000)

            if await self.is_logged_in(username):
                logger.info(f"[login] Password login successful for {username}")
                return True
            else:
                logger.error(f"[login] Password login failed for {username}")
                await page.screenshot(path=f"login_failed_{username}.png")
                return False
        except Exception as e:
            logger.error(f"[login] Exception for {username}: {e}")
            await page.screenshot(path=f"login_exception_{username}.png")
            return False

    async def authenticate_with_secret(
        self, username: str, secret_key: str, password: Optional[str] = None
    ) -> bool:
        """Login with password + TOTP 2FA secret key."""
        import pyotp

        ctx = self.get_context(username)
        if not ctx:
            ctx = await self.create_context(username, headless=True)
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            if not await self._wait_for_login_page(page, username):
                logger.error(f"[2FA] Could not load login page for {username}")
                return False

            await page.fill('input[name="username"]', username)
            await page.fill('input[name="password"]', password)
            await page.wait_for_timeout(500)
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(5000)

            # Wait for the 2FA input field to appear
            try:
                await page.wait_for_selector(
                    'input[name="verificationCode"]', state="visible", timeout=15000
                )
            except PlaywrightError:
                logger.warning(
                    f"[2FA] verificationCode field not found for {username}; "
                    "Instagram may not have prompted for 2FA or login already succeeded."
                )
                # Maybe login succeeded without 2FA prompt
                if await self.is_logged_in(username):
                    logger.info(f"[2FA] Logged in without 2FA prompt for {username}")
                    return True
                await page.screenshot(path=f"2fa_no_prompt_{username}.png")
                return False

            totp = pyotp.TOTP(secret_key)
            code = totp.now()
            logger.info(f"[2FA] Using TOTP code for {username}")
            await page.fill('input[name="verificationCode"]', code)
            await page.wait_for_timeout(500)

            # Try both common button labels
            confirm_clicked = False
            for selector in [
                'button[type="button"]:has-text("Confirm")',
                'button[type="submit"]:has-text("Confirm")',
                'button:has-text("Submit")',
            ]:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    confirm_clicked = True
                    break
            if not confirm_clicked:
                logger.warning(f"[2FA] Could not find Confirm button for {username}")

            await page.wait_for_timeout(6000)

            if await self.is_logged_in(username):
                logger.info(f"[2FA] 2FA login successful for {username}")
                return True
            else:
                logger.error(f"[2FA] 2FA login failed for {username}")
                await page.screenshot(path=f"2fa_failed_{username}.png")
                return False
        except Exception as e:
            logger.error(f"[2FA] Exception for {username}: {e}")
            await page.screenshot(path=f"2fa_exception_{username}.png")
            return False
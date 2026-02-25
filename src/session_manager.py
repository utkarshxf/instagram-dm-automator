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

    def __init__(self, profiles_dir: str = ".profiles") -> None:
        self._playwright = None
        self._contexts: Dict[str, BrowserContext] = {}
        self.profiles_dir = Path(profiles_dir)
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
                logger.info(f"[is_logged_in] Found valid sessionid cookie for {username}")
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

            login_form = await page.query_selector('input[name="email"]')
            if login_form:
                logger.info(f"[is_logged_in] Login form detected for {username}")
                await page.screenshot(path=f"not_logged_in_{username}.png")
                return False

            selectors = [
                'a[href="/"] svg',
                'a[href*="/direct/inbox"]',
                'svg[aria-label="Home"]',
                'svg[aria-label="Messenger"]',
                'img[data-testid="user-avatar"]',
                'nav',
                'header',
            ]
            for sel in selectors:
                el = await page.query_selector(sel)
                if el:
                    logger.info(f"[is_logged_in] Found logged-in selector '{sel}' for {username}")
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
                wait_until="domcontentloaded",
                timeout=60000,
            )
            # Instagram currently uses name="email" for the username/email/phone field
            username_selectors = [
                'input[name="email"]',
                'input[name="username"]',


                'input[autocomplete="username webauthn"]',
                'input[type="text"]',
            ]

            found = False
            for selector in username_selectors:
                try:
                    await page.wait_for_selector(selector, state="visible", timeout=5000)
                    logger.info(f"[_wait_for_login_page] Found username field with selector: {selector}")
                    found = True
                    break
                except PlaywrightError:
                    continue

            if not found:
                await page.wait_for_selector('input[name="email"]',
                'input[name="username"]', state="visible", timeout=10000)

            return True
        except PlaywrightError as e:
            logger.error(f"[_wait_for_login_page] Login page did not load for {username}: {e}")
            await page.screenshot(path=f"login_page_error_{username}.png")
            return False

    async def _fill_credentials(self, page: Page, username: str, password: str) -> None:
        """
        Fill username and password using press_sequentially to fire real key events.

        Instagram's React login button only becomes enabled after genuine DOM input events.
        page.fill() sets the value directly without firing React's onChange handlers,
        so the submit button stays disabled. press_sequentially() types character-by-character,
        firing the real events React needs.
        """
        # Instagram uses name="email" (not name="username") for the username/email field
        username_selector = (
            'input[name="email"]'
            if await page.query_selector('input[name="email"]')
            else 'input[type="text"]'
        )
        await page.click(username_selector)
        await page.locator(username_selector).press_sequentially(username, delay=80)

        await page.wait_for_timeout(300)

        # Instagram uses name="pass" (not name="password") for the password field
        password_selector = (
            'input[name="pass"]'
            if await page.query_selector('input[name="pass"]')
            else 'input[type="password"]'
        )
        await page.click(password_selector)
        await page.locator(password_selector).press_sequentially(password, delay=80)

        # Let React process input events and enable the submit button
        await page.wait_for_timeout(500)

    async def _click_submit_button(self, page: Page, username: str, log_prefix: str = "") -> bool:
        """
        Wait for Instagram's login button to become enabled, then click it.

        Instagram renders the login button as:
            <div role="button" aria-label="Log In" aria-disabled="true" ...>
        It is NOT a <button type="submit">. The aria-disabled attribute flips to "false"
        (or is removed) once React detects valid input in both fields.
        """
        prefix = f"[{log_prefix}]" if log_prefix else "[_click_submit_button]"

        # Primary: wait for aria-disabled to become false on the Log In div-button
        try:
            await page.wait_for_function(
                """() => {
                    const btn = document.querySelector('div[role="button"][aria-label="Log In"]');
                    if (!btn) return false;
                    const disabled = btn.getAttribute('aria-disabled');
                    return disabled === 'false' || disabled === null;
                }""",
                timeout=10000,
            )
            await page.click('div[role="button"][aria-label="Log In"]')
            logger.info(f"{prefix} Clicked Log In button for {username}")
            return True
        except PlaywrightError:
            logger.warning(f"{prefix} Log In div-button not enabled within timeout for {username}, trying Enter fallback")

        # Fallback: press Enter from the password field — submits the form regardless
        try:
            password_sel = (
                'input[name="pass"]'
                if await page.query_selector('input[name="pass"]')
                else 'input[type="password"]'
            )
            await page.press(password_sel, "Enter")
            logger.info(f"{prefix} Pressed Enter on password field (fallback) for {username}")
            return True
        except PlaywrightError as e:
            logger.error(f"{prefix} Enter fallback also failed for {username}: {e}")
            return False

    async def _handle_2fa(self, page: Page, username: str, secret_key: str) -> bool:
        """
        Handle Instagram's 2FA screen that appears after username+password submission.

        Instagram's full login flow with 2FA:
          1. User submits username + password  (handled by caller)
          2. Instagram redirects to 2FA screen
          3. User enters 6-digit TOTP code     (handled here)
          4. User confirms → logged in

        Returns True if the 2FA code was entered and submitted successfully.
        Caller must verify is_logged_in() after this returns.
        """
        import pyotp

        logger.info(f"[2FA] Waiting for 2FA verification screen for {username}")

        # Wait for the TOTP code input field — Instagram uses several possible selectors
        verification_selectors = [
            'input[name="verificationCode"]',
            'input[aria-label*="security code"]',
            'input[aria-label*="confirmation code"]',
            'input[aria-label*="Security Code"]',
            'input[autocomplete="one-time-code"]',
        ]

        verification_selector = None
        for selector in verification_selectors:
            try:
                await page.wait_for_selector(selector, state="visible", timeout=15000)
                verification_selector = selector
                logger.info(f"[2FA] Found 2FA input field with selector '{selector}' for {username}")
                break
            except PlaywrightError:
                continue

        if not verification_selector:
            logger.error(f"[2FA] No 2FA input field found for {username}")
            await page.screenshot(path=f"2fa_no_prompt_{username}.png")
            return False

        # Generate current TOTP code and type it character-by-character
        totp = pyotp.TOTP(secret_key)
        code = totp.now()
        logger.info(f"[2FA] Typing TOTP code into verification field for {username}")
        await page.click(verification_selector)
        await page.locator(verification_selector).press_sequentially(code, delay=60)
        await page.wait_for_timeout(500)

        # Instagram 2FA confirm is: <button type="button">Confirm</button>
        confirm_clicked = False
        confirm_selectors = [
            'button[type="button"]:has-text("Confirm")',
            'button:has-text("Confirm")',
            'button[type="submit"]',
            'div[role="button"][aria-label="Confirm"]',
        ]
        for selector in confirm_selectors:
            try:
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                await page.click(selector)
                confirm_clicked = True
                logger.info(f"[2FA] Clicked confirm button '{selector}' for {username}")
                break
            except PlaywrightError:
                continue

        if not confirm_clicked:
            await page.keyboard.press("Enter")
            logger.warning(f"[2FA] Used Enter fallback to confirm 2FA code for {username}")

        await page.wait_for_timeout(6000)
        return True

    async def login(self, username: str, password: str) -> bool:
        """Standard password-only login (no 2FA)."""
        ctx = self.get_context(username)
        if not ctx:
            ctx = await self.create_context(username, headless=True)
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            if not await self._wait_for_login_page(page, username):
                logger.error(f"[login] Could not load login page for {username}")
                return False

            await self._fill_credentials(page, username, password)

            if not await self._click_submit_button(page, username, log_prefix="login"):
                logger.error(f"[login] Could not submit login form for {username}")
                await page.screenshot(path=f"login_failed_{username}.png")
                return False

            await page.wait_for_timeout(6000)

            if await self.is_logged_in(username):
                logger.info(f"[login] Login successful for {username}")
                return True
            else:
                logger.error(f"[login] Login failed for {username}")
                await page.screenshot(path=f"login_failed_{username}.png")
                return False
        except Exception as e:
            logger.error(f"[login] Exception for {username}: {e}")
            await page.screenshot(path=f"login_exception_{username}.png")
            return False

    async def authenticate_with_secret(
        self, username: str, secret_key: str, password: Optional[str] = None
    ) -> bool:
        """
        Full login flow for accounts with 2FA enabled:

          Step 1 — Load login page, fill username + password, submit.
          Step 2 — Instagram redirects to 2FA screen; fill TOTP code and confirm.
          Step 3 — Verify session is established.

        If Instagram skips the 2FA screen (trusted device / 2FA not configured),
        the method detects this and returns True without attempting 2FA.
        """
        ctx = self.get_context(username)
        if not ctx:
            ctx = await self.create_context(username, headless=True)
        page: Page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            # ── Step 1: username + password ──────────────────────────────────
            if not await self._wait_for_login_page(page, username):
                logger.error(f"[2FA] Could not load login page for {username}")
                return False

            await self._fill_credentials(page, username, password)

            if not await self._click_submit_button(page, username, log_prefix="2FA-step1"):
                logger.error(f"[2FA] Could not submit credentials for {username}")
                await page.screenshot(path=f"2fa_submit_failed_{username}.png")
                return False

            # Wait for Instagram to navigate away from the login page.
            # DO NOT call is_logged_in() here — it navigates to instagram.com which
            # interrupts the 2FA screen mid-flow, causing Instagram to reset to login.
            # Instead, watch the URL: if it leaves /accounts/login/ we know credentials
            # were accepted and Instagram is now on the 2FA screen (or logged in).
            try:
                await page.wait_for_url(
                    # Accept two_factor page OR any page that isn't the plain login page
                    lambda url: "two_factor" in url or "/accounts/login/" not in url,
                    timeout=15000,
                )
                logger.info(f"[2FA] Navigated after login submit to: {page.url}")
            except PlaywrightError:
                # Still on plain login page — credentials rejected or bot-detected
                await page.screenshot(path=f"2fa_still_on_login_{username}.png")
                logger.error(f"[2FA] Still on login page after submit for {username}, URL: {page.url}")
                return False

            # ── Early exit: already logged in (no 2FA required) ──────────────
            # Check cookies only — do NOT navigate away from the current page
            cookies = await ctx.cookies("https://www.instagram.com")
            session_cookie = next(
                (c for c in cookies if c.get("name") == "sessionid" and c.get("value")),
                None,
            )
            if session_cookie:
                logger.info(f"[2FA] Logged in without 2FA prompt for {username}")
                return True

            # ── Step 2: fill TOTP code on the 2FA screen ─────────────────────
            # We are on the 2FA page — do NOT navigate away until code is submitted
            if not await self._handle_2fa(page, username, secret_key):
                logger.error(f"[2FA] 2FA handling failed for {username}")
                return False

            # ── Step 3: verify session via cookies (no page navigation) ───────
            await page.wait_for_timeout(3000)
            cookies = await ctx.cookies("https://www.instagram.com")
            session_cookie = next(
                (c for c in cookies if c.get("name") == "sessionid" and c.get("value")),
                None,
            )
            if session_cookie:
                logger.info(f"[2FA] Full login with 2FA successful for {username}")
                return True
            else:
                logger.error(f"[2FA] No session cookie after 2FA for {username}, URL: {page.url}")
                await page.screenshot(path=f"2fa_final_failed_{username}.png")
                return False

        except Exception as e:
            logger.error(f"[2FA] Exception for {username}: {e}")
            await page.screenshot(path=f"2fa_exception_{username}.png")
            return False
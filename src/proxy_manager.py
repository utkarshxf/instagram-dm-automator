"""Proxy manager with rotation, health checks, and per-IP account limits."""

import asyncio
import logging
import re
from urllib.parse import urlparse
from typing import Optional

import aiohttp

from .db import Database

logger = logging.getLogger("ig-automator.proxy")


class ProxyManager:
    """Manages proxy rotation and assignment for Instagram accounts."""

    def __init__(self, config: dict, db: Database) -> None:
        self.config = config.get("proxy", {})
        self.max_per_ip: int = self.config.get("max_accounts_per_ip", 5)
        self.rotation_enabled: bool = self.config.get("rotation_enabled", True)
        self.db = db

        # Collect all proxy URLs from accounts + scraping config
        self._proxies: list[str] = []
        for acct in config.get("accounts", []):
            p = acct.get("proxy", "")
            if p and p not in self._proxies:
                self._proxies.append(p)
        scrape_proxy = config.get("scraping", {}).get("proxy", "")
        if scrape_proxy and scrape_proxy not in self._proxies:
            self._proxies.append(scrape_proxy)

        self._healthy: set[str] = set()
        self._ip_cache: dict[str, str] = {}

    @staticmethod
    def extract_ip(proxy_url: str) -> str:
        """Extract the host/IP from a proxy URL."""
        parsed = urlparse(proxy_url)
        return parsed.hostname or proxy_url

    async def health_check(self, proxy_url: str, timeout: float = 10.0) -> bool:
        """Check if a proxy is functional by fetching a test URL."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://httpbin.org/ip",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        real_ip = data.get("origin", "")
                        self._ip_cache[proxy_url] = real_ip
                        self._healthy.add(proxy_url)
                        logger.info("Proxy %s healthy (IP: %s)", proxy_url, real_ip)
                        return True
        except Exception as e:
            logger.warning("Proxy %s failed health check: %s", proxy_url, e)
        self._healthy.discard(proxy_url)
        return False

    async def check_all_proxies(self) -> list[str]:
        """Health-check all configured proxies and return healthy ones."""
        tasks = [self.health_check(p) for p in self._proxies]
        await asyncio.gather(*tasks, return_exceptions=True)
        healthy = [p for p in self._proxies if p in self._healthy]
        logger.info("Healthy proxies: %d/%d", len(healthy), len(self._proxies))
        return healthy

    async def assign_proxy_to_account(self, account_username: str, preferred_proxy: str = "") -> Optional[str]:
        """Assign a proxy to an account, respecting the max-per-IP limit.

        If *preferred_proxy* is provided and valid, it is tried first.
        Falls back to the least-loaded healthy proxy.
        """
        # Try preferred first
        if preferred_proxy:
            ip = self._ip_cache.get(preferred_proxy, self.extract_ip(preferred_proxy))
            count = await self.db.get_accounts_on_proxy(ip)
            if count < self.max_per_ip:
                await self.db.assign_proxy(preferred_proxy, ip, account_username)
                logger.info("Assigned preferred proxy %s to %s", preferred_proxy, account_username)
                return preferred_proxy

        # Find least-loaded healthy proxy
        best: Optional[str] = None
        best_count = self.max_per_ip + 1
        for proxy_url in self._proxies:
            if proxy_url not in self._healthy:
                continue
            ip = self._ip_cache.get(proxy_url, self.extract_ip(proxy_url))
            count = await self.db.get_accounts_on_proxy(ip)
            if count < self.max_per_ip and count < best_count:
                best = proxy_url
                best_count = count

        if best:
            ip = self._ip_cache.get(best, self.extract_ip(best))
            await self.db.assign_proxy(best, ip, account_username)
            logger.info("Assigned proxy %s to %s (load: %d)", best, account_username, best_count)
            return best

        logger.error("No available proxy for %s (all at capacity or unhealthy)", account_username)
        return None

    async def get_proxy(self, account_username: str) -> Optional[str]:
        """Get the proxy currently assigned to an account."""
        return await self.db.get_proxy_for_account(account_username)

    def get_scraping_proxy(self) -> Optional[str]:
        """Get the dedicated scraping proxy from config."""
        return self.config.get("scraping", {}).get("proxy") or (
            self._proxies[0] if self._proxies else None
        )

    def parse_proxy_for_playwright(self, proxy_url: str) -> dict:
        """Convert a proxy URL to Playwright's proxy dict format."""
        parsed = urlparse(proxy_url)
        result: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        return result

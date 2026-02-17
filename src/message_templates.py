"""Message template engine with spintext and Jinja2 support."""

import hashlib
import logging
import random
import re
from typing import Optional

from jinja2 import Template

from .db import Database

logger = logging.getLogger("ig-automator.templates")

SPINTEXT_PATTERN = re.compile(r"\{([^{}]+)\}")


def resolve_spintext(text: str) -> str:
    """Resolve spintext syntax: {Hi|Hey|Hello} → picks one randomly.

    Handles nested spintext by resolving innermost first.
    """
    def _pick(match: re.Match) -> str:
        options = match.group(1).split("|")
        return random.choice(options).strip()

    prev = None
    result = text
    while prev != result:
        prev = result
        result = SPINTEXT_PATTERN.sub(_pick, result)
    return result


def render_template(template_str: str, variables: dict) -> str:
    """Render a message template with Jinja2 variables and spintext.

    1. First resolve spintext {A|B|C}
    2. Then render Jinja2 variables {{ first_name }}, etc.
    3. Also support simple {first_name} style (non-pipe) as Jinja2 vars
    """
    # Resolve spintext (only patterns with pipes)
    def _resolve_pipes(match: re.Match) -> str:
        content = match.group(1)
        if "|" in content:
            return random.choice(content.split("|")).strip()
        # Not spintext — leave as Jinja2 variable
        return "{{ " + content + " }}"

    processed = SPINTEXT_PATTERN.sub(_resolve_pipes, template_str)

    # Render Jinja2
    try:
        tmpl = Template(processed)
        return tmpl.render(**variables)
    except Exception as e:
        logger.warning("Template render error: %s", e)
        return processed


def message_hash(text: str) -> str:
    """Generate a short hash of a message for deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class MessageTemplateEngine:
    """Manages message templates with variation enforcement."""

    def __init__(self, config: dict, db: Database) -> None:
        self.templates: list[str] = config.get("templates", [])
        self.db = db

        if not self.templates:
            logger.warning("No message templates configured!")

    async def generate_message(self, account_username: str, target: dict,
                                niche: str = "", template_override: str = "") -> str:
        """Generate a personalized, unique message for a target.

        Ensures no two consecutive messages from the same account are identical.
        """
        variables = {
            "first_name": self._extract_first_name(target),
            "username": target.get("username", ""),
            "bio_keyword": self._extract_bio_keyword(target.get("bio", "")),
            "niche": niche,
        }

        templates_to_try = [template_override] if template_override else list(self.templates)
        if not templates_to_try:
            return f"Hey {variables['first_name']}! Would love to connect."

        random.shuffle(templates_to_try)
        last_hash = await self.db.get_last_message_hash(account_username)

        for tmpl in templates_to_try:
            rendered = render_template(tmpl, variables)
            h = message_hash(rendered)
            if h != last_hash:
                await self.db.add_message_hash(account_username, h)
                return rendered

        # If all produce same hash (unlikely), just use the last rendered
        rendered = render_template(templates_to_try[0], variables)
        await self.db.add_message_hash(account_username, message_hash(rendered))
        return rendered

    @staticmethod
    def _extract_first_name(target: dict) -> str:
        """Extract first name from full_name, falling back to username."""
        full_name = target.get("full_name", "").strip()
        if full_name:
            return full_name.split()[0]
        username = target.get("username", "there")
        return username

    @staticmethod
    def _extract_bio_keyword(bio: str) -> str:
        """Extract a keyword from bio for personalization."""
        if not bio:
            return ""
        # Take the first meaningful word (>3 chars, not common filler)
        filler = {"the", "and", "for", "with", "that", "this", "from", "your", "have", "been", "just"}
        words = [w.strip(".,!?#@") for w in bio.split() if len(w) > 3]
        meaningful = [w for w in words if w.lower() not in filler and not w.startswith(("http", "@", "#"))]
        return meaningful[0] if meaningful else ""

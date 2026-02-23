# Instagram DM Automator

A robust, safety-first Python tool for automating Instagram cold DMs at scale. Features persistent browser sessions, account warming, gradual scaling, proxy rotation, fingerprint management, message personalization, intelligent rate limiting, and advanced fallback login logic (persistent session, 2FA, password).

> ⚠️ **Disclaimer**: Automating Instagram may violate their Terms of Service. Use at your own risk. The authors are not responsible for any bans, restrictions, or consequences. Educational use only.

---

## Table of Contents
- [Features](#features)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Configuration](#configuration)
- [Account Onboarding & Login Fallbacks](#account-onboarding--login-fallbacks)
- [Campaign Workflow](#campaign-workflow)
- [Message Templates & Personalization](#message-templates--personalization)
- [CLI Usage](#cli-usage)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Project Structure](#project-structure)
- [Safety Guidelines](#safety-guidelines)

---

## Features
- **Persistent Browser Sessions**: Log in once, reuse session for all campaigns (no repeated logins, fewer blocks).
- **Automatic Login Fallbacks**: If persistent session fails, tries 2FA (TOTP) login, then password login.
- **Account Warming**: Simulates human activity for 48-72h before sending DMs.
- **Gradual Scaling**: Day 1: 20 DMs → Day 2: 30 → Day 3: 40 → Day 4+: 50.
- **Proxy Rotation**: Supports residential/mobile proxies, max 5 accounts per IP.
- **Fingerprint Management**: Unique device profile per account (user agent, viewport, timezone).
- **Message Personalization**: Spintext + Jinja2 templates with variables.
- **Rate Limiting**: 10/hour, 50/day per account, with cooldowns.
- **Block Detection**: Auto-pause on action blocks, exponential backoff.
- **Campaign Management**: Full state machine, parallel account orchestration.
- **SQLite Persistence**: Resume campaigns after restart, full audit trail.
- **Rich CLI**: Pretty tables, progress bars, color-coded output.

---

## How It Works

1. **Add your Instagram accounts** (with password and optional 2FA secret key).
2. **Onboard accounts**: The tool tries to reuse a persistent browser session. If not logged in, it falls back to 2FA or password login automatically.
3. **Import or scrape targets** for your campaign.
4. **Create a campaign**: Assign accounts, message templates, and settings.
5. **Start the campaign**: The tool warms up accounts, gradually scales DM sending, and handles all safety logic.
6. **Monitor progress**: View status, pause/resume, and export results.

---

## Installation

### Prerequisites
- Python 3.11+
- pip

### Steps
```bash
# 1. Clone/download the project
cd instagram-dm-automator

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright browsers
playwright install chromium

# 4. Configure
cp config.example.json config.json
# Edit config.json with your accounts, proxies, and templates
```

---

## Configuration

Edit `config.json` (see `config.example.json` for reference):

### Accounts
```json
"accounts": [
  {
    "username": "your_ig_username",
    "password": "your_ig_password",
    "proxy": "http://user:pass@residential-proxy:port",
    "secret_key": "YOUR_TOTP_SECRET"  // Optional, for 2FA fallback
  }
]
```

### Warmup Settings
```json
"warmup": {
  "duration_hours": 48,
  "actions_per_session": 15,
  "session_gap_minutes": 120
}
```

### DM Settings
```json
"dm": {
  "daily_limit": 50,
  "hourly_limit": 10,
  "min_delay_seconds": 120,
  "max_delay_seconds": 420,
  "scaling_days": 4,
  "scaling_start": 20,
  "scaling_increment": 10
}
```

### Templates (Spintext + Variables)
```json
"templates": [
  "Hey {first_name}! I noticed your work in {niche} and wanted to connect. Would love to chat about a potential collaboration 🙌",
  "{Hi|Hello|Hey} {first_name}, I came across your profile and really liked your content. Quick question — are you open to exploring new opportunities?",
  "Hi {first_name}! Your {niche} content is amazing. I'm working on something similar and thought we could help each other out. Mind if I share?"
]
```
- **Variables:** `first_name`, `username`, `bio_keyword`, `niche`
- **Spintext:** `{option1|option2|option3}` — randomly picks one
- **Jinja2:** Use `{{ variable }}` for advanced logic

### Schedule
```json
"schedule": {
  "active_hours_start": 8,
  "active_hours_end": 23,
  "days_off": ["Sunday"]
}
```

---

## Account Onboarding & Login Fallbacks

When you start a campaign, the tool will automatically:
1. **Try to reuse the persistent browser session** (no login needed if already logged in).
2. **If not logged in:**
   - If a 2FA secret key is present, it will generate a TOTP code and attempt 2FA login.
   - If 2FA fails or is not configured, it will attempt password login.
3. **If all fail:** You will be prompted to run `python main.py open-session <username>` and log in manually in a browser window.

**Manual Onboarding (if needed):**
```bash
python main.py open-session <username>
# Log in manually, solve any Instagram challenges, then close the browser window.
```

---

## Campaign Workflow

### 1. Add Accounts
```bash
python main.py add-account myusername mypassword --proxy http://user:pass@host:port --secret-key YOUR_TOTP_SECRET
```

### 2. Import or Scrape Targets
```bash
# Import from CSV (columns: username, full_name, bio, follower_count)
python main.py add-targets --csv targets.csv --campaign 1

# Scrape followers from a public account
python main.py add-targets --scrape competitor_account --campaign 1
```

### 3. Create a Campaign
```bash
python main.py create-campaign "Q1 Outreach" \
  --niche "fitness" \
  --accounts "account1,account2" \
  --template 0
```

### 4. Start a Campaign
```bash
python main.py start 1
```
- The tool will automatically warm up accounts, scale DM sending, and handle all login fallbacks.

### 5. Monitor & Manage
```bash
# Check all campaigns
python main.py status

# Check a specific campaign
python main.py status --campaign 1

# Pause/resume
python main.py pause 1
python main.py resume 1

# Export data
python main.py export --campaign 1 --type messages -o messages.csv
python main.py export --campaign 1 --type targets -o targets.csv
```

---

## Message Templates & Personalization

- **Spintext:** Use `{Hi|Hey|Hello}` to randomize greetings.
- **Variables:** Use `{first_name}`, `{niche}`, `{bio_keyword}` for dynamic content.
- **Jinja2:** Advanced logic with `{{ ... }}` (e.g., `{{ first_name|capitalize }}`).
- **Deduplication:** The engine ensures no two consecutive messages from the same account are identical.

**Example:**
```json
"templates": [
  "{Hi|Hey|Hello} {{ first_name }}! Loved your {{ niche }} content 🙌",
  "Hey {{ first_name }}, would love to connect about {{ niche }}!"
]
```

---

## Troubleshooting & FAQ

| Issue | Solution |
|-------|----------|
| Login fails | Check credentials; ensure 2FA app is ready for code prompt; try manual onboarding |
| "Action blocked" | Account is rate-limited; tool auto-pauses with cooldown |
| Proxy errors | Run health check; ensure proxy supports HTTPS |
| No message button | Target may have DM restrictions; tool auto-skips |
| Playwright crash | Run `playwright install chromium` again |
| Database locked | Only run one instance at a time |
| Instagram checkpoint | Run `python main.py open-session <username>` and resolve challenge |
| pyotp not found | Run `pip install pyotp` |

**Screenshots:** If login fails, check the generated `not_logged_in_<username>.png` for clues.

---

## Project Structure

```
instagram-dm-automator/
├── main.py                 # CLI entry point (Click + Rich)
├── config.json             # Your configuration (git-ignored)
├── config.example.json     # Template configuration
├── requirements.txt        # Python dependencies
├── src/
│   ├── session_manager.py  # Playwright browser + stealth + cookies + login fallback
│   ├── warmup_engine.py    # Human-like account warming
│   ├── dm_dispatcher.py    # DM sending with scaling
│   ├── scraper.py          # Lead scraping (separate session)
│   ├── proxy_manager.py    # Proxy rotation + health checks
│   ├── message_templates.py # Spintext + Jinja2 templates
│   ├── rate_limiter.py     # Hourly/daily limits + backoff
│   ├── monitor.py          # Block detection + alerts
│   ├── campaign.py         # Campaign state machine
│   └── db.py               # SQLite persistence layer
├── data/                   # Cookies, DB, exports
└── logs/                   # Daily log files
```

---

## Safety Guidelines

1. **Use residential/mobile proxies** — Datacenter proxies get detected instantly.
2. **Don't skip warmup** — New accounts need 48-72h of normal activity.
3. **Keep daily limits low** — 50/day max is already aggressive; 30 is safer.
4. **Use multiple accounts** — Spread volume across accounts.
5. **Max 5 accounts per proxy IP** — More triggers Instagram's fraud detection.
6. **Vary your messages** — Use spintext and multiple templates.
7. **Respect cooldowns** — If blocked, wait the full cooldown period.
8. **Monitor blocks** — Set up webhook alerts (Discord/Telegram).
9. **Don't run 24/7** — Use schedule settings for realistic activity windows.

---

## Advanced Usage & Tips
- **2FA Fallback:** Add your TOTP secret to `config.json` for each account for automated 2FA login.
- **Persistent Sessions:** Once logged in, sessions are reused for all future campaigns.
- **Manual Login:** If all else fails, use `open-session` to log in via browser and solve any Instagram challenges.
- **Custom Templates:** Use advanced Jinja2 and spintext for highly personalized outreach.
- **Scaling:** Safely increase volume by adding more warmed-up accounts and proxies.

---

## License

MIT — Use responsibly.

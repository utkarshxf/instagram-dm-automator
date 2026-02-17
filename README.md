# Instagram DM Automator

A Python-based Instagram cold DM automation tool with safety-first design: account warming, gradual scaling, proxy rotation, fingerprint management, message personalization, and intelligent rate limiting.

> ⚠️ **Disclaimer**: This tool automates interactions with Instagram, which may violate Instagram's Terms of Service. Use at your own risk. The authors are not responsible for any account bans, restrictions, or other consequences. This tool is provided for educational purposes only.

## Features

- 🔥 **Account Warming** — Human-like behavior simulation (48-72h) before sending
- 📈 **Gradual Scaling** — Day 1: 20 DMs → Day 2: 30 → Day 3: 40 → Day 4+: 50
- 🔄 **Proxy Rotation** — Residential/mobile proxy support, max 5 accounts per IP
- 🎭 **Fingerprint Management** — Unique device profile per account (UA, viewport, timezone)
- ✉️ **Message Personalization** — Spintext engine + Jinja2 templates with variables
- ⏱️ **Rate Limiting** — 10/hour, 50/day per account with automatic cooldowns
- 🛡️ **Block Detection** — Auto-pause on action blocks with exponential backoff
- 📊 **Campaign Management** — Full state machine with parallel account orchestration
- 💾 **SQLite Persistence** — Resume campaigns after restart, full audit trail
- 🎨 **Rich CLI** — Pretty tables, progress bars, color-coded output

## Architecture

```
┌─────────────────────────────────────────────────┐
│                    main.py (CLI)                 │
│         Click commands + Rich output             │
└───────────────────┬─────────────────────────────┘
                    │
┌───────────────────▼─────────────────────────────┐
│              campaign.py (Orchestrator)           │
│    State Machine: CREATED→WARMING→SCALING→ACTIVE  │
│         Manages parallel account tasks            │
└──┬──────┬──────┬──────┬──────┬──────┬───────────┘
   │      │      │      │      │      │
   ▼      ▼      ▼      ▼      ▼      ▼
┌─────┐┌─────┐┌─────┐┌─────┐┌─────┐┌─────┐
│Warm-││ DM  ││Scra-││Proxy││Rate ││Moni-│
│up   ││Disp.││per  ││Mgr  ││Limit││tor  │
│     ││     ││     ││     ││     ││     │
└──┬──┘└──┬──┘└──┬──┘└──┬──┘└──┬──┘└──┬──┘
   │      │      │      │      │      │
   └──────┴──────┴──────┼──────┴──────┘
                        │
         ┌──────────────▼──────────────┐
         │    session_manager.py       │
         │  Playwright + Stealth       │
         │  Cookie persistence         │
         │  Fingerprint per account    │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │         db.py (SQLite)       │
         │  accounts, targets, messages │
         │  campaigns, warmup_log       │
         └─────────────────────────────┘
```

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

## Configuration Guide

Copy `config.example.json` to `config.json` and customize:

### Accounts
```json
"accounts": [
  {
    "username": "your_ig_username",
    "password": "your_ig_password",
    "proxy": "http://user:pass@residential-proxy:port"
  }
]
```

### Warmup Settings
```json
"warmup": {
  "duration_hours": 48,          // 48-72h recommended
  "actions_per_session": 15,     // Actions per warmup session
  "session_gap_minutes": 120     // Gap between sessions
}
```

### DM Settings
```json
"dm": {
  "daily_limit": 50,             // Max DMs per day per account
  "hourly_limit": 10,            // Max DMs per hour per account
  "min_delay_seconds": 120,      // Min delay between DMs (2 min)
  "max_delay_seconds": 420,      // Max delay between DMs (7 min)
  "scaling_days": 4,             // Days to reach full volume
  "scaling_start": 20,           // DMs on day 1
  "scaling_increment": 10        // Daily increase
}
```

### Templates (Spintext + Variables)
```json
"templates": [
  "{Hi|Hey|Hello} {{ first_name }}! Loved your {{ niche }} content 🙌",
  "Hey {{ first_name }}, would love to connect about {{ niche }}!"
]
```

Available variables: `first_name`, `username`, `bio_keyword`, `niche`

Spintext: `{option1|option2|option3}` — randomly picks one

### Schedule
```json
"schedule": {
  "active_hours_start": 8,       // Start sending at 8 AM
  "active_hours_end": 23,        // Stop at 11 PM
  "days_off": ["Sunday"]         // No sending on Sundays
}
```

## Usage

### Add an Account
```bash
python main.py add-account myusername mypassword --proxy http://user:pass@host:port
```

### Import Targets
```bash
# From CSV (columns: username, full_name, bio, follower_count)
python main.py add-targets --csv targets.csv --campaign 1

# Scrape from Instagram account
python main.py add-targets --scrape competitor_account --campaign 1
```

### Create a Campaign
```bash
python main.py create-campaign "Q1 Outreach" \
  --niche "fitness" \
  --accounts "account1,account2" \
  --template 0
```

### Start a Campaign
```bash
python main.py start 1
```
The tool will automatically:
1. Warm up accounts (48-72h of human-like activity)
2. Gradually scale sending (20 → 30 → 40 → 50/day)
3. Send personalized DMs with random delays
4. Detect and handle blocks automatically

### Check Status
```bash
# All campaigns
python main.py status

# Specific campaign
python main.py status --campaign 1
```

### Pause / Resume
```bash
python main.py pause 1
python main.py resume 1
```

### Export Data
```bash
# Export sent messages
python main.py export --campaign 1 --type messages -o messages.csv

# Export targets
python main.py export --campaign 1 --type targets -o targets.csv
```

## Safety Guidelines

1. **Use residential/mobile proxies** — Datacenter proxies get detected instantly
2. **Don't skip warmup** — New accounts need 48-72h of normal activity
3. **Keep daily limits low** — 50/day max is already aggressive; 30 is safer
4. **Use multiple accounts** — Spread volume across accounts
5. **Max 5 accounts per proxy IP** — More triggers Instagram's fraud detection
6. **Vary your messages** — Use spintext and multiple templates
7. **Respect cooldowns** — If blocked, wait the full cooldown period
8. **Monitor blocks** — Set up webhook alerts (Discord/Telegram)
9. **Don't run 24/7** — Use schedule settings for realistic activity windows

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Login fails | Check credentials; ensure 2FA app is ready for code prompt |
| "Action blocked" | Account is rate-limited; tool auto-pauses with cooldown |
| Proxy errors | Run health check; ensure proxy supports HTTPS |
| No message button | Target may have DM restrictions; tool auto-skips |
| Playwright crash | Run `playwright install chromium` again |
| Database locked | Only run one instance at a time |

## Project Structure

```
instagram-dm-automator/
├── main.py                 # CLI entry point (Click + Rich)
├── config.json             # Your configuration (git-ignored)
├── config.example.json     # Template configuration
├── requirements.txt        # Python dependencies
├── src/
│   ├── session_manager.py  # Playwright browser + stealth + cookies
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

## License

MIT — Use responsibly.

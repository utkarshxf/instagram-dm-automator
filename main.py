"""CLI entry point for Instagram DM Automator."""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from rich.logging import RichHandler

from src.db import Database
from src.campaign import CampaignOrchestrator
from src.scraper import Scraper

console = Console()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(BASE_DIR, "config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Configure logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(console=console, rich_tracebacks=True),
        logging.FileHandler(os.path.join(LOG_DIR, f"automator_{datetime.now():%Y%m%d}.log")),
    ],
)
logger = logging.getLogger("ig-automator")


def load_config(path: str) -> dict:
    """Load JSON config file."""
    if not os.path.exists(path):
        console.print(f"[red]Config file not found: {path}[/red]")
        console.print("Copy config.example.json to config.json and edit it.")
        sys.exit(1)
    with open(path, "r") as f:
        return json.load(f)


def run_async(coro):
    """Run an async coroutine."""
    return asyncio.get_event_loop().run_until_complete(coro)


@click.group()
@click.option("--config", "-c", default=DEFAULT_CONFIG, help="Path to config JSON file")
@click.pass_context
def cli(ctx, config):
    """Instagram DM Automator — Safe, scalable cold outreach."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


@cli.command("add-account")
@click.argument("username")
@click.argument("password")
@click.option("--proxy", "-p", default="", help="Proxy URL (http://user:pass@host:port)")
@click.pass_context
def add_account(ctx, username, password, proxy):
    """Add an Instagram account."""
    async def _run():
        db = Database()
        await db.connect()
        try:
            aid = await db.add_account(username, password, proxy)
            if aid:
                console.print(f"[green]✓ Account @{username} added (ID: {aid})[/green]")
            else:
                console.print(f"[yellow]Account @{username} already exists[/yellow]")
        finally:
            await db.close()

    run_async(_run())


@cli.command("add-targets")
@click.option("--csv", "csv_path", default="", help="Import targets from CSV file")
@click.option("--scrape", "scrape_account", default="", help="Scrape followers of this account")
@click.option("--campaign", "campaign_id", default=0, type=int, help="Campaign ID to associate")
@click.pass_context
def add_targets(ctx, csv_path, scrape_account, campaign_id):
    """Import targets from CSV or scrape from Instagram."""
    config = load_config(ctx.obj["config_path"])

    async def _run():
        db = Database()
        await db.connect()
        try:
            if csv_path:
                scraper = Scraper(config, db)
                count = await scraper.import_from_csv(csv_path, campaign_id)
                console.print(f"[green]✓ Imported {count} targets from {csv_path}[/green]")
            elif scrape_account:
                scraper = Scraper(config, db)
                with Progress(console=console) as progress:
                    task = progress.add_task(f"Scraping @{scrape_account}...", total=None)
                    count = await scraper.scrape_and_save(scrape_account, campaign_id)
                    progress.update(task, completed=count)
                console.print(f"[green]✓ Scraped {count} targets from @{scrape_account}[/green]")
                await scraper.close()
            else:
                console.print("[red]Specify --csv or --scrape[/red]")
        finally:
            await db.close()

    run_async(_run())


@cli.command("create-campaign")
@click.argument("name")
@click.option("--template", "-t", default="", help="Message template (or index from config)")
@click.option("--niche", "-n", default="", help="Niche/industry keyword")
@click.option("--accounts", "-a", default="", help="Comma-separated account usernames")
@click.pass_context
def create_campaign(ctx, name, template, niche, accounts):
    """Create a new DM campaign."""
    config = load_config(ctx.obj["config_path"])

    async def _run():
        db = Database()
        await db.connect()
        try:
            # Resolve template
            tmpl = template
            if not tmpl:
                templates = config.get("templates", [])
                if templates:
                    tmpl = templates[0]
                    console.print(f"[dim]Using first template from config[/dim]")
            elif tmpl.isdigit():
                idx = int(tmpl)
                templates = config.get("templates", [])
                if 0 <= idx < len(templates):
                    tmpl = templates[idx]

            # Resolve accounts
            acct_list = [a.strip() for a in accounts.split(",") if a.strip()] if accounts else []
            if not acct_list:
                # Use all accounts from config
                acct_list = [a["username"] for a in config.get("accounts", [])]

            cid = await db.create_campaign(name, tmpl, niche, acct_list)
            console.print(f"[green]✓ Campaign '{name}' created (ID: {cid})[/green]")
            console.print(f"  Accounts: {', '.join(acct_list)}")
            console.print(f"  Template: {tmpl[:60]}...")
        finally:
            await db.close()

    run_async(_run())


@cli.command("start")
@click.argument("campaign_id", type=int)
@click.pass_context
def start_campaign(ctx, campaign_id):
    """Start a campaign (warmup → scale → send)."""
    config = load_config(ctx.obj["config_path"])

    async def _run():
        db = Database()
        await db.connect()
        orchestrator = CampaignOrchestrator(config, db)
        try:
            await orchestrator.start()
            console.print(f"[green]Starting campaign {campaign_id}...[/green]")
            await orchestrator.run_campaign(campaign_id)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — pausing campaign[/yellow]")
            await orchestrator.pause_campaign(campaign_id)
        finally:
            await orchestrator.stop()
            await db.close()

    run_async(_run())


@cli.command("status")
@click.option("--campaign", "campaign_id", default=0, type=int, help="Campaign ID (0 for all)")
@click.pass_context
def show_status(ctx, campaign_id):
    """Show campaign status, accounts, and stats."""
    config = load_config(ctx.obj["config_path"])

    async def _run():
        db = Database()
        await db.connect()
        try:
            if campaign_id:
                orchestrator = CampaignOrchestrator(config, db)
                stats = await orchestrator.get_campaign_stats(campaign_id)
                if not stats:
                    console.print(f"[red]Campaign {campaign_id} not found[/red]")
                    return
                _print_campaign_stats(stats)
            else:
                campaigns = await db.get_all_campaigns()
                if not campaigns:
                    console.print("[dim]No campaigns found[/dim]")
                    return

                table = Table(title="Campaigns")
                table.add_column("ID", style="cyan")
                table.add_column("Name", style="green")
                table.add_column("Status", style="yellow")
                table.add_column("Sent", justify="right")
                table.add_column("Failed", justify="right")
                table.add_column("Created")

                for c in campaigns:
                    table.add_row(
                        str(c["id"]), c["name"], c["status"],
                        str(c.get("total_sent", 0)), str(c.get("total_failed", 0)),
                        c.get("created_at", "")[:16],
                    )
                console.print(table)

                # Also show accounts
                accounts = await db.get_all_accounts()
                if accounts:
                    acct_table = Table(title="Accounts")
                    acct_table.add_column("Username", style="cyan")
                    acct_table.add_column("Status", style="yellow")
                    acct_table.add_column("Warmup", justify="right")
                    acct_table.add_column("Blocks", justify="right", style="red")
                    acct_table.add_column("Day", justify="right")

                    for a in accounts:
                        warmup = f"{a.get('total_warmup_hours', 0):.1f}h"
                        acct_table.add_row(
                            a["username"], a["status"], warmup,
                            str(a.get("block_count", 0)),
                            str(a.get("campaign_day", 0)),
                        )
                    console.print(acct_table)
        finally:
            await db.close()

    run_async(_run())


def _print_campaign_stats(stats: dict) -> None:
    """Pretty-print campaign statistics."""
    camp = stats["campaign"]
    console.print(f"\n[bold]Campaign #{camp['id']}: {camp['name']}[/bold]")
    console.print(f"  Status: [yellow]{camp['status']}[/yellow]")
    console.print(f"  Created: {camp['created_at'][:16]}")

    msg = stats["messages"]
    console.print(f"\n  [bold]Messages:[/bold]")
    console.print(f"    Sent:    [green]{msg['sent']}[/green]")
    console.print(f"    Failed:  [red]{msg['failed']}[/red]")
    console.print(f"    Blocked: [red]{msg['blocked']}[/red]")

    tgt = stats["targets"]
    console.print(f"\n  [bold]Targets:[/bold]")
    console.print(f"    Total:    {tgt['total']}")
    console.print(f"    Pending:  {tgt['pending']}")
    console.print(f"    Messaged: [green]{tgt['messaged']}[/green]")

    if stats["accounts"]:
        table = Table(title="Account Health")
        table.add_column("Account", style="cyan")
        table.add_column("Status")
        table.add_column("Day", justify="right")
        table.add_column("Blocks", justify="right")
        table.add_column("Hourly Left", justify="right")
        table.add_column("Daily Left", justify="right")

        for a in stats["accounts"]:
            status_style = "green" if a["status"] == "active" else "yellow" if a["status"] == "warming" else "red"
            table.add_row(
                a["username"],
                f"[{status_style}]{a['status']}[/{status_style}]",
                str(a.get("campaign_day", 0)),
                str(a.get("block_count", 0)),
                str(a.get("hourly_remaining", "?")),
                str(a.get("daily_remaining", "?")),
            )
        console.print(table)


@cli.command("pause")
@click.argument("campaign_id", type=int)
@click.pass_context
def pause_campaign(ctx, campaign_id):
    """Pause a running campaign."""
    async def _run():
        db = Database()
        await db.connect()
        try:
            await db.update_campaign(campaign_id, status="paused")
            console.print(f"[yellow]Campaign {campaign_id} paused[/yellow]")
        finally:
            await db.close()

    run_async(_run())


@cli.command("resume")
@click.argument("campaign_id", type=int)
@click.pass_context
def resume_campaign(ctx, campaign_id):
    """Resume a paused campaign."""
    config = load_config(ctx.obj["config_path"])

    async def _run():
        db = Database()
        await db.connect()
        orchestrator = CampaignOrchestrator(config, db)
        try:
            await orchestrator.start()
            await orchestrator.resume_campaign(campaign_id)
            console.print(f"[green]Campaign {campaign_id} resumed, continuing...[/green]")
            await orchestrator.run_campaign(campaign_id)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — pausing campaign[/yellow]")
            await orchestrator.pause_campaign(campaign_id)
        finally:
            await orchestrator.stop()
            await db.close()

    run_async(_run())


@cli.command("export")
@click.option("--campaign", "campaign_id", default=0, type=int, help="Campaign ID")
@click.option("--output", "-o", default="", help="Output CSV path")
@click.option("--type", "export_type", default="messages", type=click.Choice(["messages", "targets"]))
@click.pass_context
def export_data(ctx, campaign_id, output, export_type):
    """Export sent messages or targets to CSV."""
    config = load_config(ctx.obj["config_path"])

    async def _run():
        db = Database()
        await db.connect()
        try:
            if export_type == "targets":
                scraper = Scraper(config, db)
                out = output or os.path.join(BASE_DIR, "data", "targets_export.csv")
                count = await scraper.export_to_csv(out, campaign_id)
                console.print(f"[green]✓ Exported {count} targets to {out}[/green]")
            else:
                messages = await db.get_messages(campaign_id=campaign_id)
                out = output or os.path.join(BASE_DIR, "data", "messages_export.csv")
                import csv
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        "id", "campaign_id", "account_username", "target_username",
                        "message_content", "status", "error", "sent_at"
                    ], extrasaction="ignore")
                    writer.writeheader()
                    for m in messages:
                        writer.writerow(m)
                console.print(f"[green]✓ Exported {len(messages)} messages to {out}[/green]")
        finally:
            await db.close()

    run_async(_run())


if __name__ == "__main__":
    cli()

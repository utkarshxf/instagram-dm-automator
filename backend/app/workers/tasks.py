import asyncio
import logging
from arq import create_pool
from arq.connections import RedisSettings
from ..db.mongodb import MongoDB
from ..db.mongodb_adapter import MongoDBDatabase
from ..services.campaign_orchestrator import CampaignOrchestrator
from ..core.config import settings
from ..core.logging import setup_logging

# Initialize logging for the worker process
setup_logging()
logger = logging.getLogger("ig-automator.worker")

async def run_campaign(ctx, campaign_id: str, user_id: str):
    logger.info(f"Task started: run_campaign campaign_id={campaign_id} user_id={user_id}")
    
    try:
        if MongoDB.db is None:
            await MongoDB.connect()
        
        db_adapter = MongoDBDatabase(MongoDB.db, user_id)
        
        # Load config from file
        import json
        try:
            with open("config.json", "r") as f:
                config = json.load(f)
        except Exception:
            config = {
                "dm": {"hourly_limit": 10, "daily_limit": 50},
                "proxy": {"max_accounts_per_ip": 5, "rotation_enabled": True},
                "monitor": {"pause_on_block_hours": 72, "max_blocks_before_disable": 3},
                "schedule": {"active_hours_start": 0, "active_hours_end": 23},
                "templates": []
            }
        
        # Override schedule for testing if needed
        if "schedule" not in config:
            config["schedule"] = {}
        config["schedule"]["active_hours_start"] = 0
        config["schedule"]["active_hours_end"] = 23
        
        orchestrator = CampaignOrchestrator(config, db_adapter, user_id=user_id)
        
        await orchestrator.start()
        await orchestrator.run_campaign(campaign_id)
        logger.info(f"Task completed: run_campaign campaign_id={campaign_id} user_id={user_id}")
    except Exception as e:
        logger.exception(f"Task failed: run_campaign campaign_id={campaign_id} user_id={user_id} error={e}")
        raise # Arq handles retries if configured
    finally:
        if 'orchestrator' in locals():
            await orchestrator.stop()

async def startup(ctx):
    await MongoDB.connect()

async def shutdown(ctx):
    await MongoDB.close()

class WorkerSettings:
    functions = [run_campaign]
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD
    )
    on_startup = startup
    on_shutdown = shutdown
    # Production additions
    max_jobs = 10 # Control concurrency per worker
    job_timeout = 3600 * 24 # 24 hours max for a campaign run
    poll_delay = 1.0 # Poll for jobs more frequently for debugging
    keep_result = 3600 # Keep job result for an hour

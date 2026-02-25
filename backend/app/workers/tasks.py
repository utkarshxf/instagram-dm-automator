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
        
        # Load config from MongoDB
        settings_doc = await MongoDB.db.settings.find_one({"user_id": user_id})
        if settings_doc:
            # Convert MongoDB doc to dict and remove _id and user_id
            config = settings_doc
            if "_id" in config: del config["_id"]
            if "user_id" in config: del config["user_id"]
            if "updated_at" in config: del config["updated_at"]
        else:
            # Fallback to defaults if no settings found in DB
            from ..models.models import GlobalSettings
            config = GlobalSettings().dict()
        
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

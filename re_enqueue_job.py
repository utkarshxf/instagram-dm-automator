import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from backend.app.core.config import settings

async def start_camp():
    redis = await create_pool(RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD
    ))
    campaign_id = "699e181789d802feff89dcaf"
    user_id = "699e13ca9842311b8c304ee9"
    print(f"Enqueuing job for campaign {campaign_id}")
    job = await redis.enqueue_job("run_campaign", campaign_id=campaign_id, user_id=user_id)
    print(f"Job enqueued: {job.job_id}")
    await redis.close()

if __name__ == "__main__":
    asyncio.run(start_camp())

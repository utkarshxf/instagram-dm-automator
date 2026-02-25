from ..core.config import settings
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = settings.MONGO_URL
DB_NAME = settings.MONGO_DB

class MongoDB:
    client: AsyncIOMotorClient = None
    db = None

    @classmethod
    async def connect(cls):
        cls.client = AsyncIOMotorClient(MONGO_URL)
        cls.db = cls.client[DB_NAME]
        # Create indexes
        await cls.db.users.create_index("username", unique=True)
        await cls.db.accounts.create_index([("user_id", 1), ("username", 1)], unique=True)
        await cls.db.campaigns.create_index("user_id")
        await cls.db.targets.create_index([("user_id", 1), ("campaign_id", 1), ("username", 1)], unique=True)
        await cls.db.templates.create_index("user_id")
        await cls.db.settings.create_index("user_id", unique=True)

    @classmethod
    async def close(cls):
        if cls.client:
            cls.client.close()

async def get_db():
    return MongoDB.db

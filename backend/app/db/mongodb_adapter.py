from typing import List, Optional, Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone
import json

class MongoDBDatabase:
    """A drop-in replacement for SQLite Database class using MongoDB."""
    def __init__(self, db: AsyncIOMotorDatabase, user_id: str):
        self.db = db
        self.user_id = user_id

    async def get_account(self, username: str) -> Optional[dict]:
        return await self.db.accounts.find_one({"user_id": self.user_id, "username": username})

    async def update_account(self, username: str, **kwargs) -> None:
        kwargs["updated_at"] = datetime.now(timezone.utc)
        await self.db.accounts.update_one(
            {"user_id": self.user_id, "username": username},
            {"$set": kwargs}
        )

    async def get_campaign(self, campaign_id: Any) -> Optional[dict]:
        if isinstance(campaign_id, str):
            campaign_id = ObjectId(campaign_id)
        return await self.db.campaigns.find_one({"user_id": self.user_id, "_id": campaign_id})

    async def update_campaign(self, campaign_id: Any, **kwargs) -> None:
        if isinstance(campaign_id, str):
            campaign_id = ObjectId(campaign_id)
        kwargs["updated_at"] = datetime.now(timezone.utc)
        await self.db.campaigns.update_one(
            {"user_id": self.user_id, "_id": campaign_id},
            {"$set": kwargs}
        )

    async def get_pending_targets(self, campaign_id: Any, limit: int = 100) -> List[dict]:
        return await self.db.targets.find({
            "user_id": self.user_id,
            "campaign_id": str(campaign_id),
            "status": "pending"
        }).limit(limit).to_list(None)

    async def update_target_status(self, username: str, status: str) -> None:
        # Note: Targets are unique per user+campaign+username in our index
        await self.db.targets.update_one(
            {"user_id": self.user_id, "username": username},
            {"$set": {"status": status}}
        )

    async def log_message(self, campaign_id: Any, account_username: str, target_username: str, 
                         message_content: str, status: str, error: Optional[str] = None) -> None:
        await self.db.messages.insert_one({
            "user_id": self.user_id,
            "campaign_id": str(campaign_id),
            "account_username": account_username,
            "target_username": target_username,
            "message_content": message_content,
            "status": status,
            "error": error,
            "sent_at": datetime.now(timezone.utc)
        })

    async def get_rate_limit(self, account_username: str) -> Optional[dict]:
        return await self.db.rate_limits.find_one({"user_id": self.user_id, "account_username": account_username})

    async def upsert_rate_limit(self, account_username: str, hour_key: str, hour_count: int,
                                 day_key: str, day_count: int) -> None:
        await self.db.rate_limits.update_one(
            {"user_id": self.user_id, "account_username": account_username},
            {"$set": {
                "hour_key": hour_key,
                "hour_count": hour_count,
                "day_key": day_key,
                "day_count": day_count,
                "updated_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

    async def add_message_hash(self, account_username: str, message_hash: str) -> None:
        await self.db.message_history.insert_one({
            "user_id": self.user_id,
            "account_username": account_username,
            "message_hash": message_hash,
            "sent_at": datetime.now(timezone.utc)
        })

    async def get_last_message_hash(self, account_username: str) -> Optional[str]:
        doc = await self.db.message_history.find_one(
            {"user_id": self.user_id, "account_username": account_username},
            sort=[("sent_at", -1)]
        )
        return doc["message_hash"] if doc else None

    # Warmup and Proxy methods would follow similar pattern...
    async def log_warmup_action(self, account_username: str, action_type: str, details: str = "") -> None:
        await self.db.warmup_log.insert_one({
            "user_id": self.user_id,
            "account_username": account_username,
            "action_type": action_type,
            "details": details,
            "performed_at": datetime.now(timezone.utc)
        })

    async def get_warmup_actions_count(self, account_username: str) -> int:
        return await self.db.warmup_log.count_documents({"user_id": self.user_id, "account_username": account_username})

    async def get_accounts_on_proxy(self, proxy_ip: str) -> int:
        return await self.db.proxy_assignments.count_documents({"user_id": self.user_id, "proxy_ip": proxy_ip})

    async def assign_proxy(self, proxy_url: str, proxy_ip: str, account_username: str) -> None:
        await self.db.proxy_assignments.update_one(
            {"user_id": self.user_id, "account_username": account_username},
            {"$set": {
                "proxy_url": proxy_url,
                "proxy_ip": proxy_ip,
                "assigned_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

    async def get_proxy_for_account(self, account_username: str) -> Optional[str]:
        doc = await self.db.proxy_assignments.find_one({"user_id": self.user_id, "account_username": account_username})
        return doc["proxy_url"] if doc else None

    async def is_target_messaged(self, target_username: str, account_username: str) -> bool:
        doc = await self.db.messages.find_one({
            "user_id": self.user_id,
            "account_username": account_username,
            "target_username": target_username,
            "status": "sent"
        })
        return doc is not None

    async def is_account_available(self, account_username: str) -> bool:
        account = await self.get_account(account_username)
        if not account:
            return False
        # Simplified for now, in src it checked cooldown and status
        status = account.get("status")
        if status in ["disabled", "paused"]:
            return False
        
        cooldown_until = account.get("cooldown_until")
        if cooldown_until:
            if isinstance(cooldown_until, str):
                from datetime import datetime
                cooldown_dt = datetime.fromisoformat(cooldown_until)
            else:
                cooldown_dt = cooldown_until
                
            if datetime.now(timezone.utc) < cooldown_dt:
                return False
                
        return True

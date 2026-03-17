from typing import List, Optional, Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from datetime import datetime, timezone

class BaseService:
    def __init__(self, db: AsyncIOMotorDatabase, user_id: str):
        self.db = db
        self.user_id = user_id

class AccountService(BaseService):
    async def get_account(self, username: str) -> Optional[dict]:
        return await self.db.accounts.find_one({"user_id": self.user_id, "username": username})

    async def update_account(self, username: str, **kwargs):
        kwargs["updated_at"] = datetime.now(timezone.utc)
        await self.db.accounts.update_one(
            {"user_id": self.user_id, "username": username},
            {"$set": kwargs}
        )

    async def get_active_accounts(self) -> List[dict]:
        return await self.db.accounts.find({"user_id": self.user_id, "status": {"$in": ["active", "warming"]}}).to_list(None)

class CampaignService(BaseService):
    async def get_campaign(self, campaign_id: str) -> Optional[dict]:
        return await self.db.campaigns.find_one({"user_id": self.user_id, "_id": ObjectId(campaign_id)})

    async def update_campaign(self, campaign_id: str, **kwargs):
        kwargs["updated_at"] = datetime.now(timezone.utc)
        await self.db.campaigns.update_one(
            {"user_id": self.user_id, "_id": ObjectId(campaign_id)},
            {"$set": kwargs}
        )

    async def get_pending_targets(self, campaign_id: str, limit: int = 100) -> List[dict]:
        targets = await self.db.targets.find({
            "user_id": self.user_id,
            "campaign_id": campaign_id,
            "status": "pending"
        }).limit(limit).to_list(None)

        if len(targets) >= limit:
            return targets

        # Check attached lead campaigns
        campaign = await self.get_campaign(campaign_id)
        if campaign and campaign.get("lead_campaign_ids"):
            lead_limit = limit - len(targets)
            lead_campaign_ids = campaign["lead_campaign_ids"]
            leads = await self.db.leads.find({
                "user_id": self.user_id,
                "campaign_id": {"$in": lead_campaign_ids}
            }).limit(lead_limit * 2).to_list(None)

            for lead in leads:
                if len(targets) >= limit:
                    break
                exists = await self.db.targets.find_one({
                    "user_id": self.user_id,
                    "campaign_id": campaign_id,
                    "username": lead["username"]
                })
                if not exists:
                    target_doc = {
                        "user_id": self.user_id,
                        "campaign_id": campaign_id,
                        "username": lead["username"],
                        "full_name": lead.get("full_name", ""),
                        "bio": lead.get("bio", ""),
                        "status": "pending",
                        "created_at": datetime.now(timezone.utc)
                    }
                    try:
                        await self.db.targets.insert_one(target_doc)
                        target_doc["_id"] = str(target_doc["_id"])
                        targets.append(target_doc)
                    except Exception:
                        pass
        return targets

    async def update_target_status(self, target_username: str, campaign_id: str, status: str):
        await self.db.targets.update_one(
            {"user_id": self.user_id, "campaign_id": campaign_id, "username": target_username},
            {"$set": {"status": status}}
        )

    async def log_message(self, campaign_id: str, account_username: str, target_username: str, 
                         message_content: str, status: str, error: Optional[str] = None):
        log_entry = {
            "user_id": self.user_id,
            "campaign_id": campaign_id,
            "account_username": account_username,
            "target_username": target_username,
            "message_content": message_content,
            "status": status,
            "error": error,
            "sent_at": datetime.now(timezone.utc)
        }
        await self.db.messages.insert_one(log_entry)

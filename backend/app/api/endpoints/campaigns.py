from fastapi import APIRouter, Depends, HTTPException, Request
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from ...models.models import CampaignCreate, Campaign
from datetime import datetime
from bson import ObjectId
from typing import List

router = APIRouter()

@router.post("/", response_model=Campaign)
async def create_campaign(campaign_in: CampaignCreate, user = Depends(get_current_user), db = Depends(get_db)):
    campaign_dict = campaign_in.dict()
    campaign_dict["user_id"] = user["id"]
    campaign_dict["status"] = "created"
    campaign_dict["total_sent"] = 0
    campaign_dict["total_failed"] = 0
    campaign_dict["total_targets"] = 0
    campaign_dict["created_at"] = datetime.utcnow()
    campaign_dict["updated_at"] = datetime.utcnow()
    
    result = await db.campaigns.insert_one(campaign_dict)
    campaign_dict["_id"] = str(result.inserted_id)
    return campaign_dict

@router.get("/", response_model=List[dict])
async def list_campaigns(user = Depends(get_current_user), db = Depends(get_db)):
    campaigns = await db.campaigns.find({"user_id": user["id"]}).to_list(100)
    for camp in campaigns:
        camp["_id"] = str(camp["_id"])
    return campaigns

@router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: str, request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    campaign = await db.campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    # Enqueue task to Arq/Redis
    await request.app.state.arq_redis.enqueue_job("run_campaign", campaign_id=campaign_id, user_id=user["id"])
    
    await db.campaigns.update_one(
        {"_id": ObjectId(campaign_id)},
        {"$set": {"status": "queued", "updated_at": datetime.utcnow()}}
    )
    
    return {"status": "queued", "campaign_id": campaign_id}

@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    result = await db.campaigns.delete_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"status": "deleted"}

@router.post("/{campaign_id}/targets")
async def add_targets(campaign_id: str, targets: List[dict], user = Depends(get_current_user), db = Depends(get_db)):
    # Check if campaign exists and belongs to user
    campaign = await db.campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    for t in targets:
        t["user_id"] = user["id"]
        t["campaign_id"] = campaign_id
        t["status"] = "pending"
        t["created_at"] = datetime.utcnow()
    
    # Use insert_many for efficiency
    if targets:
        try:
            await db.targets.insert_many(targets, ordered=False)
        except Exception:
            # Some might fail due to unique index (user_id, campaign_id, username)
            pass
            
    # Update total_targets in campaign
    total = await db.targets.count_documents({"campaign_id": campaign_id})
    await db.campaigns.update_one({"_id": ObjectId(campaign_id)}, {"$set": {"total_targets": total}})
    
    return {"status": "added", "count": len(targets)}

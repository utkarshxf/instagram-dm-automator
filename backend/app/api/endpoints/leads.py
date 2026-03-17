from fastapi import APIRouter, Depends, HTTPException, Request
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from ...models.models import LeadCampaignCreate, LeadCampaign
from datetime import datetime
from bson import ObjectId
from typing import List

router = APIRouter()

@router.post("/", response_model=LeadCampaign)
async def create_lead_campaign(campaign_in: LeadCampaignCreate, user = Depends(get_current_user), db = Depends(get_db)):
    campaign_dict = campaign_in.dict()
    campaign_dict["user_id"] = user["id"]
    campaign_dict["status"] = "created"
    campaign_dict["total_leads"] = 0
    campaign_dict["processed_count"] = 0
    campaign_dict["created_at"] = datetime.utcnow()
    campaign_dict["updated_at"] = datetime.utcnow()
    
    result = await db.lead_campaigns.insert_one(campaign_dict)
    campaign_dict["_id"] = str(result.inserted_id)
    return campaign_dict

@router.get("/", response_model=List[dict])
async def list_lead_campaigns(user = Depends(get_current_user), db = Depends(get_db)):
    campaigns = await db.lead_campaigns.find({"user_id": user["id"]}).to_list(100)
    for camp in campaigns:
        camp["_id"] = str(camp["_id"])
    return campaigns

@router.get("/{campaign_id}", response_model=dict)
async def get_lead_campaign(campaign_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    campaign = await db.lead_campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    if not campaign:
        raise HTTPException(status_code=404, detail="Lead Campaign not found")
    campaign["_id"] = str(campaign["_id"])
    return campaign

@router.post("/{campaign_id}/start")
async def start_lead_extraction(campaign_id: str, request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    campaign = await db.lead_campaigns.find_one({"_id": ObjectId(campaign_id), "user_id": user["id"]})
    if not campaign:
        raise HTTPException(status_code=404, detail="Lead Campaign not found")
    
    # Enqueue task
    await request.app.state.arq_redis.enqueue_job("run_lead_extraction", campaign_id=campaign_id, user_id=user["id"])
    
    await db.lead_campaigns.update_one(
        {"_id": ObjectId(campaign_id)},
        {"$set": {"status": "running", "updated_at": datetime.utcnow()}}
    )
    
    return {"status": "running", "campaign_id": campaign_id}

@router.post("/{campaign_id}/pause")
async def pause_lead_extraction(campaign_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    result = await db.lead_campaigns.update_one(
        {"_id": ObjectId(campaign_id), "user_id": user["id"]},
        {"$set": {"status": "paused", "updated_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Lead Campaign not found")
    return {"status": "paused"}

@router.post("/{campaign_id}/stop")
async def stop_lead_extraction(campaign_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    result = await db.lead_campaigns.update_one(
        {"_id": ObjectId(campaign_id), "user_id": user["id"]},
        {"$set": {"status": "stopped", "updated_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Lead Campaign not found")
    return {"status": "stopped"}

@router.get("/{campaign_id}/leads", response_model=List[dict])
async def list_leads(campaign_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    leads = await db.leads.find({"campaign_id": campaign_id, "user_id": user["id"]}).to_list(1000)
    for lead in leads:
        lead["_id"] = str(lead["_id"])
    return leads

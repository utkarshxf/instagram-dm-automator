from fastapi import APIRouter, Depends, HTTPException
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from ...models.models import GlobalSettings
from datetime import datetime
from typing import Any

router = APIRouter()

@router.get("/", response_model=GlobalSettings)
async def get_settings(user = Depends(get_current_user), db = Depends(get_db)):
    settings_doc = await db.settings.find_one({"user_id": user["id"]})
    if not settings_doc:
        # Return default settings
        return GlobalSettings()
    return settings_doc

@router.post("/", response_model=GlobalSettings)
async def update_settings(settings_in: GlobalSettings, user = Depends(get_current_user), db = Depends(get_db)):
    settings_dict = settings_in.dict()
    settings_dict["user_id"] = user["id"]
    settings_dict["updated_at"] = datetime.utcnow()
    
    await db.settings.update_one(
        {"user_id": user["id"]},
        {"$set": settings_dict},
        upsert=True
    )
    return settings_dict

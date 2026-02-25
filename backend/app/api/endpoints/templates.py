from fastapi import APIRouter, Depends, HTTPException, status
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from ...models.models import TemplateCreate, Template
from datetime import datetime
from bson import ObjectId
from typing import List

router = APIRouter()

@router.post("/", response_model=Template)
async def create_template(template_in: TemplateCreate, user = Depends(get_current_user), db = Depends(get_db)):
    template_dict = template_in.dict()
    template_dict["user_id"] = user["id"]
    template_dict["created_at"] = datetime.utcnow()
    template_dict["updated_at"] = datetime.utcnow()
    
    result = await db.templates.insert_one(template_dict)
    template_dict["_id"] = str(result.inserted_id)
    return template_dict

@router.get("/", response_model=List[dict])
async def list_templates(user = Depends(get_current_user), db = Depends(get_db)):
    templates = await db.templates.find({"user_id": user["id"]}).to_list(100)
    for t in templates:
        t["_id"] = str(t["_id"])
    return templates

@router.delete("/{template_id}")
async def delete_template(template_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    result = await db.templates.delete_one({"_id": ObjectId(template_id), "user_id": user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"status": "deleted"}

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from ...models.models import TemplateCreate, Template
from ...services.firebase_service import upload_image
from datetime import datetime
from bson import ObjectId
from typing import List, Optional
import uuid

router = APIRouter()

@router.post("/", response_model=Template)
async def create_template(
    name: str = Form(...),
    content: str = Form(...),
    image: Optional[UploadFile] = File(None),
    user = Depends(get_current_user), 
    db = Depends(get_db)
):
    image_url = None
    if image:
        content_type = image.content_type
        if content_type not in ["image/jpeg", "image/png"]:
            raise HTTPException(status_code=400, detail="Only JPEG and PNG images are supported")
        
        file_content = await image.read()
        extension = "jpg" if content_type == "image/jpeg" else "png"
        filename = f"{uuid.uuid4()}.{extension}"
        
        try:
            image_url = await upload_image(file_content, filename)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload image: {str(e)}")

    template_dict = {
        "name": name,
        "content": content,
        "image_url": image_url,
        "user_id": user["id"],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
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

from fastapi import APIRouter, Depends, HTTPException, status
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from ...models.models import InstagramAccountCreate, InstagramAccount
from datetime import datetime
from bson import ObjectId
from typing import List

router = APIRouter()

@router.post("/", response_model=InstagramAccount)
async def add_account(account_in: InstagramAccountCreate, user = Depends(get_current_user), db = Depends(get_db)):
    account_exists = await db.accounts.find_one({"user_id": user["id"], "username": account_in.username})
    if account_exists:
        raise HTTPException(status_code=400, detail="Account already added")
    
    account_dict = account_in.dict()
    account_dict["user_id"] = user["id"]
    account_dict["status"] = "new"
    account_dict["created_at"] = datetime.utcnow()
    account_dict["updated_at"] = datetime.utcnow()
    
    result = await db.accounts.insert_one(account_dict)
    account_dict["_id"] = str(result.inserted_id)
    return account_dict

@router.get("/", response_model=List[dict]) # Use dict because of _id/id mapping in return
async def list_accounts(user = Depends(get_current_user), db = Depends(get_db)):
    accounts = await db.accounts.find({"user_id": user["id"]}).to_list(100)
    for acct in accounts:
        acct["_id"] = str(acct["_id"])
    return accounts

@router.delete("/{username}")
async def delete_account(username: str, user = Depends(get_current_user), db = Depends(get_db)):
    result = await db.accounts.delete_one({"user_id": user["id"], "username": username})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "deleted"}

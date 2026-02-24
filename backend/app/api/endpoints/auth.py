from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from ...db.mongodb import get_db
from ...core.auth import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from ...models.models import UserCreate, Token, User
from datetime import timedelta
from bson import ObjectId

router = APIRouter()

@router.post("/register", response_model=User)
async def register(user_in: UserCreate, db = Depends(get_db)):
    user_exists = await db.users.find_one({"username": user_in.username})
    if user_exists:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    user_dict = user_in.dict()
    user_dict["password"] = get_password_hash(user_dict["password"])
    user_dict["created_at"] = ObjectId().generation_time # Or use datetime.utcnow()
    
    result = await db.users.insert_one(user_dict)
    user_dict["_id"] = str(result.inserted_id)
    return user_dict

@router.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db = Depends(get_db)):
    user = await db.users.find_one({"username": form_data.username})
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "id": str(user["_id"])},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str = Field(..., max_length=72)

class User(UserBase):
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None
    user_id: Optional[str] = None

class InstagramAccountBase(BaseModel):
    username: str
    proxy: Optional[str] = None
    secret_key: Optional[str] = None

class InstagramAccountCreate(InstagramAccountBase):
    password: str = Field(..., max_length=72)

class InstagramAccount(InstagramAccountBase):
    id: str = Field(alias="_id")
    user_id: str
    status: str = "new"
    created_at: datetime
    updated_at: datetime

class CampaignBase(BaseModel):
    name: str
    template: str
    niche: Optional[str] = ""
    accounts: List[str] = []

class CampaignCreate(CampaignBase):
    pass

class Campaign(CampaignBase):
    id: str = Field(alias="_id")
    user_id: str
    status: str = "created"
    total_sent: int = 0
    total_failed: int = 0
    total_targets: int = 0
    created_at: datetime
    updated_at: datetime

class TargetBase(BaseModel):
    username: str
    full_name: Optional[str] = ""
    bio: Optional[str] = ""

class Target(TargetBase):
    id: str = Field(alias="_id")
    user_id: str
    campaign_id: str
    status: str = "pending"
    created_at: datetime

class TemplateBase(BaseModel):
    name: str
    content: str

class TemplateCreate(TemplateBase):
    pass

class Template(TemplateBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime

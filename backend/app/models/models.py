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
    lead_campaign_ids: List[str] = []

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
    image_url: Optional[str] = None

class TemplateCreate(TemplateBase):
    pass

class Template(TemplateBase):
    id: str = Field(alias="_id")
    user_id: str
    created_at: datetime
    updated_at: datetime

class WarmupSettings(BaseModel):
    duration_hours: int = 0
    actions_per_session: int = 15
    session_gap_minutes: int = 120

class DMSettings(BaseModel):
    daily_limit: int = 50
    hourly_limit: int = 10
    min_delay_seconds: int = 120
    max_delay_seconds: int = 420
    scaling_days: int = 4
    scaling_start: int = 20
    scaling_increment: int = 10

class ScrapingSettings(BaseModel):
    targets_per_run: int = 500
    min_followers: int = 100
    max_followers: int = 50000
    has_bio: bool = True
    is_private: bool = False

class ProxySettings(BaseModel):
    max_accounts_per_ip: int = 5
    rotation_enabled: bool = True

class MonitorSettings(BaseModel):
    webhook_url: Optional[str] = ""
    pause_on_block_hours: int = 72
    max_blocks_before_disable: int = 3

class ScheduleSettings(BaseModel):
    active_hours_start: int = 8
    active_hours_end: int = 23
    days_off: List[str] = ["Sunday"]

class LeadCampaignBase(BaseModel):
    name: str
    seed_profile: str  # Seed username or post URL
    filters: dict = {
        "min_followers": 0,
        "max_followers": 1000000,
        "bio_keywords": [],
        "required_bio_links": [],
        "content_topics": [],
        "interaction_filters": {}
    }
    accounts: List[str] = []
    dm_campaign_id: Optional[str] = None

class LeadCampaignCreate(LeadCampaignBase):
    pass

class LeadCampaign(LeadCampaignBase):
    id: str = Field(alias="_id")
    user_id: str
    status: str = "created"  # created, running, paused, stopped, completed
    total_leads: int = 0
    processed_count: int = 0
    created_at: datetime
    updated_at: datetime

class LeadBase(BaseModel):
    username: str
    profile_url: str
    follower_count: int = 0
    bio: Optional[str] = ""
    full_name: Optional[str] = ""
    is_private: bool = False
    external_url: Optional[str] = None

class Lead(LeadBase):
    id: str = Field(alias="_id")
    user_id: str
    campaign_id: str
    created_at: datetime

class GlobalSettings(BaseModel):
    warmup: WarmupSettings = WarmupSettings()
    dm: DMSettings = DMSettings()
    scraping: ScrapingSettings = ScrapingSettings()
    proxy: ProxySettings = ProxySettings()
    monitor: MonitorSettings = MonitorSettings()
    schedule: ScheduleSettings = ScheduleSettings()

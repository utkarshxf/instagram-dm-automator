from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .api.endpoints import auth, accounts, campaigns, templates, dashboard, settings as api_settings
from .db.mongodb import MongoDB
from .core.config import settings
from .core.logging import setup_logging
import os
from arq import create_pool
from arq.connections import RedisSettings

# Initialize logging
setup_logging()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

tags_metadata = [
    {"name": "auth", "description": "Authentication: register and obtain JWT tokens."},
    {"name": "accounts", "description": "Manage Instagram accounts per user."},
    {"name": "campaigns", "description": "Create and manage campaigns, add targets, start jobs."},
    {"name": "templates", "description": "Manage reusable message templates."},
    {"name": "settings", "description": "Global settings for campaigns and automation."},
    {"name": "dashboard", "description": "Metrics and activity data for the overview page."},
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Multi-tenant backend for Instagram DM Automator",
    openapi_tags=tags_metadata,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Set up Rate Limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Set up CORS
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.on_event("startup")
async def startup_db_client():
    await MongoDB.connect()
    app.state.arq_redis = await create_pool(RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD
    ))

@app.on_event("shutdown")
async def shutdown_db_client():
    await MongoDB.close()
    await app.state.arq_redis.close()

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
app.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
app.include_router(templates.router, prefix="/templates", tags=["templates"])
app.include_router(api_settings.router, prefix="/settings", tags=["settings"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])

@app.get("/")
async def root():
    return {"message": "Welcome to Instagram DM Automator API"}

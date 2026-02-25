from fastapi import APIRouter, Depends
from ...db.mongodb import get_db
from ...core.auth import get_current_user
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

router = APIRouter()

@router.get("/")
async def get_dashboard_stats(user = Depends(get_current_user), db = Depends(get_db)):
    user_id = user["id"]
    
    # 1. Active Accounts count
    active_accounts = await db.accounts.count_documents({
        "user_id": user_id,
        "status": "active"
    })
    
    # 2. Active Campaigns count (status is 'running')
    active_campaigns = await db.campaigns.count_documents({
        "user_id": user_id,
        "status": "running"
    })
    
    # 3. Total Messages Sent count
    # We can sum total_sent from campaigns or count from messages collection
    # Summing from campaigns is more efficient if it's updated correctly
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": None, "total": {"$sum": "$total_sent"}}}
    ]
    campaign_sent_result = await db.campaigns.aggregate(pipeline).to_list(1)
    total_sent = campaign_sent_result[0]["total"] if campaign_sent_result else 0
    
    # 4. Sending Activity (last 7 days)
    # Get daily counts of messages with status 'sent'
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    
    pipeline = [
        {
            "$match": {
                "user_id": user_id,
                "status": "sent",
                "sent_at": {"$gte": seven_days_ago}
            }
        },
        {
            "$project": {
                "day": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$sent_at"}
                }
            }
        },
        {
            "$group": {
                "_id": "$day",
                "total": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ]
    
    activity_results = await db.messages.aggregate(pipeline).to_list(None)
    
    # Fill in missing days with 0
    activity_data = []
    for i in range(7):
        day = (datetime.now(timezone.utc) - timedelta(days=6-i)).strftime("%Y-%m-%d")
        day_name = (datetime.now(timezone.utc) - timedelta(days=6-i)).strftime("%a")
        
        match = next((item for item in activity_results if item["_id"] == day), None)
        count = match["total"] if match else 0
        
        activity_data.append({
            "name": day_name,
            "date": day,
            "total": count
        })
        
    return {
        "active_accounts": active_accounts,
        "active_campaigns": active_campaigns,
        "total_messages_sent": total_sent,
        "sending_activity": activity_data
    }

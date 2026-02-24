from backend.app.db.mongodb import MongoDB
import asyncio

async def check_targets():
    await MongoDB.connect()
    campaign_id = "699e181789d802feff89dcaf"
    targets = await MongoDB.db.targets.find({"campaign_id": campaign_id}).to_list(None)
    print(f"Total targets: {len(targets)}")
    for t in targets:
        print(f"Target: {t['username']}, Status: {t['status']}")
    
    # Reset all to pending
    result = await MongoDB.db.targets.update_many(
        {"campaign_id": campaign_id},
        {"$set": {"status": "pending"}}
    )
    print(f"Reset {result.modified_count} targets to 'pending'")
    
    # Also reset campaign to queued
    await MongoDB.db.campaigns.update_one(
        {"_id": "699e181789d802feff89dcaf"},
        {"$set": {"status": "queued"}}
    )
    
    await MongoDB.close()

if __name__ == "__main__":
    asyncio.run(check_targets())

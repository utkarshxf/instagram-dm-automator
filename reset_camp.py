from backend.app.db.mongodb import MongoDB
import asyncio

async def reset_camp():
    await MongoDB.connect()
    result = await MongoDB.db.campaigns.update_one(
        {"name": "camp1"},
        {"$set": {"status": "queued"}}
    )
    if result.modified_count:
        print("Campaign 'camp1' reset to 'queued'")
    else:
        print("Campaign 'camp1' not found or already in 'queued'")
    await MongoDB.close()

if __name__ == "__main__":
    asyncio.run(reset_camp())

from backend.app.db.mongodb import MongoDB
import asyncio
from bson import ObjectId

async def check():
    await MongoDB.connect()
    camp = await MongoDB.db.campaigns.find_one({"name": "camp1"})
    if camp:
        print(f"Status: {camp['status']}")
        print(f"ID: {camp['_id']}")
        print(f"User ID: {camp['user_id']}")
        print(f"Accounts: {camp['accounts']}")
    else:
        print("Campaign 'camp1' not found")
    await MongoDB.close()

if __name__ == "__main__":
    asyncio.run(check())

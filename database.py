from motor.motor_asyncio import AsyncIOMotorClient
import config

client = AsyncIOMotorClient(config.MONGO_URL) if config.MONGO_URL else None
db = client["movie_blogger_bot"] if client is not None else None


async def get_setting(key: str, default=None):
    if db is None:
        return default
    doc = await db.settings.find_one({"_id": key})
    return doc if doc else default


async def set_setting(key: str, data: dict):
    if db is None:
        return
    await db.settings.update_one({"_id": key}, {"$set": data}, upsert=True)

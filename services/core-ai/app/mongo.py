from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings


async def create_mongo_client() -> AsyncIOMotorClient:
    return AsyncIOMotorClient(settings.mongo_url)


def get_mongo_db(client: AsyncIOMotorClient = Depends(create_mongo_client)) -> AsyncIOMotorDatabase:
    return client[settings.mongo_db_name]


@asynccontextmanager
async def lifespan_mongo() -> AsyncIterator[AsyncIOMotorDatabase]:
    client = await create_mongo_client()
    db = client[settings.mongo_db_name]
    try:
        yield db
    finally:
        client.close()

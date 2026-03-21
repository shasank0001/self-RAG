import asyncio
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models import Bin, ChatMessage, ChatSession, IngestionJob, Item, TelemetryEvent, User


async def check() -> None:
    async with SessionLocal() as session:
        counts = {
            "users": await session.scalar(select(func.count(User.id))),
            "bins": await session.scalar(select(func.count(Bin.id))),
            "items": await session.scalar(select(func.count(Item.id))),
            "ingestion_jobs": await session.scalar(select(func.count(IngestionJob.id))),
            "chat_sessions": await session.scalar(select(func.count(ChatSession.id))),
            "chat_messages": await session.scalar(select(func.count(ChatMessage.id))),
            "telemetry_events": await session.scalar(select(func.count(TelemetryEvent.id))),
        }

    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    asyncio.run(check())

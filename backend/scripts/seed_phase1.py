import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models import Bin, ChatMessage, ChatSession, IngestionJob, IngestionStatus, Item, ItemSourceType, TelemetryEvent, User
from app.models.chat_message import MessageRole


async def seed() -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.clerk_user_id == "user_phase1_demo"))
        if user is None:
            user = User(clerk_user_id="user_phase1_demo", email="demo@example.com")
            session.add(user)
            await session.flush()

        bin_obj = await session.scalar(select(Bin).where(Bin.user_id == user.id, Bin.title == "Starter Bin"))
        if bin_obj is None:
            bin_obj = Bin(user_id=user.id, title="Starter Bin", description="Phase 1 seed bin", vector_namespace="starter-bin")
            session.add(bin_obj)
            await session.flush()

        item = await session.scalar(select(Item).where(Item.bin_id == bin_obj.id, Item.source_name == "seed-doc.txt"))
        if item is None:
            item = Item(
                user_id=user.id,
                bin_id=bin_obj.id,
                source_type=ItemSourceType.TEXT,
                source_name="seed-doc.txt",
                media_type="text/plain",
                raw_text="Seed placeholder content for ingestion checks.",
            )
            session.add(item)
            await session.flush()

        job = await session.scalar(select(IngestionJob).where(IngestionJob.bin_id == bin_obj.id))
        if job is None:
            job = IngestionJob(
                user_id=user.id,
                bin_id=bin_obj.id,
                item_id=item.id,
                source_name="seed-doc.txt",
                status=IngestionStatus.SUCCEEDED,
            )
            session.add(job)

        chat_session = await session.scalar(select(ChatSession).where(ChatSession.user_id == user.id, ChatSession.title == "Seed Chat"))
        if chat_session is None:
            chat_session = ChatSession(
                user_id=user.id,
                title="Seed Chat",
                initial_bin_ids=[bin_obj.id],
                last_active_bin_ids=[bin_obj.id],
            )
            session.add(chat_session)
            await session.flush()

        first_message = await session.scalar(select(ChatMessage).where(ChatMessage.session_id == chat_session.id))
        if first_message is None:
            session.add_all(
                [
                    ChatMessage(
                        session_id=chat_session.id,
                        user_id=user.id,
                        role=MessageRole.USER,
                        content="What does the seeded document contain?",
                        bin_ids_used=[bin_obj.id],
                        citations=[],
                    ),
                    ChatMessage(
                        session_id=chat_session.id,
                        user_id=user.id,
                        role=MessageRole.ASSISTANT,
                        content="The seed document is a placeholder for Phase 1 schema checks.",
                        bin_ids_used=[bin_obj.id],
                        citations=[{"item_name": "seed-doc.txt", "chunk_excerpt": "placeholder", "bin_title": "Starter Bin"}],
                    ),
                ]
            )

        telemetry = await session.scalar(select(TelemetryEvent).where(TelemetryEvent.event_type == "phase1.seed.completed"))
        if telemetry is None:
            session.add(
                TelemetryEvent(
                    user_id=user.id,
                    event_type="phase1.seed.completed",
                    payload={"status": "ok", "component": "seed_phase1"},
                )
            )

        await session.commit()
    print("Phase 1 seed completed")


if __name__ == "__main__":
    asyncio.run(seed())

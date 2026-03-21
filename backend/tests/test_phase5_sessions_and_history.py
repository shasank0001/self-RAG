from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_update_session_bins_route_updates_last_active_bin_ids() -> None:
    from app.api.v1.routes import sessions as sessions_routes
    from app.models.user import User

    user = User(id=uuid4(), clerk_user_id="user_bins", email="bins@example.com")
    record = SimpleNamespace(
        id=uuid4(),
        user_id=user.id,
        title="Session",
        initial_bin_ids=[],
        last_active_bin_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    class FakeSession:
        def add(self, _obj):
            return None

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    fake_session = FakeSession()
    new_bins = [uuid4(), uuid4()]

    async def fake_require_session_owner(_session, *, session_id, owner_user_id):
        assert session_id == record.id
        assert owner_user_id == user.id
        return record

    sessions_routes.require_session_owner = fake_require_session_owner  # type: ignore[assignment]

    response = await sessions_routes.update_session_bins(
        session_id=record.id,
        payload=sessions_routes.SessionBinsUpdateRequest(bin_ids=new_bins),
        session=fake_session,  # type: ignore[arg-type]
        current_user=user,
    )

    assert response.last_active_bin_ids == new_bins

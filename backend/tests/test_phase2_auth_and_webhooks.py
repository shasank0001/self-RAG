from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound

import app.api.v1.routes.messages as messages_routes
import app.api.v1.routes.webhooks as webhooks_routes
import app.auth.dependencies as auth_dependencies
from app.auth.clerk import ClerkClaims
from app.auth.clerk import ClerkTokenVerifier
from app.auth.dependencies import get_current_user
from app.core.errors import UnauthorizedError
from app.db.session import get_db_session
from app.main import create_app
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.services.clerk_webhooks import process_clerk_user_lifecycle_event
from app.services.user_sync import get_or_create_user_from_claims


class FakeSession:
    def __init__(self) -> None:
        self.committed = False

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    def begin_nested(self):
        class _Nested:
            async def __aenter__(self):
                return None

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        return _Nested()

    async def execute(self, _statement):
        class FakeResult:
            def scalar_one_or_none(self):
                return None

            def scalar_one(self):
                raise RuntimeError("not implemented")

        return FakeResult()


class InMemoryResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        if self.value is None:
            raise NoResultFound("no row found")
        return self.value


class InMemoryLifecycleSession:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.webhook_events: dict[str, WebhookEvent] = {}

    def begin_nested(self):
        class _Nested:
            async def __aenter__(self):
                return None

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        return _Nested()

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is User:
            clerk_user_id = self._extract_where_value(statement, "clerk_user_id")
            return InMemoryResult(self.users.get(cast(str, clerk_user_id)))

        if entity is WebhookEvent:
            svix_id = self._extract_where_value(statement, "svix_id")
            existing = self.webhook_events.get(cast(str, svix_id))
            if statement.column_descriptions[0].get("name") == "id":
                return InMemoryResult(existing.id if existing else None)
            return InMemoryResult(existing)

        return InMemoryResult(None)

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

        if isinstance(obj, User):
            self.users[obj.clerk_user_id] = obj
        elif isinstance(obj, WebhookEvent):
            self.webhook_events[obj.svix_id] = obj

    async def flush(self) -> None:
        return None

    @staticmethod
    def _extract_where_value(statement, column_name: str):
        for criterion in statement._where_criteria:  # noqa: SLF001
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            if getattr(left, "name", None) == column_name:
                return getattr(right, "value", None)
        return None


def make_client(fake_session: FakeSession) -> TestClient:
    app = create_app()

    async def override_db_session():
        yield fake_session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def test_missing_bearer_token_returns_401() -> None:
    fake_session = FakeSession()
    with make_client(fake_session) as client:
        response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"


def test_invalid_bearer_token_returns_401(monkeypatch) -> None:
    def fake_verify(_token: str):
        raise UnauthorizedError(code="invalid_token", message="Invalid authentication token")

    monkeypatch.setattr(auth_dependencies, "verify_clerk_token", fake_verify)

    fake_session = FakeSession()
    with make_client(fake_session) as client:
        response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_valid_bearer_token_returns_authenticated_subject(monkeypatch) -> None:
    def fake_verify(_token: str) -> ClerkClaims:
        return ClerkClaims(sub="user_123", email="user@example.com", raw={"sub": "user_123"})

    monkeypatch.setattr(auth_dependencies, "verify_clerk_token", fake_verify)

    fake_session = FakeSession()
    with make_client(fake_session) as client:
        response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer good-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "user_123"
    assert body["clerk_user_id"] == "user_123"


def test_clerk_verifier_uses_explicit_jwks_url(monkeypatch) -> None:
    verifier = ClerkTokenVerifier()
    captured: dict[str, str] = {}

    class FakePyJWKClient:
        def __init__(self, url: str) -> None:
            captured["url"] = url

        def get_signing_key_from_jwt(self, _token: str):
            return SimpleNamespace(key="signing-key")

    monkeypatch.setattr(
        "app.auth.clerk.get_settings",
        lambda: SimpleNamespace(
            clerk_jwks_url="https://example.clerk.accounts.dev/custom-jwks.json",
            clerk_issuer="https://example.clerk.accounts.dev",
            clerk_audience="",
        ),
    )
    monkeypatch.setattr("app.auth.clerk.jwt.PyJWKClient", FakePyJWKClient)
    monkeypatch.setattr(
        "app.auth.clerk.jwt.decode",
        lambda *_args, **_kwargs: {"sub": "user_123", "email": "user@example.com"},
    )

    claims = verifier.verify_clerk_token("good-token")

    assert claims.sub == "user_123"
    assert captured["url"] == "https://example.clerk.accounts.dev/custom-jwks.json"


def test_clerk_verifier_derives_jwks_url_from_issuer(monkeypatch) -> None:
    verifier = ClerkTokenVerifier()
    captured: dict[str, str] = {}

    class FakePyJWKClient:
        def __init__(self, url: str) -> None:
            captured["url"] = url

        def get_signing_key_from_jwt(self, _token: str):
            return SimpleNamespace(key="signing-key")

    monkeypatch.setattr(
        "app.auth.clerk.get_settings",
        lambda: SimpleNamespace(
            clerk_jwks_url="",
            clerk_issuer="https://example.clerk.accounts.dev/",
            clerk_audience="",
        ),
    )
    monkeypatch.setattr("app.auth.clerk.jwt.PyJWKClient", FakePyJWKClient)
    monkeypatch.setattr(
        "app.auth.clerk.jwt.decode",
        lambda *_args, **_kwargs: {"sub": "user_456", "email": "derived@example.com"},
    )

    claims = verifier.verify_clerk_token("good-token")

    assert claims.sub == "user_456"
    assert captured["url"] == "https://example.clerk.accounts.dev/.well-known/jwks.json"


def test_create_bin_ignores_client_supplied_owner_id() -> None:
    fake_session = FakeSession()
    app_user = User(id=uuid4(), clerk_user_id="user_123", email="user@example.com")
    app = create_app()

    async def override_db_session():
        yield fake_session

    async def override_current_user() -> User:
        return app_user

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/bins",
            json={
                "title": "Private Bin",
                "description": "Scoped data",
                "vector_namespace": "bin-private",
                "user_id": str(uuid4()),
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(app_user.id)


def test_create_session_ignores_client_supplied_owner_id() -> None:
    fake_session = FakeSession()
    app_user = User(id=uuid4(), clerk_user_id="user_456", email="session@example.com")
    app = create_app()

    async def override_db_session():
        yield fake_session

    async def override_current_user() -> User:
        return app_user

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sessions",
            json={
                "title": "Session",
                "initial_bin_ids": [],
                "last_active_bin_ids": [],
                "user_id": str(uuid4()),
            },
        )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(app_user.id)


def test_cross_user_bin_access_is_denied() -> None:
    fake_session = FakeSession()
    app_user = User(id=uuid4(), clerk_user_id="user_owner", email="owner@example.com")
    app = create_app()

    async def override_db_session():
        yield fake_session

    async def override_current_user() -> User:
        return app_user

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user

    with TestClient(app) as client:
        response = client.get(f"/api/v1/bins/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_create_message_ignores_client_supplied_owner_id(monkeypatch) -> None:
    fake_session = FakeSession()
    app_user = User(id=uuid4(), clerk_user_id="user_789", email="message@example.com")
    app = create_app()

    async def override_db_session():
        yield fake_session

    async def override_current_user() -> User:
        return app_user

    async def allow_session(*_args, **_kwargs):
        return SimpleNamespace(id=uuid4(), user_id=app_user.id)

    monkeypatch.setattr(messages_routes, "_require_owned_session", allow_session)

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            json={
                "role": "user",
                "content": "hello",
                "bin_ids_used": [],
                "citations": [],
                "user_id": str(uuid4()),
            },
        )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(app_user.id)


def test_webhook_invalid_signature_returns_400(monkeypatch) -> None:
    class FakeWebhook:
        def __init__(self, _secret: str) -> None:
            return None

        def verify(self, _payload: bytes, _headers: dict[str, str]):
            raise Exception("invalid")

    monkeypatch.setattr(webhooks_routes, "get_settings", lambda: SimpleNamespace(clerk_webhook_secret="whsec_test"))
    monkeypatch.setattr(webhooks_routes, "Webhook", FakeWebhook)
    monkeypatch.setattr(webhooks_routes, "WebhookVerificationError", Exception)

    fake_session = FakeSession()
    with make_client(fake_session) as client:
        response = client.post(
            "/webhooks/clerk",
            content=b"{}",
            headers={
                "svix-id": "msg_1",
                "svix-timestamp": "1234",
                "svix-signature": "v1,bad",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "webhook_invalid_signature"


def test_webhook_duplicate_delivery_is_idempotent(monkeypatch) -> None:
    class FakeWebhook:
        def __init__(self, _secret: str) -> None:
            return None

        def verify(self, _payload: bytes, _headers: dict[str, str]):
            return {
                "type": "user.created",
                "data": {
                    "id": "user_123",
                    "email_addresses": [{"id": "em_1", "email_address": "user@example.com"}],
                    "primary_email_address_id": "em_1",
                },
            }

    async def duplicate_event(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(webhooks_routes, "get_settings", lambda: SimpleNamespace(clerk_webhook_secret="whsec_test"))
    monkeypatch.setattr(webhooks_routes, "Webhook", FakeWebhook)
    monkeypatch.setattr(webhooks_routes, "process_clerk_user_lifecycle_event", duplicate_event)

    fake_session = FakeSession()
    with make_client(fake_session) as client:
        response = client.post(
            "/webhooks/clerk",
            content=b"{}",
            headers={
                "svix-id": "msg_1",
                "svix-timestamp": "1234",
                "svix-signature": "v1,good",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "duplicate": True}


def test_webhook_valid_signature_is_accepted(monkeypatch) -> None:
    class FakeWebhook:
        def __init__(self, _secret: str) -> None:
            return None

        def verify(self, _payload: bytes, _headers: dict[str, str]):
            return {
                "type": "user.updated",
                "data": {
                    "id": "user_123",
                    "email_addresses": [{"id": "em_1", "email_address": "user@example.com"}],
                    "primary_email_address_id": "em_1",
                },
            }

    async def process_event(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(webhooks_routes, "get_settings", lambda: SimpleNamespace(clerk_webhook_secret="whsec_test"))
    monkeypatch.setattr(webhooks_routes, "Webhook", FakeWebhook)
    monkeypatch.setattr(webhooks_routes, "process_clerk_user_lifecycle_event", process_event)

    fake_session = FakeSession()
    with make_client(fake_session) as client:
        response = client.post(
            "/webhooks/clerk",
            content=b"{}",
            headers={
                "svix-id": "msg_2",
                "svix-timestamp": "1234",
                "svix-signature": "v1,good",
                "content-type": "application/json",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "duplicate": False}


async def test_get_or_create_user_from_claims_is_deterministic() -> None:
    session = InMemoryLifecycleSession()
    claims = ClerkClaims(sub="user_abc", email="first@example.com", raw={"sub": "user_abc"})

    first = await get_or_create_user_from_claims(cast(Any, session), claims)
    second = await get_or_create_user_from_claims(cast(Any, session), claims)

    assert first.created is True
    assert second.created is False
    assert first.user.id == second.user.id


async def test_user_lifecycle_events_sync_and_idempotency() -> None:
    session = InMemoryLifecycleSession()

    created_payload = {
        "type": "user.created",
        "data": {
            "id": "user_xyz",
            "email_addresses": [{"id": "em_1", "email_address": "created@example.com"}],
            "primary_email_address_id": "em_1",
        },
    }
    updated_payload = {
        "type": "user.updated",
        "data": {
            "id": "user_xyz",
            "email_addresses": [{"id": "em_2", "email_address": "updated@example.com"}],
            "primary_email_address_id": "em_2",
        },
    }
    deleted_payload = {
        "type": "user.deleted",
        "data": {
            "id": "user_xyz",
        },
    }

    created = await process_clerk_user_lifecycle_event(
        cast(Any, session),
        svix_id="svix_create",
        event_type="user.created",
        payload=created_payload,
    )
    assert created is True
    assert session.users["user_xyz"].email == "created@example.com"
    assert session.users["user_xyz"].is_active is True

    updated = await process_clerk_user_lifecycle_event(
        cast(Any, session),
        svix_id="svix_update",
        event_type="user.updated",
        payload=updated_payload,
    )
    assert updated is True
    assert session.users["user_xyz"].email == "updated@example.com"

    deleted = await process_clerk_user_lifecycle_event(
        cast(Any, session),
        svix_id="svix_delete",
        event_type="user.deleted",
        payload=deleted_payload,
    )
    assert deleted is True
    assert session.users["user_xyz"].is_active is False
    assert session.users["user_xyz"].deleted_at is not None

    duplicate = await process_clerk_user_lifecycle_event(
        cast(Any, session),
        svix_id="svix_delete",
        event_type="user.deleted",
        payload=deleted_payload,
    )
    assert duplicate is False

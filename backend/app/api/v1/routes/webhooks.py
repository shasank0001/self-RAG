import logging
from importlib import import_module
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

try:
    svix_webhooks = import_module("svix.webhooks")
    Webhook: Any = svix_webhooks.Webhook
    WebhookVerificationError = svix_webhooks.WebhookVerificationError
except ModuleNotFoundError:  # pragma: no cover
    Webhook: Any = None
    WebhookVerificationError = Exception

from app.core.config import get_settings
from app.core.errors import BadRequestError, WebhookSignatureError
from app.db.session import get_db_session
from app.services.clerk_webhooks import process_clerk_user_lifecycle_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


class WebhookAcceptedResponse(BaseModel):
    accepted: bool
    duplicate: bool = False


def _svix_headers(request: Request) -> dict[str, str]:
    header_mapping = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }
    if not all(header_mapping.values()):
        raise BadRequestError(code="webhook_missing_headers", message="Missing Svix headers")
    return {key: str(value) for key, value in header_mapping.items()}


@router.post(
    "/clerk",
    response_model=WebhookAcceptedResponse,
    summary="Receive Clerk webhooks",
    description="Validates Svix signature using raw request body and processes user lifecycle events idempotently.",
)
async def clerk_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> WebhookAcceptedResponse:
    settings = get_settings()
    if not settings.clerk_webhook_secret:
        raise BadRequestError(code="webhook_config_error", message="Webhook secret is not configured")

    payload_bytes = await request.body()
    headers = _svix_headers(request)

    try:
        if Webhook is None:
            raise RuntimeError("svix package is not installed")
        payload = Webhook(settings.clerk_webhook_secret).verify(payload_bytes, headers)
    except RuntimeError as exc:
        logger.error("svix dependency is not available for webhook verification")
        raise BadRequestError(code="webhook_verifier_unavailable", message="Webhook verifier is unavailable") from exc
    except WebhookVerificationError as exc:
        logger.warning("clerk webhook signature verification failed")
        raise WebhookSignatureError() from exc

    event_type = payload.get("type")
    svix_id = headers["svix-id"]
    if not isinstance(event_type, str):
        raise BadRequestError(code="webhook_invalid_payload", message="Webhook payload missing event type")

    duplicate = False
    if event_type in {"user.created", "user.updated", "user.deleted"}:
        duplicate = not await process_clerk_user_lifecycle_event(
            session,
            svix_id=svix_id,
            event_type=event_type,
            payload=payload,
        )
        await session.commit()

    return WebhookAcceptedResponse(accepted=True, duplicate=duplicate)

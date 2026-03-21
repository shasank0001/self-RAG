import logging
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import InvalidTokenError
from jwt.exceptions import PyJWKClientError

from app.core.config import get_settings
from app.core.errors import UnauthorizedError

logger = logging.getLogger(__name__)


@dataclass
class ClerkClaims:
    sub: str
    email: str | None
    raw: dict[str, Any]


class ClerkTokenVerifier:
    def __init__(self) -> None:
        self._jwks_clients: dict[str, jwt.PyJWKClient] = {}

    @staticmethod
    def _resolve_jwks_url() -> str:
        settings = get_settings()
        if settings.clerk_jwks_url:
            return settings.clerk_jwks_url

        if settings.clerk_issuer:
            return f"{settings.clerk_issuer.rstrip('/')}/.well-known/jwks.json"

        raise UnauthorizedError(code="auth_config_error", message="Auth verifier misconfigured")

    def _get_jwks_client(self) -> jwt.PyJWKClient:
        jwks_url = self._resolve_jwks_url()
        if not jwks_url:
            raise UnauthorizedError(code="auth_config_error", message="Auth verifier misconfigured")

        if jwks_url not in self._jwks_clients:
            self._jwks_clients[jwks_url] = jwt.PyJWKClient(jwks_url)
        return self._jwks_clients[jwks_url]

    def verify_clerk_token(self, token: str) -> ClerkClaims:
        settings = get_settings()

        if not token:
            raise UnauthorizedError(code="missing_token", message="Authentication token missing")

        issuer = settings.clerk_issuer or None
        audience = settings.clerk_audience or None

        try:
            signing_key = self._get_jwks_client().get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=audience,
                options={"verify_aud": bool(audience), "require": ["sub"]},
            )
        except UnauthorizedError:
            raise
        except PyJWKClientError as exc:
            logger.warning("unable to fetch jwks during token verification")
            raise UnauthorizedError(code="invalid_token", message="Invalid authentication token") from exc
        except InvalidTokenError as exc:
            raise UnauthorizedError(code="invalid_token", message="Invalid authentication token") from exc

        if "sub" not in payload:
            raise UnauthorizedError(code="invalid_token", message="Invalid authentication token")

        email = payload.get("email")
        if email is None:
            email_addresses = payload.get("email_addresses") or []
            if isinstance(email_addresses, list) and email_addresses:
                first = email_addresses[0]
                if isinstance(first, dict):
                    email = first.get("email_address")

        return ClerkClaims(sub=str(payload["sub"]), email=email, raw=payload)


verifier = ClerkTokenVerifier()


def verify_clerk_token(token: str) -> ClerkClaims:
    return verifier.verify_clerk_token(token)

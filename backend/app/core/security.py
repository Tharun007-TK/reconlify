import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, status, Request
import httpx
import structlog
from app.config import settings

logger = structlog.get_logger(__name__)


class UserRole(StrEnum):
    INTERNAL_ADMIN = "internal_admin"
    CA_FIRM_ADMIN = "ca_firm_admin"
    AUDITOR = "auditor"


class UserPayload:
    def __init__(self, user_id: uuid.UUID, client_id: uuid.UUID, role: UserRole):
        self.user_id = user_id
        self.client_id = client_id
        self.role = role

    def require_role(self, required_role: UserRole) -> None:
        if self.role != required_role:
            # Check hierarchy: INTERNAL_ADMIN can do anything, CA_FIRM_ADMIN can do AUDITOR
            roles = [UserRole.AUDITOR, UserRole.CA_FIRM_ADMIN, UserRole.INTERNAL_ADMIN]
            try:
                if roles.index(self.role) < roles.index(required_role):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Requires {required_role} role."
                    )
            except ValueError:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


async def get_current_user(request: Request) -> UserPayload:
    """Mock authentication dependency for Recko v4."""
    # In a real app, this would verify a Supabase JWT from the Authorization header
    # and decode the user_id, client_id, and role from the app_metadata.
    
    # Using a dummy client for development
    return UserPayload(
        user_id=uuid.uuid4(),
        client_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        role=UserRole.AUDITOR,
    )


CurrentUser = Annotated[UserPayload, Depends(get_current_user)]


async def verify_turnstile_token(request: Request) -> None:
    """
    FastAPI Dependency to validate Cloudflare Turnstile tokens.
    Extracts the token from the X-Turnstile-Token header.
    """
    token = request.headers.get("X-Turnstile-Token")
    if not token:
        logger.warning("security.turnstile.missing_token", ip=request.client.host)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing Turnstile token. Bot verification required."
        )

    if settings.ENVIRONMENT == "development" and token == "dummy-token":
        return

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": getattr(settings, "TURNSTILE_SECRET_KEY", "dummy"),
                    "response": token,
                    "remoteip": request.client.host
                },
                timeout=5.0
            )
            result = response.json()
            if not result.get("success"):
                logger.warning("security.turnstile.failed", ip=request.client.host)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Turnstile challenge failed."
                )
        except httpx.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify captcha due to upstream network issue."
            )

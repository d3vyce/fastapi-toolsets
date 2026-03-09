import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import selectinload

from fastapi_toolsets.exceptions import UnauthorizedError
from fastapi_toolsets.security import (
    APIKeyHeaderAuth,
    BearerTokenAuth,
    CookieAuth,
    MultiAuth,
)

from .crud import UserCrud, UserTokenCrud
from .db import get_db_context
from .models import User, UserRole, UserToken
from .schemas import UserTokenCreate

SESSION_COOKIE = "session"
SECRET_KEY = "123456789"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _verify_token(token: str, role: UserRole | None = None) -> User:
    async with get_db_context() as db:
        user_token = await UserTokenCrud.first(
            session=db,
            filters=[UserToken.token_hash == _hash_token(token)],
            load_options=[selectinload(UserToken.user)],
        )

        if user_token is None or not user_token.user.is_active:
            raise UnauthorizedError()

        if user_token.expires_at and user_token.expires_at < datetime.now(timezone.utc):
            raise UnauthorizedError()

        user = user_token.user

        if role is not None and user.role != role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        return user


async def _verify_cookie(user_id: str, role: UserRole | None = None) -> User:
    async with get_db_context() as db:
        user = await UserCrud.first(
            session=db,
            filters=[User.id == UUID(user_id)],
        )

    if not user or not user.is_active:
        raise UnauthorizedError()

    if role is not None and user.role != role:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return user


bearer_auth = BearerTokenAuth(
    validator=_verify_token,
    prefix="ctf_",
)
header_auth = APIKeyHeaderAuth(
    name="X-API-Key",
    validator=_verify_token,
)
cookie_auth = CookieAuth(
    name=SESSION_COOKIE,
    validator=_verify_cookie,
    secret_key=SECRET_KEY,
)
auth = MultiAuth(bearer_auth, header_auth, cookie_auth)


async def create_api_token(
    user_id: UUID,
    *,
    name: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, UserToken]:
    raw = bearer_auth.generate_token()
    async with get_db_context() as db:
        token_row = await UserTokenCrud.create(
            session=db,
            obj=UserTokenCreate(
                user_id=user_id,
                token_hash=_hash_token(raw),
                name=name,
                expires_at=expires_at,
            ),
        )
    return raw, token_row

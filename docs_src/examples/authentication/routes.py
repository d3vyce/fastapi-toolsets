from typing import Annotated
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Form, HTTPException, Response, Security

from fastapi_toolsets.dependencies import PathDependency

from .crud import UserCrud, UserTokenCrud
from .db import SessionDep
from .models import OAuthProvider, User, UserToken
from .schemas import (
    ApiTokenCreateRequest,
    ApiTokenResponse,
    RegisterRequest,
    UserCreate,
    UserResponse,
)
from .security import auth, cookie_auth, create_api_token

ProviderDep = PathDependency(
    model=OAuthProvider,
    field=OAuthProvider.slug,
    session_dep=SessionDep,
    param_name="slug",
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


router = APIRouter(prefix="/auth")


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(body: RegisterRequest, session: SessionDep):
    existing = await UserCrud.first(
        session=session, filters=[User.username == body.username]
    )
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")

    user = await UserCrud.create(
        session=session,
        obj=UserCreate(
            username=body.username,
            email=body.email,
            hashed_password=hash_password(body.password),
        ),
    )
    return user


@router.post("/token", status_code=204)
async def login(
    session: SessionDep,
    response: Response,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    user = await UserCrud.first(session=session, filters=[User.username == username])

    if (
        not user
        or not user.hashed_password
        or not verify_password(password, user.hashed_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    cookie_auth.set_cookie(response, str(user.id))


@router.post("/logout", status_code=204)
async def logout(response: Response):
    cookie_auth.delete_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Security(auth)):
    return user


@router.post("/tokens", response_model=ApiTokenResponse, status_code=201)
async def create_token(
    body: ApiTokenCreateRequest,
    user: User = Security(auth),
):
    raw, token_row = await create_api_token(
        user.id, name=body.name, expires_at=body.expires_at
    )
    return ApiTokenResponse(
        id=token_row.id,
        name=token_row.name,
        expires_at=token_row.expires_at,
        created_at=token_row.created_at,
        token=raw,
    )


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    session: SessionDep,
    token_id: UUID,
    user: User = Security(auth),
):
    if not await UserTokenCrud.first(
        session=session,
        filters=[UserToken.id == token_id, UserToken.user_id == user.id],
    ):
        raise HTTPException(status_code=404, detail="Token not found")
    await UserTokenCrud.delete(
        session=session,
        filters=[UserToken.id == token_id, UserToken.user_id == user.id],
    )

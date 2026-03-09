from datetime import datetime
from uuid import UUID

from pydantic import EmailStr

from fastapi_toolsets.schemas import PydanticBase


class RegisterRequest(PydanticBase):
    username: str
    password: str
    email: EmailStr | None = None


class UserResponse(PydanticBase):
    id: UUID
    username: str
    email: str | None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class ApiTokenCreateRequest(PydanticBase):
    name: str | None = None
    expires_at: datetime | None = None


class ApiTokenResponse(PydanticBase):
    id: UUID
    name: str | None
    expires_at: datetime | None
    created_at: datetime
    # Only populated on creation
    token: str | None = None

    model_config = {"from_attributes": True}


class OAuthProviderResponse(PydanticBase):
    slug: str
    name: str

    model_config = {"from_attributes": True}


class UserCreate(PydanticBase):
    username: str
    email: str | None = None
    hashed_password: str | None = None


class UserTokenCreate(PydanticBase):
    user_id: UUID
    token_hash: str
    name: str | None = None
    expires_at: datetime | None = None


class OAuthAccountCreate(PydanticBase):
    user_id: UUID
    provider_id: UUID
    subject: str

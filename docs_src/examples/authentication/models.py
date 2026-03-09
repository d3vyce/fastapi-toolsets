import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastapi_toolsets.models import TimestampMixin, UUIDMixin


class Base(DeclarativeBase, UUIDMixin):
    type_annotation_map = {
        str: String(),
        int: Integer(),
        UUID: PG_UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
    }


class UserRole(enum.Enum):
    admin = "admin"
    moderator = "moderator"
    user = "user"


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    users: Mapped[list["User"]] = relationship(back_populates="team")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(
        String, unique=True, index=True, nullable=True
    )
    hashed_password: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user)

    team_id: Mapped[UUID | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team: Mapped["Team | None"] = relationship(back_populates="users")
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(back_populates="user")
    tokens: Mapped[list["UserToken"]] = relationship(back_populates="user")


class UserToken(Base, TimestampMixin):
    """API tokens for a user (multiple allowed)."""

    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    # Store hashed token value
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="tokens")


class OAuthProvider(Base, TimestampMixin):
    """Configurable OAuth2 / OpenID Connect provider."""

    __tablename__ = "oauth_providers"

    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    client_id: Mapped[str] = mapped_column(String)
    client_secret: Mapped[str] = mapped_column(String)
    discovery_url: Mapped[str] = mapped_column(String, nullable=False)
    scopes: Mapped[str] = mapped_column(String, default="openid email profile")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    accounts: Mapped[list["OAuthAccount"]] = relationship(back_populates="provider")


class OAuthAccount(Base, TimestampMixin):
    """OAuth2 / OpenID Connect account linked to a user."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider_id", "subject", name="uq_oauth_provider_subject"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("oauth_providers.id"))
    # OAuth `sub` / OpenID subject identifier
    subject: Mapped[str] = mapped_column(String)

    user: Mapped["User"] = relationship(back_populates="oauth_accounts")
    provider: Mapped["OAuthProvider"] = relationship(back_populates="accounts")

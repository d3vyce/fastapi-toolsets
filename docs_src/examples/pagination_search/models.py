import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastapi_toolsets.models import CreatedAtMixin


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="category")


class Article(Base, CreatedAtMixin):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )

    category: Mapped["Category | None"] = relationship(back_populates="articles")

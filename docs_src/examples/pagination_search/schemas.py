import datetime
import uuid

from fastapi_toolsets.schemas import PydanticBase


class ArticleRead(PydanticBase):
    id: uuid.UUID
    created_at: datetime.datetime
    title: str
    status: str
    published: bool
    category_id: uuid.UUID | None

from typing import Annotated

from fastapi import APIRouter, Depends

from fastapi_toolsets.schemas import (
    CursorPaginatedResponse,
    OffsetPaginatedResponse,
    PaginatedResponse,
)

from .crud import ArticleCrud
from .db import SessionDep
from .models import Article
from .schemas import ArticleRead

router = APIRouter(prefix="/articles")


@router.get("/offset")
async def list_articles_offset(
    session: SessionDep,
    params: Annotated[
        dict,
        Depends(
            ArticleCrud.offset_paginate_params(
                default_page_size=20,
                max_page_size=100,
                default_order_field=Article.created_at,
            )
        ),
    ],
) -> OffsetPaginatedResponse[ArticleRead]:
    return await ArticleCrud.offset_paginate(
        session=session,
        **params,
        schema=ArticleRead,
    )


@router.get("/cursor")
async def list_articles_cursor(
    session: SessionDep,
    params: Annotated[
        dict,
        Depends(
            ArticleCrud.cursor_paginate_params(
                default_page_size=20,
                max_page_size=100,
                default_order_field=Article.created_at,
            )
        ),
    ],
) -> CursorPaginatedResponse[ArticleRead]:
    return await ArticleCrud.cursor_paginate(
        session=session,
        **params,
        schema=ArticleRead,
    )


@router.get("/")
async def list_articles(
    session: SessionDep,
    params: Annotated[
        dict,
        Depends(
            ArticleCrud.paginate_params(
                default_page_size=20,
                max_page_size=100,
                default_order_field=Article.created_at,
            )
        ),
    ],
) -> PaginatedResponse[ArticleRead]:
    return await ArticleCrud.paginate(
        session,
        **params,
        schema=ArticleRead,
    )

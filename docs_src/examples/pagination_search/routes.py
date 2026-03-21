from typing import Annotated

from fastapi import APIRouter, Depends

from fastapi_toolsets.crud import OrderByClause
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
        Depends(ArticleCrud.offset_params(default_page_size=20, max_page_size=100)),
    ],
    filter_by: Annotated[dict[str, list[str]], Depends(ArticleCrud.filter_params())],
    order_by: Annotated[
        OrderByClause | None,
        Depends(ArticleCrud.order_params(default_field=Article.created_at)),
    ],
    search: str | None = None,
) -> OffsetPaginatedResponse[ArticleRead]:
    return await ArticleCrud.offset_paginate(
        session=session,
        **params,
        search=search,
        filter_by=filter_by or None,
        order_by=order_by,
        schema=ArticleRead,
    )


@router.get("/cursor")
async def list_articles_cursor(
    session: SessionDep,
    params: Annotated[
        dict,
        Depends(ArticleCrud.cursor_params(default_page_size=20, max_page_size=100)),
    ],
    filter_by: Annotated[dict[str, list[str]], Depends(ArticleCrud.filter_params())],
    order_by: Annotated[
        OrderByClause | None,
        Depends(ArticleCrud.order_params(default_field=Article.created_at)),
    ],
    search: str | None = None,
) -> CursorPaginatedResponse[ArticleRead]:
    return await ArticleCrud.cursor_paginate(
        session=session,
        **params,
        search=search,
        filter_by=filter_by or None,
        order_by=order_by,
        schema=ArticleRead,
    )


@router.get("/")
async def list_articles(
    session: SessionDep,
    params: Annotated[
        dict,
        Depends(ArticleCrud.paginate_params(default_page_size=20, max_page_size=100)),
    ],
    filter_by: Annotated[dict[str, list[str]], Depends(ArticleCrud.filter_params())],
    order_by: Annotated[
        OrderByClause | None,
        Depends(ArticleCrud.order_params(default_field=Article.created_at)),
    ],
    search: str | None = None,
) -> PaginatedResponse[ArticleRead]:
    return await ArticleCrud.paginate(
        session,
        **params,
        search=search,
        filter_by=filter_by or None,
        order_by=order_by,
        schema=ArticleRead,
    )

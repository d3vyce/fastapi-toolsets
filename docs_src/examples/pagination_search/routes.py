from typing import Annotated

from fastapi import APIRouter, Depends, Query

from fastapi_toolsets.crud import OrderByClause, PaginationType
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
    filter_by: Annotated[dict[str, list[str]], Depends(ArticleCrud.filter_params())],
    order_by: Annotated[
        OrderByClause | None,
        Depends(ArticleCrud.order_params(default_field=Article.created_at)),
    ],
    page: int = Query(1, ge=1),
    items_per_page: int = Query(20, ge=1, le=100),
    search: str | None = None,
) -> OffsetPaginatedResponse[ArticleRead]:
    return await ArticleCrud.offset_paginate(
        session=session,
        page=page,
        items_per_page=items_per_page,
        search=search,
        filter_by=filter_by or None,
        order_by=order_by,
        schema=ArticleRead,
    )


@router.get("/cursor")
async def list_articles_cursor(
    session: SessionDep,
    filter_by: Annotated[dict[str, list[str]], Depends(ArticleCrud.filter_params())],
    order_by: Annotated[
        OrderByClause | None,
        Depends(ArticleCrud.order_params(default_field=Article.created_at)),
    ],
    cursor: str | None = None,
    items_per_page: int = Query(20, ge=1, le=100),
    search: str | None = None,
) -> CursorPaginatedResponse[ArticleRead]:
    return await ArticleCrud.cursor_paginate(
        session=session,
        cursor=cursor,
        items_per_page=items_per_page,
        search=search,
        filter_by=filter_by or None,
        order_by=order_by,
        schema=ArticleRead,
    )


@router.get("/")
async def list_articles(
    session: SessionDep,
    filter_by: Annotated[dict[str, list[str]], Depends(ArticleCrud.filter_params())],
    order_by: Annotated[
        OrderByClause | None,
        Depends(ArticleCrud.order_params(default_field=Article.created_at)),
    ],
    pagination_type: PaginationType = PaginationType.OFFSET,
    page: int = Query(1, ge=1),
    cursor: str | None = None,
    items_per_page: int = Query(20, ge=1, le=100),
    search: str | None = None,
) -> PaginatedResponse[ArticleRead]:
    return await ArticleCrud.paginate(
        session,
        pagination_type=pagination_type,
        page=page,
        cursor=cursor,
        items_per_page=items_per_page,
        search=search,
        filter_by=filter_by or None,
        order_by=order_by,
        schema=ArticleRead,
    )

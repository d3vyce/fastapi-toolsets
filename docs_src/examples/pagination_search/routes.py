from fastapi import APIRouter, Depends, Query

from fastapi_toolsets.schemas import PaginatedResponse

from .crud import ArticleCrud
from .db import SessionDep
from .schemas import ArticleRead

router = APIRouter(prefix="/articles")


@router.get("/offset")
async def list_articles_offset(
    session: SessionDep,
    page: int = Query(1, ge=1),
    items_per_page: int = Query(20, ge=1, le=100),
    search: str | None = None,
    filter_by: dict[str, list[str]] = Depends(ArticleCrud.filter_params()),
) -> PaginatedResponse[ArticleRead]:
    return await ArticleCrud.offset_paginate(
        session=session,
        page=page,
        items_per_page=items_per_page,
        search=search,
        filter_by=filter_by or None,
        schema=ArticleRead,
    )


@router.get("/cursor")
async def list_articles_cursor(
    session: SessionDep,
    cursor: str | None = None,
    items_per_page: int = Query(20, ge=1, le=100),
    search: str | None = None,
    filter_by: dict[str, list[str]] = Depends(ArticleCrud.filter_params()),
) -> PaginatedResponse[ArticleRead]:
    return await ArticleCrud.cursor_paginate(
        session=session,
        cursor=cursor,
        items_per_page=items_per_page,
        search=search,
        filter_by=filter_by or None,
        schema=ArticleRead,
    )

from fastapi_toolsets.crud import CrudFactory

from .models import Article, Category

ArticleCrud = CrudFactory(
    model=Article,
    cursor_column=Article.created_at,  # monotonic timestamp — required for cursor_paginate
    searchable_fields=[  # default fields for full-text search
        Article.title,
        Article.body,
        (Article.category, Category.name),
    ],
    facet_fields=[  # fields exposed as filter dropdowns
        Article.status,
        (Article.category, Category.name),
    ],
)

ArticleFilters = ArticleCrud.filter_params()

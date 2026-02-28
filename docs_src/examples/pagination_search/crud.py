from fastapi_toolsets.crud import CrudFactory

from .models import Article, Category

ArticleCrud = CrudFactory(
    model=Article,
    cursor_column=Article.created_at,
    searchable_fields=[  # default fields for full-text search
        Article.title,
        Article.body,
        (Article.category, Category.name),
    ],
    facet_fields=[  # fields exposed as filter dropdowns
        Article.status,
        (Article.category, Category.name),
    ],
    sort_fields=[  # fields exposed for client-driven sorting
        Article.title,
        Article.created_at,
    ],
)

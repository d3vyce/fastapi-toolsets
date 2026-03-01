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
    order_fields=[  # fields exposed for client-driven ordering
        Article.title,
        Article.created_at,
    ],
)

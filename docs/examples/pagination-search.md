# Pagination & search

This example builds an articles listing endpoint that supports **offset pagination**, **cursor pagination**, **full-text search**, and **faceted filtering** — all from a single `CrudFactory` definition.

## Models

```python title="models.py"
--8<-- "docs_src/examples/pagination_search/models.py"
```

## Schemas

```python title="schemas.py"
--8<-- "docs_src/examples/pagination_search/schemas.py"
```

## CRUD

Declare `facet_fields` and `searchable_fields` once on [`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory). All endpoints built from this class share the same defaults and can override them per call.

```python title="crud.py"
--8<-- "docs_src/examples/pagination_search/crud.py"
```

## Session dependency

```python title="app.py"
--8<-- "docs_src/examples/pagination_search/app.py"
```

!!! info "Deploy a Postgres DB with docker"
    ```bash
    docker run -d --name postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres -p 5432:5432 postgres:18-alpine
    ```


## Offset pagination

Best for admin panels or any UI that needs a total item count and numbered pages.

```python title="routes.py:12:27"
--8<-- "docs_src/examples/pagination_search/routes.py:12:27"
```

**Example request**

```
GET /articles/offset?page=2&items_per_page=10&search=fastapi&status=published
```

**Example response**

```json
{
  "status": "SUCCESS",
  "data": [
    { "id": "3f47ac69-...", "title": "FastAPI tips", "status": "published", ... }
  ],
  "pagination": {
    "total_count": 42,
    "page": 2,
    "items_per_page": 10,
    "has_more": true
  },
  "filter_attributes": {
    "status": ["archived", "draft", "published"],
    "name": ["backend", "frontend", "python"]
  }
}
```

`filter_attributes` always reflects the values visible **after** applying the active filters. Use it to populate filter dropdowns on the client.

## Cursor pagination

Best for feeds, infinite scroll, or any high-throughput API where offset performance degrades.

```python title="routes.py:30:45"
--8<-- "docs_src/examples/pagination_search/routes.py:30:45"
```

**Example request** (first page)

```
GET /articles/cursor?items_per_page=10&status=published
```

**Example response**

```json
{
  "status": "SUCCESS",
  "data": [
    { "id": "3f47ac69-...", "title": "FastAPI tips", "status": "published", ... }
  ],
  "pagination": {
    "next_cursor": "eyJ2YWx1ZSI6ICIzZjQ3YWM2OS0uLi4ifQ==",
    "prev_cursor": null,
    "items_per_page": 10,
    "has_more": true
  },
  "filter_attributes": {
    "status": ["published"],
    "name": ["backend", "python"]
  }
}
```

Pass `next_cursor` as the `cursor` query parameter on the next request to advance to the next page.

## Search behaviour

Both endpoints inherit the same `searchable_fields` declared on `ArticleCrud`:

| Field | What it searches |
|---|---|
| `Article.title` | Article title |
| `Article.body` | Article body text |
| `(Article.category, Category.name)` | Name of the related category |

Search is **case-insensitive** and uses a `LIKE %query%` pattern. Pass a [`SearchConfig`](../reference/crud.md#fastapi_toolsets.crud.search.SearchConfig) instead of a plain string to control case sensitivity or switch to `match_mode="all"` (AND across all fields instead of OR).

```python
from fastapi_toolsets.crud import SearchConfig

# Both title AND body must contain "fastapi"
result = await ArticleCrud.offset_paginate(
    session,
    search=SearchConfig(query="fastapi", case_sensitive=True, match_mode="all"),
    search_fields=[Article.title, Article.body],
)
```

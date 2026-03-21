# Pagination & search

This example builds an articles listing endpoint that supports **offset pagination**, **cursor pagination**, **full-text search**, **faceted filtering**, and **sorting** — all from a single `CrudFactory` definition.

## Models

```python title="models.py"
--8<-- "docs_src/examples/pagination_search/models.py"
```

## Schemas

```python title="schemas.py"
--8<-- "docs_src/examples/pagination_search/schemas.py"
```

## Crud

Declare `searchable_fields`, `facet_fields`, and `order_fields` once on [`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory). All endpoints built from this class share the same defaults and can override them per call.

```python title="crud.py"
--8<-- "docs_src/examples/pagination_search/crud.py"
```

## Session dependency

```python title="db.py"
--8<-- "docs_src/examples/pagination_search/db.py"
```

!!! info "Deploy a Postgres DB with docker"
    ```bash
    docker run -d --name postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres -p 5432:5432 postgres:18-alpine
    ```


## App

```python title="app.py"
--8<-- "docs_src/examples/pagination_search/app.py"
```


## Routes

```python title="routes.py:1:17"
--8<-- "docs_src/examples/pagination_search/routes.py:1:17"
```

### Offset pagination

Best for admin panels or any UI that needs a total item count and numbered pages.

```python title="routes.py:20:40"
--8<-- "docs_src/examples/pagination_search/routes.py:20:40"
```

**Example request**

```
GET /articles/offset?page=2&items_per_page=10&search=fastapi&status=published&order_by=title&order=asc
```

**Example response**

```json
{
  "status": "SUCCESS",
  "pagination_type": "offset",
  "data": [
    { "id": "3f47ac69-...", "title": "FastAPI tips", "status": "published", ... }
  ],
  "pagination": {
    "total_count": 42,
    "pages": 5,
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

To skip the `COUNT(*)` query for better performance on large tables, pass `include_total=False`. `pagination.total_count` will be `null` in the response, while `has_more` remains accurate.

### Cursor pagination

Best for feeds, infinite scroll, or any high-throughput API where offset performance degrades.

```python title="routes.py:43:63"
--8<-- "docs_src/examples/pagination_search/routes.py:43:63"
```

**Example request**

```
GET /articles/cursor?items_per_page=10&status=published&order_by=created_at&order=desc
```

**Example response**

```json
{
  "status": "SUCCESS",
  "pagination_type": "cursor",
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

### Unified endpoint (both strategies)

!!! info "Added in `v2.3.0`"

[`paginate()`](../module/crud.md#unified-paginate--both-strategies-on-one-endpoint) lets a single endpoint support both strategies via a `pagination_type` query parameter. The `pagination_type` field in the response acts as a discriminator for frontend tooling.

```python title="routes.py:66:90"
--8<-- "docs_src/examples/pagination_search/routes.py:66:90"
```

**Offset request** (default)

```
GET /articles/?pagination_type=offset&page=1&items_per_page=10
```

```json
{
  "status": "SUCCESS",
  "pagination_type": "offset",
  "data": ["..."],
  "pagination": { "total_count": 42, "pages": 5, "page": 1, "items_per_page": 10, "has_more": true }
}
```

**Cursor request**

```
GET /articles/?pagination_type=cursor&items_per_page=10
GET /articles/?pagination_type=cursor&items_per_page=10&cursor=eyJ2YWx1ZSI6...
```

```json
{
  "status": "SUCCESS",
  "pagination_type": "cursor",
  "data": ["..."],
  "pagination": { "next_cursor": "eyJ2YWx1ZSI6...", "prev_cursor": null, "items_per_page": 10, "has_more": true }
}
```

## Search behaviour

Both endpoints inherit the same `searchable_fields` declared on `ArticleCrud`:

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

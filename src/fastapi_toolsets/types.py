"""Shared type aliases for the fastapi-toolsets package."""

from collections.abc import AsyncGenerator, Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, QueryableAttribute
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

# Generic TypeVars
DataT = TypeVar("DataT")
ModelType = TypeVar("ModelType", bound=DeclarativeBase)
SchemaType = TypeVar("SchemaType", bound=BaseModel)

# CRUD type aliases
JoinType = list[tuple[type[DeclarativeBase], Any]]
M2MFieldType = Mapping[str, QueryableAttribute[Any]]
OrderByClause = ColumnElement[Any] | QueryableAttribute[Any]

# Search / facet type aliases
SearchFieldType = InstrumentedAttribute[Any] | tuple[InstrumentedAttribute[Any], ...]
FacetFieldType = SearchFieldType

# Dependency type aliases
SessionDependency = Callable[[], AsyncGenerator[AsyncSession, None]]

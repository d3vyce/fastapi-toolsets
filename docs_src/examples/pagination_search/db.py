from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.db import Database

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

db = Database(url=DATABASE_URL)

get_db = db

SessionDep = Annotated[AsyncSession, Depends(db)]

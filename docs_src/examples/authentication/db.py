from fastapi import Depends
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fastapi_toolsets.db import create_db_context, create_db_dependency

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

engine = create_async_engine(url=DATABASE_URL, future=True)
async_session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

get_db = create_db_dependency(session_maker=async_session_maker)
get_db_context = create_db_context(session_maker=async_session_maker)


SessionDep = Depends(get_db)

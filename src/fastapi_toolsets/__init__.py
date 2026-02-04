"""FastAPI utilities package.

Provides CRUD operations, fixtures, CLI, and standardized API responses
for FastAPI with async SQLAlchemy and PostgreSQL.

Example usage:
    from fastapi import FastAPI, Depends
    from fastapi_toolsets.exceptions import init_exceptions_handlers
    from fastapi_toolsets.crud import CrudFactory
    from fastapi_toolsets.db import create_db_dependency
    from fastapi_toolsets.schemas import Response

    app = FastAPI()
    init_exceptions_handlers(app)

    UserCrud = CrudFactory(User)

    @app.get("/users/{user_id}", response_model=Response[dict])
    async def get_user(user_id: int, session = Depends(get_db)):
        user = await UserCrud.get(session, [User.id == user_id])
        return Response(data={"user": user.username}, message="Success")
"""

__version__ = "0.6.0"

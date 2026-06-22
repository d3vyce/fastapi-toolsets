from fastapi import FastAPI

from fastapi_toolsets.exceptions import init_exceptions_handlers

from .db import db
from .routes import router

app = FastAPI()
db.install(app=app)
init_exceptions_handlers(app=app)
app.include_router(router=router)

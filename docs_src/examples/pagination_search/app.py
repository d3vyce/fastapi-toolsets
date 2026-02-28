from fastapi import FastAPI

from fastapi_toolsets.exceptions import init_exceptions_handlers

from .routes import router

app = FastAPI()
init_exceptions_handlers(app=app)
app.include_router(router=router)

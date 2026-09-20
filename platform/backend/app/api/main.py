from fastapi import APIRouter

from app.api.routes import audit, items, login, private, services, users, utils
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(services.router)
api_router.include_router(audit.router)
api_router.include_router(audit.roles_router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)

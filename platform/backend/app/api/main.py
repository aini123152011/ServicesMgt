from fastapi import APIRouter

from app.api.routes import (
    audit,
    l2,
    login,
    service_data,
    services,
    system,
    users,
    utils,
)

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(services.router)
api_router.include_router(service_data.router)
api_router.include_router(audit.router)
api_router.include_router(system.router)
api_router.include_router(l2.router)
api_router.include_router(audit.roles_router)

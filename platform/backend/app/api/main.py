from fastapi import APIRouter

from app.api.routes import (
    access_control,
    audit,
    captcha,
    l2,
    login,
    registration,
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
# 账号准入：匿名入口（验证码/注册/邮箱验证）与管理端规则配置分开成两个 router
api_router.include_router(captcha.router)
api_router.include_router(registration.router)
api_router.include_router(access_control.router)

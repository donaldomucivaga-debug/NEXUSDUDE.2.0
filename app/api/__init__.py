from fastapi import APIRouter
from app.api.health_routes import router as health_router
from app.api.auth_routes import router as auth_router
from app.api.maps_routes import router as maps_router
from app.api.inventory_routes import router as inventory_router
from app.api.zabbix_routes import router as zabbix_router
from app.api.webhook_routes import router as webhook_router
from app.api.rbac_routes import router as rbac_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(maps_router)
api_router.include_router(inventory_router)
api_router.include_router(zabbix_router)
api_router.include_router(webhook_router)
api_router.include_router(rbac_router)

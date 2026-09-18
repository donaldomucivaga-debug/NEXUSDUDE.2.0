from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import logging
import os

from app.config import settings
from app.database import init_db
from app.api import api_router

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("nexusdude")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Iniciando {settings.APP_NAME} v{settings.APP_VERSION}...")
    await init_db()
    # Iniciar carga asíncrona de inventario en segundo plano
    import asyncio
    from app.services.inventory_service import inventory_service
    asyncio.create_task(inventory_service.refresh_cache())
    logger.info("Base de datos lista. NexusDude listo para recibir conexiones.")
    yield
    # Shutdown
    logger.info("Deteniendo NexusDude...")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="The Modern Network Dude for Nexus, Zabbix & NetBox",
    lifespan=lifespan
)

# Configuración de CORS
origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]
if not origins or "*" in origins:
    origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routers
app.include_router(api_router)

# Archivos Estáticos
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index():
    """Sirve la interfaz web SPA de NexusDude."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {
        "service": settings.APP_NAME,
        "status": "online",
        "message": "NexusDude API running. Static frontend not found."
    }

from fastapi import APIRouter
from datetime import datetime, timezone
import os
import aiosqlite
from app.config import settings
from app.models import HealthResponse

router = APIRouter(tags=["Health"])

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Endpoint de verificación de salud del servicio."""
    db_status = "ok"
    try:
        if os.path.exists(settings.DATABASE_PATH):
            async with aiosqlite.connect(settings.DATABASE_PATH) as db:
                await db.execute("SELECT 1")
        else:
            db_status = "database_file_not_found"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        timestamp=datetime.now(timezone.utc),
        database=db_status,
        nexus_orchestrator_url=settings.NEXUS_ORCHESTRATOR_URL
    )

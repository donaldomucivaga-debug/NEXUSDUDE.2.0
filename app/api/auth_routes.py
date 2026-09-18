from fastapi import APIRouter, Depends
from typing import Dict, Any
from app.auth import get_current_user
from app.config import settings
from app.models import UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("/verify", response_model=UserResponse)
async def verify_token(user: Dict[str, Any] = Depends(get_current_user)):
    """Verifica si el token JWT de Nexus es válido y retorna datos del usuario y sus permisos."""
    return UserResponse(
        username=user["username"],
        role=user.get("role", "viewer"),
        permissions=user.get("permissions"),
        exp=user.get("exp"),
        authenticated=True
    )

@router.get("/me", response_model=UserResponse)
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    """Retorna información del usuario autenticado y su perfil de permisos."""
    return UserResponse(
        username=user["username"],
        role=user.get("role", "viewer"),
        permissions=user.get("permissions"),
        exp=user.get("exp"),
        authenticated=True
    )

@router.get("/config")
async def get_auth_config():
    """Retorna configuración pública para redirecciones de autenticación."""
    return {
        "nexus_orchestrator_url": settings.NEXUS_ORCHESTRATOR_URL,
        "login_url": f"{settings.NEXUS_ORCHESTRATOR_URL}/#/login"
    }

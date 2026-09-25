from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.auth import get_current_user
from app.services.iwisp_service import iwisp_service

router = APIRouter(prefix="/config", tags=["System Configuration"])

class IWispConfigPayload(BaseModel):
    api_key: Optional[str] = None
    api_url: Optional[str] = None

class IWispTestPayload(BaseModel):
    api_key: Optional[str] = None
    api_url: Optional[str] = None

@router.get("/iwisp")
async def get_iwisp_config(user: Dict[str, Any] = Depends(get_current_user)):
    """Obtiene la configuración actual y estado de la integración con i-WISP Manager."""
    try:
        cfg = await iwisp_service.get_config()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo configuración: {str(e)}")

@router.post("/iwisp")
async def save_iwisp_config(
    payload: IWispConfigPayload,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Guarda o actualiza la API Key y URL base de i-WISP Manager en la base de datos local."""
    try:
        res = await iwisp_service.save_config(api_key=payload.api_key, api_url=payload.api_url)
        return {
            "status": "success",
            "message": "Configuración de i-WISP guardada correctamente.",
            "config": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando configuración: {str(e)}")

@router.post("/iwisp/test")
async def test_iwisp_connection(
    payload: Optional[IWispTestPayload] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Prueba la conectividad y validez de la API Key contra los servidores de i-WISP."""
    try:
        k = payload.api_key if payload else None
        u = payload.api_url if payload else None
        result = await iwisp_service.test_connection(test_api_key=k, test_api_url=u)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error probando conexión: {str(e)}")

@router.post("/iwisp/sync")
async def sync_iwisp_clients(
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Inicia la sincronización de clientes y ONTs desde i-WISP Manager hacia la caché local."""
    try:
        # Iniciar sincronización (puede correr sincrónica o en fondo)
        report = await iwisp_service.sync_clients_from_iwisp()
        return {
            "status": "success",
            "message": "Sincronización completada exitosamente.",
            "report": report
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en sincronización i-WISP: {str(e)}")

@router.get("/iwisp/cache-status")
async def get_iwisp_cache_status(user: Dict[str, Any] = Depends(get_current_user)):
    """Obtiene el número de clientes y ONTs cacheadas en SQLite y la última fecha de actualización."""
    try:
        status_info = await iwisp_service.get_cache_status()
        return status_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo estado de caché: {str(e)}")

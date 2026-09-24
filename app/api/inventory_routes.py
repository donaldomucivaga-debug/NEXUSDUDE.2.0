from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Dict, Any, List, Optional
from app.auth import get_current_user
from app.services.inventory_service import inventory_service

router = APIRouter(prefix="/inventory", tags=["Inventory & Search"])

@router.get("/devices/{device_id}")
async def get_inventory_device_by_id(
    device_id: int,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Retorna los datos completos de un dispositivo por su ID de NetBox."""
    dev = await inventory_service.get_device_by_id(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado en NetBox")
    return dev

@router.get("/devices/{device_id}/interfaces")
async def get_device_interfaces(
    device_id: int,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Retorna la lista de puertos/interfaces físicas de un dispositivo en NetBox con su estado de ocupación."""
    interfaces = await inventory_service.get_device_interfaces(device_id)
    return interfaces

@router.get("/devices")
async def get_inventory_devices(
    query: Optional[str] = Query(None, description="Búsqueda por texto (nombre, ip, modelo)"),
    site: Optional[str] = Query(None, description="Filtro por sitio"),
    role: Optional[str] = Query(None, description="Filtro por rol de dispositivo"),
    manufacturer: Optional[str] = Query(None, description="Filtro por fabricante"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Retorna dispositivos filtrados y paginados en <5ms desde la memoria caché."""
    return await inventory_service.search_devices(
        query=query or "",
        site=site or "",
        role=role or "",
        manufacturer=manufacturer or "",
        limit=limit,
        offset=offset
    )

@router.get("/sites-summary")
async def get_inventory_sites_summary(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista de sitios con conteo de dispositivos ordenados por mayor densidad."""
    return await inventory_service.get_sites_summary()

@router.get("/sites/{site_name}/devices")
async def get_devices_in_site(site_name: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Lista todos los dispositivos que pertenecen a un sitio específico."""
    return await inventory_service.get_devices_by_site(site_name)

@router.get("/sites")
async def get_inventory_sites(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista de sitios registrados en NetBox."""
    return await inventory_service.get_sites()

@router.get("/roles")
async def get_inventory_roles(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista de roles de dispositivos (Core, Switch, AP, etc.)."""
    return await inventory_service.get_roles()

@router.get("/manufacturers")
async def get_inventory_manufacturers(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista de fabricantes de equipos (MikroTik, Cambium, etc.)."""
    return await inventory_service.get_manufacturers()

@router.post("/refresh")
async def refresh_inventory_cache(user: Dict[str, Any] = Depends(get_current_user)):
    """Fuerza la recarga de inventario desde NetBox."""
    await inventory_service.refresh_cache(force=True)
    return {"status": "success", "message": "Inventario recargado exitosamente"}

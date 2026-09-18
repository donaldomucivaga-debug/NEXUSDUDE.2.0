from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.services.zabbix_service import zabbix_service

router = APIRouter(prefix="/zabbix", tags=["Zabbix BSM Integration"])

@router.get("/status")
async def get_zabbix_status(user: Dict[str, Any] = Depends(get_current_user)):
    """Verifica la conectividad con la API de Zabbix 7.0 y obtiene métricas de estado."""
    status_info = await zabbix_service.get_status()
    return status_info

@router.get("/analysis/{map_id}")
async def get_topology_analysis(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Analiza la topología del mapa:
    - Clasificación de relaciones Directas vs Indirectas/Redundantes.
    - Detección de nodos raíz (Gateways / Uplinks).
    - Estado de coincidencia de cada nodo con los hosts de Zabbix.
    """
    try:
        analysis = await zabbix_service.analyze_topology(map_id)
        return analysis
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error analizando topología: {str(e)}")

@router.post("/sync")
async def sync_all_to_zabbix(clear_first: bool = True, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Sincroniza toda la arquitectura de NexusDude hacia Zabbix Services (BSM):
    - Mapas y Submapas como servicios contenedores con jerarquía padre/hijo.
    - Nodos como servicios de dispositivos, asociando etiquetas de problemas.
    - Aristas modeladas como dependencias directas o redundantes para alimentar el motor de alertas y SLA.
    """
    try:
        report = await zabbix_service.sync_all_to_zabbix(clear_first=clear_first)
        return {
            "status": "success",
            "message": "Topología sincronizada exitosamente con Zabbix Services",
            "report": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en sincronización con Zabbix: {str(e)}")

@router.delete("/services")
async def clear_zabbix_services(user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina todos los servicios creados por NexusDude en Zabbix para permitir re-sincronizaciones limpias."""
    try:
        deleted_count = await zabbix_service.clear_nexus_services()
        return {
            "status": "success",
            "deleted_services": deleted_count,
            "message": f"Se eliminaron {deleted_count} servicios de NexusDude en Zabbix."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error eliminando servicios en Zabbix: {str(e)}")

@router.get("/realtime/{map_id}")
async def get_map_realtime_status(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Calcula en tiempo real el estado operativo (online/offline/warning/ok) de todos los nodos
    y submapas de un mapa, cruzando telemetría viva de Zabbix 7.0 (ICMP ping, triggers activos).
    Actualiza automáticamente el campo 'status' en la base de datos local.
    """
    try:
        result = await zabbix_service.get_map_realtime_status(map_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo estado en tiempo real: {str(e)}")

@router.get("/node-telemetry/{node_id}")
async def get_node_realtime_telemetry(node_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene la telemetría en tiempo real de un nodo específico:
    estado ICMP ping, pérdida de paquetes, RTT y problemas activos en Zabbix.
    """
    try:
        result = await zabbix_service.get_node_telemetry(node_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo telemetría del nodo: {str(e)}")

@router.get("/spectrum/{map_id}")
async def get_map_spectrum(map_id: str = "default-map", user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene el análisis espectral y canales de frecuencia (4850 - 7250 MHz)
    de los equipos del mapa seleccionado para la visualización en la regla de frecuencias.
    """
    try:
        result = await zabbix_service.get_map_spectrum_data(map_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo datos de espectro: {str(e)}")


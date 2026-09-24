from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.models import ZabbixSyncRequest
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
    - Clasificación de relaciones Directas de Servicio (flujo Padre -> Hijo).
    - Detección de nodos raíz (Gateways / Uplinks).
    - Estado de coincidencia de cada nodo con los hosts de Zabbix.
    """
    try:
        analysis = await zabbix_service.analyze_topology(map_id)
        return analysis
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error analizando topología: {str(e)}")

@router.post("/sync")
async def sync_to_zabbix(
    payload: Optional[ZabbixSyncRequest] = None,
    clear_first: Optional[bool] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Sincroniza la arquitectura de NexusDude hacia Zabbix Services (BSM):
    - Permite alcance 'global' (todo el sistema) o 'branch' (mapa activo y descendientes).
    - Mapas y Submapas como servicios contenedores con jerarquía padre/hijo.
    - Nodos como servicios de dispositivos con propagación de fallas y causas raíz.
    """
    try:
        scope = "global"
        map_id = None
        should_clear = True

        if payload:
            scope = payload.scope or "global"
            map_id = payload.map_id
            if payload.clear_first is not None:
                should_clear = payload.clear_first
        elif clear_first is not None:
            should_clear = clear_first

        report = await zabbix_service.sync_to_zabbix(map_id=map_id, scope=scope, clear_first=should_clear)
        return {
            "status": "success",
            "message": f"Topología ({'Rama Actual' if scope == 'branch' else 'Todo el Sistema'}) sincronizada exitosamente con Zabbix Services",
            "report": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en sincronización con Zabbix: {str(e)}")

@router.delete("/services")
async def clear_zabbix_services(
    map_id: Optional[str] = None,
    scope: Optional[str] = "global",
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Elimina los servicios creados por NexusDude en Zabbix (Global o Rama específica)."""
    try:
        if scope == "branch" and map_id:
            branch_map_ids = await zabbix_service.get_descendant_map_ids(map_id)
            deleted_count = await zabbix_service.clear_branch_services(branch_map_ids)
            msg = f"Se eliminaron {deleted_count} servicios de la rama seleccionada ({len(branch_map_ids)} mapas) en Zabbix."
        else:
            deleted_count = await zabbix_service.clear_nexus_services()
            msg = f"Se eliminaron {deleted_count} servicios de NexusDude en Zabbix."

        return {
            "status": "success",
            "deleted_services": deleted_count,
            "message": msg
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

@router.get("/nodes/{node_id}/interfaces")
async def get_node_zabbix_interfaces(node_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene la lista de interfaces de red, radio y ópticas monitoreadas en Zabbix para un nodo.
    Permite seleccionar fuentes de datos vivas para vincular a los enlaces / aristas de la topología.
    """
    try:
        ifaces = await zabbix_service.get_node_zabbix_interfaces(node_id)
        return ifaces
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo interfaces de Zabbix: {str(e)}")

@router.get("/links/{link_id}/telemetry")
async def get_link_telemetry(link_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene la telemetría viva en tiempo real de una arista (enlace):
    tráfico de subida/bajada, estado de puerto, potencia óptica DDM (Rx/Tx) o señal inalámbrica (RSSI/SNR).
    """
    try:
        telemetry = await zabbix_service.get_link_telemetry(link_id)
        return telemetry
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo telemetría del enlace: {str(e)}")

@router.get("/maps/{map_id}/links-telemetry")
async def get_map_links_telemetry(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene la telemetría en lote de todas las aristas de un mapa para renderizado y animación continua.
    """
    try:
        data = await zabbix_service.get_map_links_telemetry(map_id)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo telemetría de enlaces del mapa: {str(e)}")

@router.get("/gpon-branch/{node_id}")
async def get_gpon_branch_telemetry(node_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene la telemetría completa de un brazo FTTH / ramal GPON:
    - Estado de puerto (Up/Down)
    - Tráfico en tiempo real (In/Out)
    - Volumen acumulado de datos
    - Potencia óptica de clientes ONUs: conteo y promedio de Típicos (> -27 dBm) y Atípicos (<= -27 dBm)
    """
    try:
        data = await zabbix_service.get_gpon_branch_telemetry(node_id)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo telemetría de brazo GPON: {str(e)}")

@router.get("/olt/{olt_ip_or_name}/gpon-ports")
async def get_olt_gpon_ports(olt_ip_or_name: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Obtiene la lista de puertos GPON detectados en Zabbix para una OLT seleccionada.
    """
    try:
        ports = await zabbix_service.get_olt_gpon_ports(olt_ip_or_name)
        return ports
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listando puertos GPON de la OLT: {str(e)}")


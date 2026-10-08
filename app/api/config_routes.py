import os
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
import httpx

from app.auth import get_current_user
from app.config import settings
from app.database import get_system_config, set_system_config, get_all_system_config
from app.services.iwisp_service import iwisp_service

logger = logging.getLogger("nexusdude.config")
router = APIRouter(prefix="/config", tags=["System Configuration"])

# --- Helper para actualizar .env ---
def update_env_file(key_values: Dict[str, str]):
    possible_paths = [
        "/app/.env",
        "/home/nexusmv/nexusdude2.0/.env",
        os.path.abspath(".env")
    ]
    for p in possible_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                updated_lines = []
                keys_found = set()
                for line in lines:
                    replaced = False
                    for k, v in key_values.items():
                        if line.startswith(f"{k}="):
                            updated_lines.append(f"{k}={v}\n")
                            keys_found.add(k)
                            replaced = True
                            break
                    if not replaced:
                        updated_lines.append(line)
                for k, v in key_values.items():
                    if k not in keys_found:
                        updated_lines.append(f"{k}={v}\n")
                with open(p, "w", encoding="utf-8") as f:
                    f.writelines(updated_lines)
                logger.info(f"Archivo de entorno {p} sincronizado exitosamente.")
            except Exception as e:
                logger.warning(f"Error actualizando archivo {p}: {e}")

# --- Pydantic Models ---
class NetboxConfigPayload(BaseModel):
    url: Optional[str] = None
    token: Optional[str] = None

class NetboxTestPayload(BaseModel):
    url: Optional[str] = None
    token: Optional[str] = None

class ZabbixConfigPayload(BaseModel):
    url: Optional[str] = None
    token: Optional[str] = None
    poll_interval: Optional[int] = None

class ZabbixTestPayload(BaseModel):
    url: Optional[str] = None
    token: Optional[str] = None

class IWispConfigPayload(BaseModel):
    api_key: Optional[str] = None
    api_url: Optional[str] = None

class IWispTestPayload(BaseModel):
    api_key: Optional[str] = None
    api_url: Optional[str] = None

# --- Rutas de Integraciones (NetBox y Zabbix) ---
@router.get("/integrations")
async def get_integrations_config(user: Dict[str, Any] = Depends(get_current_user)):
    """Devuelve la configuración de IP y API de NetBox y Zabbix."""
    try:
        return {
            "netbox": {
                "url": settings.NETBOX_URL,
                "token": settings.NETBOX_TOKEN
            },
            "zabbix": {
                "url": settings.ZABBIX_URL,
                "token": getattr(settings, "ZABBIX_TOKEN", ""),
                "poll_interval": getattr(settings, "ZABBIX_POLL_INTERVAL", 30)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error obteniendo configuraciones: {str(e)}")

@router.post("/netbox/test")
async def test_netbox_connection(
    payload: Optional[NetboxTestPayload] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Prueba la conectividad y validez del Token contra la API de NetBox."""
    target_url = (payload.url.strip() if payload and payload.url else settings.NETBOX_URL).rstrip('/')
    target_token = payload.token.strip() if payload and payload.token else settings.NETBOX_TOKEN

    if not target_url:
        return {"success": False, "message": "Por favor especifica la URL de NetBox."}
    if not target_token:
        return {"success": False, "message": "Por favor especifica el Token de API de NetBox."}

    headers = {
        "Authorization": f"Token {target_token}",
        "Accept": "application/json"
    }

    async def _try_connect_netbox(test_url: str):
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            try:
                res = await client.get(f"{test_url}/api/status/", headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    ver = data.get("netbox-version") or data.get("version") or "4.x"
                    return True, ver, f"Conexión exitosa. Servidor NetBox v{ver} en línea y respondiendo."
                elif res.status_code in (401, 403):
                    return False, None, f"Acceso denegado (HTTP {res.status_code}): Token de NetBox inválido o expirado."
            except Exception:
                pass

            try:
                res_sites = await client.get(f"{test_url}/api/dcim/sites/?limit=1", headers=headers)
                if res_sites.status_code == 200:
                    count = res_sites.json().get("count", 0)
                    return True, "4.x", f"Conexión exitosa con NetBox. Total de sitios disponibles: {count}."
                elif res_sites.status_code in (401, 403):
                    return False, None, f"Acceso denegado (HTTP {res_sites.status_code}): Token de NetBox sin permisos suficientes."
            except Exception as err:
                return False, None, str(err)
        return False, None, "No se pudo conectar a NetBox."

    # 1. Si la URL apunta a la IP local del host (10.9.1.6 o localhost), priorizar red interna Docker (netbox:8080)
    if any(h in target_url for h in ("10.9.1.6", "localhost", "127.0.0.1", "netbox")):
        ok_docker, ver_docker, msg_docker = await _try_connect_netbox("http://netbox:8080")
        if ok_docker:
            return {
                "success": True,
                "version": ver_docker,
                "message": f"Conexión exitosa con NetBox v{ver_docker}. (Enlace interno Docker netbox:8080 activo)."
            }

    # 2. Probar URL indicada por el usuario
    ok, ver, msg = await _try_connect_netbox(target_url)
    if ok:
        return {"success": True, "version": ver, "message": msg}

    return {"success": False, "message": f"Tiempo de espera agotado al conectar a {target_url}. En Docker usa 'http://netbox:8080'."}

@router.post("/netbox")
async def save_netbox_config(
    payload: NetboxConfigPayload,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Guarda la configuración de IP y API Token de NetBox."""
    try:
        env_updates = {}
        if payload.url is not None:
            clean_url = payload.url.strip().rstrip('/')
            effective_url = clean_url
            if any(h in clean_url for h in ("10.9.1.6", "localhost", "127.0.0.1")):
                effective_url = "http://netbox:8080"
            await set_system_config("netbox_url", effective_url, "URL del servidor NetBox")
            settings.NETBOX_URL = effective_url
            env_updates["NETBOX_URL"] = effective_url

        if payload.token is not None and payload.token.strip():
            clean_token = payload.token.strip()
            await set_system_config("netbox_token", clean_token, "Token API de NetBox")
            settings.NETBOX_TOKEN = clean_token
            env_updates["NETBOX_TOKEN"] = clean_token

        if env_updates:
            update_env_file(env_updates)

        # Refrescar inventario en segundo plano
        from app.services.inventory_service import inventory_service
        BackgroundTasks().add_task(inventory_service.refresh_cache, force=True)

        return {
            "success": True,
            "message": "Configuración de IP y API Token de NetBox guardada correctamente."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando configuración de NetBox: {str(e)}")

@router.post("/zabbix/test")
async def test_zabbix_connection(
    payload: Optional[ZabbixTestPayload] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Prueba la conectividad y validez del API Token contra la API JSON-RPC de Zabbix."""
    target_url = (payload.url.strip() if payload and payload.url else settings.ZABBIX_URL).rstrip('/')
    target_token = payload.token.strip() if payload and payload.token else getattr(settings, "ZABBIX_TOKEN", "")

    if not target_url:
        return {"success": False, "message": "Por favor especifica la URL de Zabbix."}
    if not target_token:
        return {"success": False, "message": "Por favor especifica el API Token de Zabbix."}

    api_url = f"{target_url}/api_jsonrpc.php"

    async with httpx.AsyncClient(verify=False, timeout=8.0) as client:
        try:
            # 1. Obtener versión de API
            ver_res = await client.post(api_url, json={
                "jsonrpc": "2.0",
                "method": "apiinfo.version",
                "params": [],
                "id": 1
            })
            if ver_res.status_code != 200:
                return {
                    "success": False,
                    "message": f"Servidor Zabbix respondió con HTTP {ver_res.status_code}. Verifica la URL."
                }
            ver_data = ver_res.json()
            version = ver_data.get("result", "7.0")

            # 2. Validar autenticación con el API Token
            test_res = await client.post(
                api_url,
                headers={"Authorization": f"Bearer {target_token}"},
                json={
                    "jsonrpc": "2.0",
                    "method": "host.get",
                    "params": {"countOutput": True},
                    "auth": target_token,
                    "id": 2
                }
            )
            test_data = test_res.json()
            if "error" in test_data:
                err = test_data["error"]
                detail = err.get("data") or err.get("message") or "Token inválido"
                return {
                    "success": False,
                    "message": f"Zabbix v{version}: Falló la autenticación con el Token ({detail})."
                }

            host_count = test_data.get("result", 0)
            return {
                "success": True,
                "version": version,
                "message": f"Conexión y autenticación exitosa con Zabbix v{version} (Total hosts: {host_count})."
            }
        except httpx.ConnectTimeout:
            return {
                "success": False,
                "message": f"Tiempo de espera agotado al conectar a Zabbix en {target_url}."
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error conectando a Zabbix ({target_url}): {str(e)}"
            }

@router.post("/zabbix")
async def save_zabbix_config(
    payload: ZabbixConfigPayload,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Guarda la configuración de IP y API Token de Zabbix."""
    try:
        env_updates = {}
        if payload.url is not None:
            clean_url = payload.url.strip().rstrip('/')
            await set_system_config("zabbix_url", clean_url, "URL del servidor Zabbix")
            settings.ZABBIX_URL = clean_url
            env_updates["ZABBIX_URL"] = clean_url

        if payload.token is not None and payload.token.strip():
            clean_token = payload.token.strip()
            await set_system_config("zabbix_token", clean_token, "Token API de Zabbix")
            settings.ZABBIX_TOKEN = clean_token
            env_updates["ZABBIX_TOKEN"] = clean_token

        if payload.poll_interval is not None:
            clean_interval = int(payload.poll_interval)
            await set_system_config("zabbix_poll_interval", str(clean_interval), "Intervalo de polling Zabbix (segundos)")
            settings.ZABBIX_POLL_INTERVAL = clean_interval
            env_updates["ZABBIX_POLL_INTERVAL"] = str(clean_interval)

        if env_updates:
            update_env_file(env_updates)

        # Actualizar servicio Zabbix en memoria
        try:
            from app.services.zabbix_service import zabbix_service
            zabbix_service.base_url = settings.ZABBIX_URL.rstrip('/')
            zabbix_service.api_url = f"{zabbix_service.base_url}/api_jsonrpc.php"
            if payload.token and payload.token.strip():
                zabbix_service.auth_token = payload.token.strip()
        except Exception:
            pass

        return {
            "success": True,
            "message": "Configuración de IP y API Token de Zabbix guardada y aplicada exitosamente."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error guardando configuración de Zabbix: {str(e)}")

# --- Compatibilidad hacia atrás (i-WISP Endpoints inactivos) ---
@router.get("/iwisp")
async def get_iwisp_config(user: Dict[str, Any] = Depends(get_current_user)):
    return await iwisp_service.get_config()

@router.post("/iwisp")
async def save_iwisp_config(payload: IWispConfigPayload, user: Dict[str, Any] = Depends(get_current_user)):
    res = await iwisp_service.save_config(api_key=payload.api_key, api_url=payload.api_url)
    return {"status": "success", "message": "OK", "config": res}

@router.post("/iwisp/test")
async def test_iwisp_connection(payload: Optional[IWispTestPayload] = None, user: Dict[str, Any] = Depends(get_current_user)):
    return await iwisp_service.test_connection(test_api_key=payload.api_key if payload else None, test_api_url=payload.api_url if payload else None)

@router.post("/iwisp/sync")
async def sync_iwisp_clients(background_tasks: BackgroundTasks, user: Dict[str, Any] = Depends(get_current_user)):
    return {"status": "success", "message": "Sincronización deshabilitada en esta versión."}

@router.get("/iwisp/cache-status")
async def get_iwisp_cache_status(user: Dict[str, Any] = Depends(get_current_user)):
    return await iwisp_service.get_cache_status()

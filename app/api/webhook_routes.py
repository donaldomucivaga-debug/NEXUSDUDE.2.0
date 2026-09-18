import json
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException, status, Depends
import httpx

from app.database import get_db_connection
from app.services.inventory_service import inventory_service
from app.config import settings
from app.auth import get_current_user

logger = logging.getLogger("nexusdude.webhooks")

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

@router.post("/netbox")
async def receive_netbox_webhook(request: Request):
    """
    Recibe eventos en tiempo real desde NetBox (Sitios, Dispositivos).
    Cuando se crea un sitio nuevo en NetBox, genera automáticamente el submapa en NexusDude al instante
    sin tocar ni sobrescribir los mapas que ya existen.
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.warning(f"Webhook recibido con payload no-JSON: {e}")
        return {"status": "error", "message": "JSON inválido"}

    event = (payload.get("event") or "").lower()
    model = (payload.get("model") or "").lower()
    obj_type = str(payload.get("object_type") or "").lower()
    data = payload.get("data") or {}
    user = payload.get("username") or "NetBox Webhook"

    # Normalizar modelo (compatibilidad con NetBox 4.x object_type 'dcim.site' / 'dcim.device')
    if not model:
        if "site" in obj_type:
            model = "site"
        elif "device" in obj_type:
            model = "device"

    logger.info(f"🔔 NetBox Webhook recibido: model='{model}', event='{event}', user='{user}'")

    # 1. EVENTOS DE SITIOS (dcim.site)
    if model in ("site", "dcim.site") or "site" in obj_type:
        site_name = (data.get("name") or "").strip()
        site_id = data.get("id")

        if not site_name:
            return {"status": "ignored", "message": "Evento de sitio sin nombre"}

        # Si el sitio fue CREADO en NetBox
        if event in ("created", "object_created", "create"):
            async with get_db_connection() as db:
                # Comprobar si ya existe un mapa con este nombre (¡no tocar los existentes!)
                cursor = await db.execute("SELECT id, name FROM maps WHERE LOWER(name) = LOWER(?)", (site_name,))
                existing = await cursor.fetchone()

                if existing:
                    logger.info(f"Webhook NetBox: El sitio '{site_name}' ya tiene submapa (ID: {existing['id']}). Preservando intacto.")
                    # Refrescar caché de inventario de todas formas
                    asyncio.create_task(inventory_service.refresh_cache(force=True))
                    return {
                        "status": "preserved",
                        "message": f"El submapa '{site_name}' ya existe. No se modificó.",
                        "map_id": existing["id"],
                        "site_name": site_name
                    }

                # Si NO existe, crear el submapa automáticamente
                new_map_id = f"map-{uuid.uuid4().hex[:8]}"
                desc = f"Submapa generado automáticamente vía Webhook NetBox (Sitio: {site_name})"

                # 1. Crear el mapa
                await db.execute("""
                    INSERT INTO maps (id, name, description, parent_map_id, grid_size)
                    VALUES (?, ?, ?, ?, ?)
                """, (new_map_id, site_name, desc, "default-map", 20))

                # 2. Insertar nodo de acceso directo en el mapa principal (default-map)
                c_pnodes = await db.execute("SELECT id, x, y FROM nodes WHERE map_id = 'default-map'")
                pnodes = await c_pnodes.fetchall()
                cols = 8
                x_step = 180.0
                y_step = 110.0
                submap_idx = len(pnodes)
                sub_x = 80.0 + (submap_idx % cols) * x_step
                sub_y = 80.0 + (submap_idx // cols) * y_step

                subnode_id = f"node-{uuid.uuid4().hex[:8]}"
                sub_extra = json.dumps({
                    "target_map_id": new_map_id,
                    "site_name": site_name,
                    "site_id": site_id,
                    "webhook_auto": True
                })

                await db.execute("""
                    INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    subnode_id, "default-map", site_name, "", "submap",
                    site_name, sub_x, sub_y, "ok", sub_extra
                ))

                await db.commit()

                logger.info(f"✨ Webhook NetBox: ¡Nuevo sitio '{site_name}' detectado! Submapa '{new_map_id}' generado automáticamente.")

                # Forzar recarga de inventario en segundo plano
                asyncio.create_task(inventory_service.refresh_cache(force=True))

                return {
                    "status": "created",
                    "message": f"Submapa '{site_name}' creado automáticamente en tiempo real.",
                    "map_id": new_map_id,
                    "site_name": site_name
                }

        # Si el sitio fue ACTUALIZADO o ELIMINADO en NetBox
        elif event in ("updated", "object_updated", "deleted", "object_deleted"):
            logger.info(f"Webhook NetBox: Sitio '{site_name}' {event}. Actualizando caché...")
            asyncio.create_task(inventory_service.refresh_cache(force=True))
            return {"status": "synced", "message": f"Caché actualizado por evento {event} en sitio '{site_name}'"}

    # 2. EVENTOS DE DISPOSITIVOS (dcim.device)
    elif model in ("device", "dcim.device") or "device" in obj_type:
        dev_name = data.get("name") or f"Device-{data.get('id')}"
        logger.info(f"Webhook NetBox: Dispositivo '{dev_name}' {event}. Refrescando inventario...")
        asyncio.create_task(inventory_service.refresh_cache(force=True))
        return {"status": "synced", "message": f"Dispositivo '{dev_name}' sincronizado"}

    return {"status": "ignored", "message": f"Modelo '{model}' no requiere acción de submapa"}

@router.get("/netbox/status")
async def get_netbox_webhook_status(user: Dict[str, Any] = Depends(get_current_user)):
    """Verifica si el webhook y la regla de eventos están configurados en NetBox."""
    headers = {
        "Authorization": f"Token {settings.NETBOX_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    webhook_name = "NexusDude Realtime Submaps Webhook"
    event_rule_name = "NexusDude - Sync NetBox Sites to Submaps"
    target_url = "http://nexusdude-web:8000/api/webhooks/netbox"

    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            res_wh = await client.get(f"{settings.NETBOX_URL}/api/extras/webhooks/?name={webhook_name}", headers=headers)
            wh_data = res_wh.json() if res_wh.status_code == 200 else {}
            wh_results = wh_data.get("results", [])
            webhook_exists = len(wh_results) > 0
            webhook_id = wh_results[0]["id"] if webhook_exists else None

            res_er = await client.get(f"{settings.NETBOX_URL}/api/extras/event-rules/?name={event_rule_name}", headers=headers)
            er_data = res_er.json() if res_er.status_code == 200 else {}
            er_results = er_data.get("results", [])
            event_rule_exists = len(er_results) > 0
            event_rule_id = er_results[0]["id"] if event_rule_exists else None

            is_active = webhook_exists and event_rule_exists

            return {
                "active": is_active,
                "webhook_id": webhook_id,
                "event_rule_id": event_rule_id,
                "target_url": target_url,
                "status": "connected" if is_active else "not_configured"
            }
        except Exception as e:
            return {
                "active": False,
                "status": "error",
                "error": str(e)
            }

@router.post("/netbox/setup")
async def setup_netbox_webhook(user: Dict[str, Any] = Depends(get_current_user)):
    """
    Configura automáticamente el Webhook y EventRule en NetBox apuntando a NexusDude.
    Permite que la creación de sitios en NetBox dispare la generación automática de submapas al instante.
    """
    headers = {
        "Authorization": f"Token {settings.NETBOX_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    target_url = "http://nexusdude-web:8000/api/webhooks/netbox"
    webhook_name = "NexusDude Realtime Submaps Webhook"
    event_rule_name = "NexusDude - Sync NetBox Sites to Submaps"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Comprobar o crear Webhook
        res_wh = await client.get(f"{settings.NETBOX_URL}/api/extras/webhooks/?name={webhook_name}", headers=headers)
        wh_id = None
        if res_wh.status_code == 200 and res_wh.json().get("results"):
            wh_id = res_wh.json()["results"][0]["id"]
            logger.info(f"Webhook ya existe en NetBox con ID {wh_id}")
        else:
            # Crear Webhook
            payload_wh = {
                "name": webhook_name,
                "description": "Webhook para creación automática de submapas en tiempo real en NexusDude (Puerto 8085)",
                "payload_url": target_url,
                "http_method": "POST",
                "http_content_type": "application/json"
            }
            res_create_wh = await client.post(f"{settings.NETBOX_URL}/api/extras/webhooks/", json=payload_wh, headers=headers)
            if res_create_wh.status_code not in (200, 201):
                raise HTTPException(status_code=500, detail=f"Error creando Webhook en NetBox: {res_create_wh.text}")
            wh_id = res_create_wh.json()["id"]
            logger.info(f"Webhook creado en NetBox exitosamente con ID {wh_id}")

        # 2. Comprobar o crear EventRule
        res_er = await client.get(f"{settings.NETBOX_URL}/api/extras/event-rules/?name={event_rule_name}", headers=headers)
        er_id = None
        if res_er.status_code == 200 and res_er.json().get("results"):
            er_id = res_er.json()["results"][0]["id"]
            logger.info(f"EventRule ya existe en NetBox con ID {er_id}")
        else:
            # Crear EventRule vinculada a 'dcim.site' y 'dcim.device'
            payload_er = {
                "name": event_rule_name,
                "description": "Dispara webhook hacia NexusDude al crear, modificar o borrar sitios o dispositivos",
                "object_types": ["dcim.site", "dcim.device"],
                "event_types": ["object_created", "object_updated", "object_deleted"],
                "enabled": True,
                "action_type": "webhook",
                "action_object_type": "extras.webhook",
                "action_object_id": wh_id
            }
            res_create_er = await client.post(f"{settings.NETBOX_URL}/api/extras/event-rules/", json=payload_er, headers=headers)
            if res_create_er.status_code not in (200, 201):
                raise HTTPException(status_code=500, detail=f"Error creando EventRule en NetBox: {res_create_er.text}")
            er_id = res_create_er.json()["id"]
            logger.info(f"EventRule creada en NetBox exitosamente con ID {er_id}")

        return {
            "status": "success",
            "message": "Webhook y Regla de Eventos configurados exitosamente en NetBox para NexusDude",
            "webhook_id": wh_id,
            "webhook_url": target_url,
            "event_rule_id": er_id
        }

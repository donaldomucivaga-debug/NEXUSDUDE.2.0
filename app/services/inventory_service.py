import httpx
import logging
import asyncio
import uuid
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings
from app.database import get_db_connection

logger = logging.getLogger("nexusdude.inventory")

class InventoryService:
    def __init__(self):
        self._devices_cache: List[Dict[str, Any]] = []
        self._sites_cache: List[Dict[str, Any]] = []
        self._roles_cache: List[Dict[str, Any]] = []
        self._manufacturers_cache: List[Dict[str, Any]] = []
        self._last_refresh: Optional[datetime] = None
        self._is_refreshing: bool = False

    async def get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Token {settings.NETBOX_TOKEN}",
            "Accept": "application/json"
        }

    async def refresh_cache(self, force: bool = False):
        """Descarga dispositivos, sitios y roles desde NetBox en memoria para búsqueda ultrarrápida."""
        if self._is_refreshing:
            while self._is_refreshing:
                await asyncio.sleep(0.2)
            return
        if not force and self._last_refresh and (datetime.now() - self._last_refresh).total_seconds() < 300:
            return

        self._is_refreshing = True
        logger.info("Iniciando sincronización de inventario NetBox...")

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                headers = await self.get_headers()

                # 1. Cargar Sitios
                try:
                    res_sites = await client.get(f"{settings.NETBOX_URL}/api/dcim/sites/?limit=1000", headers=headers)
                    if res_sites.status_code == 200:
                        self._sites_cache = sorted(
                            [{"id": s["id"], "name": s["name"], "slug": s["slug"]} for s in res_sites.json().get("results", [])],
                            key=lambda x: x["name"]
                        )
                except Exception as e:
                    logger.warning(f"Error cargando sitios: {e}")

                # 2. Cargar Roles con sus Colores oficiales de NetBox
                roles_color_map = {}
                try:
                    res_roles = await client.get(f"{settings.NETBOX_URL}/api/dcim/device-roles/?limit=100", headers=headers)
                    if res_roles.status_code == 200:
                        self._roles_cache = sorted(
                            [
                                {
                                    "id": r["id"],
                                    "name": r["name"],
                                    "slug": r["slug"],
                                    "color": (r.get("color") or "").strip()
                                }
                                for r in res_roles.json().get("results", [])
                            ],
                            key=lambda x: x["name"]
                        )
                        roles_color_map = {r["name"].lower(): (r.get("color") or "").strip() for r in self._roles_cache}
                except Exception as e:
                    logger.warning(f"Error cargando roles: {e}")

                # 3. Cargar Fabricantes
                try:
                    res_mfrs = await client.get(f"{settings.NETBOX_URL}/api/dcim/manufacturers/?limit=100", headers=headers)
                    if res_mfrs.status_code == 200:
                        self._manufacturers_cache = sorted(
                            [{"id": m["id"], "name": m["name"], "slug": m["slug"]} for m in res_mfrs.json().get("results", [])],
                            key=lambda x: x["name"]
                        )
                except Exception as e:
                    logger.warning(f"Error cargando fabricantes: {e}")

                # 4. Cargar Dispositivos (paginado de 1000 en 1000)
                all_devices = []
                offset = 0
                limit = 1000

                while True:
                    url = f"{settings.NETBOX_URL}/api/dcim/devices/?limit={limit}&offset={offset}"
                    res = await client.get(url, headers=headers)
                    if res.status_code != 200:
                        logger.error(f"NetBox API retornó {res.status_code}: {res.text[:200]}")
                        break
                    data = res.json()
                    results = data.get("results", [])
                    if not results:
                        break

                    for d in results:
                        primary_ip = d.get("primary_ip") or {}
                        raw_ip = primary_ip.get("address", "")
                        clean_ip = raw_ip.split("/")[0] if raw_ip else ""

                        site_obj = d.get("site") or {}
                        role_obj = d.get("device_role") or d.get("role") or {}
                        role_name = role_obj.get("name", "Desconocido")
                        role_color = roles_color_map.get(role_name.lower(), "")
                        type_obj = d.get("device_type") or {}
                        mfr_obj = type_obj.get("manufacturer") or {}

                        cf = d.get("custom_fields") or {}

                        all_devices.append({
                            "id": d["id"],
                            "name": d.get("name") or f"Device-{d['id']}",
                            "ip": clean_ip,
                            "raw_ip": raw_ip,
                            "site": site_obj.get("name", "Desconocido"),
                            "site_id": site_obj.get("id"),
                            "role": role_name,
                            "role_slug": role_obj.get("slug", ""),
                            "role_color": role_color,
                            "manufacturer": mfr_obj.get("name", "Genérico"),
                            "model": type_obj.get("model", ""),
                            "device_type_id": type_obj.get("id"),
                            "status": d.get("status", {}).get("value", "active") if isinstance(d.get("status"), dict) else str(d.get("status", "active")),
                            "serial": d.get("serial") or "",
                            "custom_fields": cf,
                            "azimuth": cf.get("azimuth"),
                            "tilt": cf.get("tilt"),
                            "height": cf.get("height")
                        })

                    if not data.get("next"):
                        break
                    offset += limit

                self._devices_cache = all_devices
                self._last_refresh = datetime.now()
                logger.info(f"Inventario NetBox actualizado con éxito: {len(self._devices_cache)} dispositivos en caché.")

        except Exception as e:
            logger.error(f"Fallo sincronizando inventario NetBox: {e}")
        finally:
            self._is_refreshing = False

    async def search_devices(
        self,
        query: str = "",
        site: str = "",
        role: str = "",
        manufacturer: str = "",
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Búsqueda ultrarrápida (<5ms) en el inventario cacheado en memoria."""
        if not self._devices_cache:
            await self.refresh_cache()

        filtered = self._devices_cache

        # Filtrar por Sitio
        if site and site.strip():
            s_lower = site.strip().lower()
            filtered = [d for d in filtered if d["site"].lower() == s_lower or str(d.get("site_id")) == site]

        # Filtrar por Rol
        if role and role.strip():
            r_lower = role.strip().lower()
            filtered = [d for d in filtered if d["role"].lower() == r_lower or d.get("role_slug", "").lower() == r_lower]

        # Filtrar por Fabricante
        if manufacturer and manufacturer.strip():
            m_lower = manufacturer.strip().lower()
            filtered = [d for d in filtered if d["manufacturer"].lower() == m_lower]

        # Búsqueda por texto (Nombre, IP, Modelo, Serial)
        if query and query.strip():
            q = query.strip().lower()
            filtered = [
                d for d in filtered
                if q in d["name"].lower()
                or (d["ip"] and q in d["ip"].lower())
                or (d["model"] and q in d["model"].lower())
                or (d["site"] and q in d["site"].lower())
                or (d["serial"] and q in d["serial"].lower())
            ]

        total_count = len(filtered)
        paginated = filtered[offset : offset + limit]

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "results": paginated,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None
        }

    async def get_sites(self) -> List[Dict[str, Any]]:
        if not self._sites_cache:
            await self.refresh_cache()
        return self._sites_cache

    async def get_roles(self) -> List[Dict[str, Any]]:
        if not self._roles_cache:
            await self.refresh_cache()
        return self._roles_cache

    async def get_manufacturers(self) -> List[Dict[str, Any]]:
        if not self._manufacturers_cache:
            await self.refresh_cache()
        return self._manufacturers_cache

    async def get_device_by_id(self, device_id: int) -> Optional[Dict[str, Any]]:
        if not self._devices_cache:
            await self.refresh_cache()
        for d in self._devices_cache:
            if d.get("id") == device_id:
                return d
        return None

    async def get_sites_summary(self) -> List[Dict[str, Any]]:
        """Retorna la lista de sitios ordenados por cantidad de dispositivos registrados."""
        if not self._sites_cache or not self._devices_cache:
            await self.refresh_cache()

        counts: Dict[str, int] = {}
        for d in self._devices_cache:
            s_name = (d.get("site") or "").strip()
            if s_name:
                counts[s_name] = counts.get(s_name, 0) + 1

        summary = []
        for s in self._sites_cache:
            name = s.get("name", "")
            summary.append({
                "id": s.get("id"),
                "name": name,
                "slug": s.get("slug", ""),
                "device_count": counts.get(name, 0)
            })

        return sorted(summary, key=lambda x: (-x["device_count"], x["name"]))

    async def get_device_interfaces(self, device_id: int) -> List[Dict[str, Any]]:
        """
        Obtiene la lista completa de interfaces físicas/lógicas de un dispositivo en NetBox.
        Si el dispositivo no tiene creadas sus interfaces físicas aún (ej. solo mgmt0),
        consulta las plantillas de interfaz de su tipo de dispositivo y las auto-crea o retorna.
        """
        headers = await self.get_headers()
        interfaces_list = []

        try:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                # 1. Consultar interfaces existentes
                res = await client.get(f"{settings.NETBOX_URL}/api/dcim/interfaces/?device_id={device_id}&limit=200", headers=headers)
                existing_ifaces = res.json().get("results", []) if res.status_code == 200 else []

                # Mapear interfaces existentes
                existing_names = {i["name"]: i for i in existing_ifaces}

                # Si solo tiene mgmt0 o no tiene puertos físicos, intentar consultar Interface Templates del Device Type
                dev = await self.get_device_by_id(device_id)
                dev_type_id = None
                if dev and dev.get("device_type_id"):
                    dev_type_id = dev.get("device_type_id")
                elif dev and isinstance(dev.get("device_type"), dict):
                    dev_type_id = dev["device_type"].get("id")

                if not dev_type_id:
                    res_dev = await client.get(f"{settings.NETBOX_URL}/api/dcim/devices/{device_id}/", headers=headers)
                    if res_dev.status_code == 200:
                        d_data = res_dev.json()
                        if isinstance(d_data.get("device_type"), dict):
                            dev_type_id = d_data["device_type"].get("id")
                        elif d_data.get("device_type_id"):
                            dev_type_id = d_data.get("device_type_id")

                if dev_type_id:
                    res_tmpl = await client.get(f"{settings.NETBOX_URL}/api/dcim/interface-templates/?device_type_id={dev_type_id}&limit=100", headers=headers)
                    if res_tmpl.status_code == 200:
                        templates = res_tmpl.json().get("results", [])
                        if templates and len(existing_names) < len(templates):
                            for tmpl in templates:
                                t_name = tmpl["name"]
                                if t_name not in existing_names:
                                    try:
                                        t_type = tmpl.get("type", {}).get("value") if isinstance(tmpl.get("type"), dict) else (tmpl.get("type") or "1000base-t")
                                        # Normalizar tipo para NetBox 4.x
                                        if "10gbase-x-sfp" in str(t_type):
                                            t_type = "10gbase-x-sfpp"
                                        res_new = await client.post(f"{settings.NETBOX_URL}/api/dcim/interfaces/", headers=headers, json={
                                            "device": device_id,
                                            "name": t_name,
                                            "type": t_type,
                                            "mgmt_only": bool(tmpl.get("mgmt_only", False))
                                        })
                                        if res_new.status_code == 201:
                                            new_if = res_new.json()
                                            existing_ifaces.append(new_if)
                                            existing_names[t_name] = new_if
                                    except Exception as e:
                                        logger.warning(f"No se pudo auto-crear interfaz {t_name} en device {device_id}: {e}")

                # 2. Formatear cada interfaz con su estado de conexión
                for i in existing_ifaces:
                    cable = i.get("cable")
                    is_conn = bool(cable)
                    conn_dev = None
                    conn_if = None
                    cable_id = cable.get("id") if isinstance(cable, dict) else (cable if isinstance(cable, int) else None)
                    cable_status = cable.get("status", {}).get("value") if isinstance(cable, dict) and isinstance(cable.get("status"), dict) else "connected"
                    cable_type = cable.get("type", {}).get("value") if isinstance(cable, dict) and isinstance(cable.get("type"), dict) else "cat6"

                    # Si está conectada, obtener la otra punta (link_peers / connected_endpoints)
                    conn_dev_id = None
                    conn_if_id = None
                    link_peers = i.get("link_peers", []) or i.get("connected_endpoints", [])
                    if link_peers and len(link_peers) > 0:
                        peer = link_peers[0]
                        if isinstance(peer, dict):
                            conn_if = peer.get("name")
                            conn_if_id = peer.get("id")
                            if isinstance(peer.get("device"), dict):
                                conn_dev = peer.get("device", {}).get("name")
                                conn_dev_id = peer.get("device", {}).get("id")

                    interfaces_list.append({
                        "id": i.get("id"),
                        "name": i.get("name"),
                        "type": i.get("type", {}).get("value") if isinstance(i.get("type"), dict) else (i.get("type") or "1000base-t"),
                        "enabled": i.get("enabled", True),
                        "mgmt_only": i.get("mgmt_only", False),
                        "is_connected": is_conn or bool(conn_dev),
                        "connected_device": conn_dev,
                        "connected_device_id": conn_dev_id,
                        "connected_interface": conn_if,
                        "connected_interface_id": conn_if_id,
                        "cable_id": cable_id,
                        "cable_status": cable_status,
                        "cable_type": cable_type
                    })

        except Exception as e:
            logger.error(f"Error consultando interfaces para device {device_id}: {e}")

        # Ordenar puertos de forma natural (GE1, GE2... SFP1...)
        return sorted(interfaces_list, key=lambda x: (x.get("mgmt_only", False), x.get("name", "")))

    async def get_or_create_interface(self, device_id: int, interface_name: str, if_type: str = "1000base-t") -> Dict[str, Any]:
        """Obtiene o crea una interfaz específica en NetBox."""
        headers = await self.get_headers()
        clean_name = interface_name.strip()
        # Normalizar tipo para NetBox 4.x
        if "10gbase-x-sfp" in str(if_type) and not str(if_type).endswith("sfpp"):
            if_type = "10gbase-x-sfpp"

        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            res = await client.get(f"{settings.NETBOX_URL}/api/dcim/interfaces/", headers=headers, params={"device_id": device_id, "name": clean_name})
            if res.status_code == 200:
                results = res.json().get("results", [])
                if results:
                    return results[0]

            # Crear interfaz si no existe
            res_create = await client.post(f"{settings.NETBOX_URL}/api/dcim/interfaces/", headers=headers, json={
                "device": device_id,
                "name": clean_name,
                "type": if_type
            })
            if res_create.status_code in (200, 201):
                return res_create.json()
            else:
                logger.error(f"Error creando interfaz {clean_name} en NetBox: {res_create.text}")
                raise Exception(f"No se pudo crear interfaz {clean_name} en NetBox: {res_create.text}")

    async def create_netbox_cable(self, dev1_id: int, if1_name: str, dev2_id: int, if2_name: str, cable_type: str = "cat6", description: str = "") -> Dict[str, Any]:
        """Crea un Cable físico en NetBox entre dos dispositivos e interfaces."""
        headers = await self.get_headers()
        if1 = await self.get_or_create_interface(dev1_id, if1_name)
        if2 = await self.get_or_create_interface(dev2_id, if2_name)

        cable_payload = {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": if1["id"]}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": if2["id"]}],
            "status": "connected",
            "type": cable_type or "cat6",
            "description": description or "Enlace creado desde NexusDude Topology"
        }

        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            res = await client.post(f"{settings.NETBOX_URL}/api/dcim/cables/", headers=headers, json=cable_payload)
            if res.status_code in (200, 201):
                cable_data = res.json()
                logger.info(f"Cable creado exitosamente en NetBox: ID {cable_data.get('id')} ({if1_name} <-> {if2_name})")
                return {
                    "cable_id": cable_data.get("id"),
                    "if1_id": if1["id"],
                    "if2_id": if2["id"],
                    "cable_status": cable_data.get("status", {}).get("value") if isinstance(cable_data.get("status"), dict) else "connected",
                    "cable_type": cable_data.get("type", {}).get("value") if isinstance(cable_data.get("type"), dict) else cable_type
                }
            else:
                logger.error(f"Error creando cable en NetBox: {res.status_code} - {res.text}")
                raise Exception(f"NetBox no pudo crear el cable: {res.text}")

    async def get_netbox_cable(self, cable_id: int) -> Optional[Dict[str, Any]]:
        """Obtiene los datos completos de un cable desde NetBox por su ID."""
        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                res = await client.get(f"{settings.NETBOX_URL}/api/dcim/cables/{cable_id}/", headers=headers)
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error(f"Error consultando cable {cable_id} en NetBox: {e}")
        return None

    async def get_locations(self) -> List[Dict[str, Any]]:
        """Obtiene todas las ubicaciones registradas en NetBox."""
        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                res = await client.get(f"{settings.NETBOX_URL}/api/dcim/locations/?limit=1000", headers=headers)
                if res.status_code == 200:
                    return res.json().get("results", [])
        except Exception as e:
            logger.error(f"Error consultando ubicaciones en NetBox: {e}")
        return []

    async def get_all_cables(self) -> List[Dict[str, Any]]:
        """Obtiene todos los cables registrados en NetBox."""
        headers = await self.get_headers()
        cables = []
        offset = 0
        limit = 1000
        try:
            async with httpx.AsyncClient(verify=False, timeout=25.0) as client:
                while True:
                    res = await client.get(f"{settings.NETBOX_URL}/api/dcim/cables/?limit={limit}&offset={offset}", headers=headers)
                    if res.status_code != 200:
                        break
                    data = res.json()
                    results = data.get("results", [])
                    if not results:
                        break
                    cables.extend(results)
                    if not data.get("next"):
                        break
                    offset += limit
        except Exception as e:
            logger.error(f"Error consultando cables en NetBox: {e}")
        return cables

    async def get_wireless_links(self) -> List[Dict[str, Any]]:
        """Obtiene todos los enlaces inalámbricos registrados en NetBox."""
        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                res = await client.get(f"{settings.NETBOX_URL}/api/wireless/wireless-links/?limit=1000", headers=headers)
                if res.status_code == 200:
                    return res.json().get("results", [])
        except Exception as e:
            logger.error(f"Error consultando wireless-links en NetBox: {e}")
        return []

    async def sync_netbox_hierarchy_and_maps(self) -> Dict[str, Any]:
        """
        Sincroniza la estructura de ubicaciones (Locations) y Sitios de NetBox con los Mapas de NexusDude:
        1. Importa ubicaciones y sitios como mapas.
        2. Determina la relación jerárquica padre-hijo basada en location.parent.
        3. Genera automáticamente los nodos portal de navegación (Padre -> Submapa y Submapa -> Padre).
        4. Actualiza la tabla map_hierarchy para reflejar la navegación completa.
        """
        locations = await self.get_locations()
        sites = await self.get_sites()
        
        created_maps = 0
        updated_maps = 0
        portals_created = 0

        async with get_db_connection() as db:
            c_maps = await db.execute("SELECT id, name, parent_map_id, netbox_site_id, netbox_location_id, site_name FROM maps")
            existing_maps = [dict(r) for r in await c_maps.fetchall()]
            
            map_by_id = {m["id"]: m for m in existing_maps}
            map_by_site_id = {m["netbox_site_id"]: m for m in existing_maps if m.get("netbox_site_id")}
            map_by_loc_id = {m["netbox_location_id"]: m for m in existing_maps if m.get("netbox_location_id")}
            map_by_name = {m["name"].strip().lower(): m for m in existing_maps}

            # 1. Asegurar mapas para Ubicaciones (Locations)
            loc_to_map_id = {}
            for loc in locations:
                loc_id = loc["id"]
                loc_name = loc["name"]
                site_data = loc.get("site") or {}
                site_id = site_data.get("id")
                site_name = site_data.get("name", "")

                matched_map = map_by_loc_id.get(loc_id) or map_by_name.get(loc_name.strip().lower())
                if matched_map:
                    map_id = matched_map["id"]
                    await db.execute("""
                        UPDATE maps
                        SET netbox_location_id = ?, netbox_site_id = COALESCE(?, netbox_site_id), site_name = COALESCE(?, site_name), updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (loc_id, site_id, site_name, map_id))
                    loc_to_map_id[loc_id] = map_id
                    updated_maps += 1
                else:
                    new_map_id = f"map-{uuid.uuid4().hex[:8]}"
                    await db.execute("""
                        INSERT INTO maps (id, name, description, parent_map_id, grid_size, netbox_location_id, netbox_site_id, site_name)
                        VALUES (?, ?, ?, NULL, 20, ?, ?, ?)
                    """, (new_map_id, loc_name, f"Ubicación NetBox: {loc_name}", loc_id, site_id, site_name))
                    loc_to_map_id[loc_id] = new_map_id
                    created_maps += 1

            # 2. Establecer jerarquía basada en location.parent
            for loc in locations:
                loc_id = loc["id"]
                child_map_id = loc_to_map_id.get(loc_id)
                parent_loc = loc.get("parent")
                if parent_loc and isinstance(parent_loc, dict):
                    parent_loc_id = parent_loc.get("id")
                    parent_map_id = loc_to_map_id.get(parent_loc_id)
                    if not parent_map_id:
                        # Si el padre es un sitio
                        site_data = loc.get("site") or {}
                        if site_data.get("id") in map_by_site_id:
                            parent_map_id = map_by_site_id[site_data["id"]]["id"]

                    if parent_map_id and child_map_id and parent_map_id != child_map_id:
                        await db.execute("UPDATE maps SET parent_map_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (parent_map_id, child_map_id))
                        # Registrar en map_hierarchy
                        c_h = await db.execute("SELECT id FROM map_hierarchy WHERE child_map_id = ? AND parent_map_id = ?", (child_map_id, parent_map_id))
                        if not await c_h.fetchone():
                            h_id = f"h-{uuid.uuid4().hex[:8]}"
                            await db.execute("INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, is_primary) VALUES (?, ?, ?, 1)", (h_id, parent_map_id, child_map_id))

            # 3. Generar portales de navegación automáticamente para todos los mapas padre-hijo
            c_all_maps = await db.execute("SELECT id, name, parent_map_id FROM maps WHERE parent_map_id IS NOT NULL")
            hier_maps = [dict(r) for r in await c_all_maps.fetchall()]

            for hm in hier_maps:
                child_id = hm["id"]
                child_name = hm["name"]
                parent_id = hm["parent_map_id"]

                c_parent = await db.execute("SELECT id, name FROM maps WHERE id = ?", (parent_id,))
                p_row = await c_parent.fetchone()
                if not p_row:
                    continue
                parent_name = p_row["name"]

                # A. Portal en Mapa Padre apuntando a Submapa
                c_sub_node = await db.execute("SELECT id, extra_data FROM nodes WHERE map_id = ? AND (device_type = 'submap' OR name LIKE ?)", (parent_id, f"%{child_name}%"))
                existing_sub_node = None
                for sn in await c_sub_node.fetchall():
                    ed = {}
                    if sn["extra_data"]:
                        try:
                            ed = json.loads(sn["extra_data"]) if isinstance(sn["extra_data"], str) else sn["extra_data"]
                        except Exception:
                            pass
                    if ed.get("target_map_id") == child_id or child_name.lower() in sn["id"]:
                        existing_sub_node = sn
                        break

                if not existing_sub_node:
                    new_sub_node_id = f"node-{uuid.uuid4().hex[:8]}"
                    extra_sub = {"target_map_id": child_id}
                    await db.execute("""
                        INSERT INTO nodes (id, map_id, name, device_type, x, y, extra_data)
                        VALUES (?, ?, ?, 'submap', 600.0, 150.0, ?)
                    """, (new_sub_node_id, parent_id, f"📁 {child_name}", json.dumps(extra_sub)))
                    portals_created += 1

                # B. Portal en Submapa apuntando a Mapa Padre (Portal de Retorno)
                c_parent_node = await db.execute("SELECT id, extra_data FROM nodes WHERE map_id = ? AND (device_type = 'parent_map' OR extra_data LIKE '%is_parent_shortcut%')", (child_id,))
                existing_p_node = await c_parent_node.fetchone()
                if not existing_p_node:
                    new_p_node_id = f"node-{uuid.uuid4().hex[:8]}"
                    extra_parent = {
                        "target_map_id": parent_id,
                        "is_parent_shortcut": True,
                        "parent_map_name": parent_name
                    }
                    await db.execute("""
                        INSERT INTO nodes (id, map_id, name, device_type, x, y, extra_data)
                        VALUES (?, ?, ?, 'parent_map', 100.0, 80.0, ?)
                    """, (new_p_node_id, child_id, f"📁 ⬆ {parent_name}", json.dumps(extra_parent)))
                    portals_created += 1

            await db.commit()

        return {
            "status": "success",
            "created_maps": created_maps,
            "updated_maps": updated_maps,
            "portals_created": portals_created,
            "message": f"Jerarquía NetBox sincronizada: {created_maps} mapas creados, {updated_maps} actualizados, {portals_created} portales generados."
        }

    async def sync_all_links_from_netbox(self, map_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Sincroniza masivamente cables y wireless-links de NetBox hacia aristas de NexusDude:
        - Conexiones Intra-mapa: actualiza/crea aristas directas entre nodos del mismo mapa.
        - Conexiones Inter-mapa (ej. AP en Mapa Padre ➔ CPE en Submapa):
          Crea/actualiza el par de aristas intermapa vinculadas por un pin_id consistente
          a través de los nodos portal (📁 Submapa y 📁 ⬆ Padre).
        """
        cables = await self.get_all_cables()
        wireless_links = await self.get_wireless_links()

        intra_links_synced = 0
        inter_links_synced = 0

        async with get_db_connection() as db:
            # Cargar todos los mapas
            c_maps = await db.execute("SELECT id, name, parent_map_id FROM maps")
            all_maps_dict = {m["id"]: dict(m) for m in await c_maps.fetchall()}

            # Cargar todos los nodos
            c_nodes = await db.execute("SELECT id, map_id, device_id, name, device_type, extra_data FROM nodes")
            all_nodes = [dict(r) for r in await c_nodes.fetchall()]

            # Mapear device_id a lista de nodos (un dispositivo puede estar en un mapa específico)
            dev_to_nodes: Dict[int, List[Dict[str, Any]]] = {}
            # Mapear portales por mapa: portals_by_map[map_id][target_map_id] = node
            portals_by_map: Dict[str, Dict[str, Dict[str, Any]]] = {}

            for n in all_nodes:
                did = n.get("device_id")
                if did:
                    dev_to_nodes.setdefault(did, []).append(n)

                dtype = n.get("device_type")
                if dtype in ("submap", "parent_map"):
                    ed = {}
                    if n.get("extra_data"):
                        try:
                            ed = json.loads(n["extra_data"]) if isinstance(n["extra_data"], str) else n["extra_data"]
                        except Exception:
                            pass
                    target_mid = ed.get("target_map_id")
                    if target_mid:
                        portals_by_map.setdefault(n["map_id"], {})[target_mid] = n

            # Cargar enlaces existentes
            c_links = await db.execute("SELECT id, map_id, source_node_id, target_node_id, netbox_cable_id, extra_data FROM links")
            existing_links = [dict(r) for r in await c_links.fetchall()]

            # Mapear cables ya sincronizados: cable_id -> lista de enlaces
            cable_to_links: Dict[int, List[Dict[str, Any]]] = {}
            for l in existing_links:
                cid = l.get("netbox_cable_id")
                if cid:
                    cable_to_links.setdefault(cid, []).append(l)

            # Procesar Cables Físicos de NetBox
            for cable in cables:
                cid = cable.get("id")
                a_terms = cable.get("a_terminations", [])
                b_terms = cable.get("b_terminations", [])
                if not a_terms or not b_terms:
                    continue

                obj_a = a_terms[0].get("object", {})
                obj_b = b_terms[0].get("object", {})

                dev_a_id = obj_a.get("device", {}).get("id")
                if_a_name = obj_a.get("name")
                if_a_id = a_terms[0].get("object_id")

                dev_b_id = obj_b.get("device", {}).get("id")
                if_b_name = obj_b.get("name")
                if_b_id = b_terms[0].get("object_id")

                if not dev_a_id or not dev_b_id or dev_a_id not in dev_to_nodes or dev_b_id not in dev_to_nodes:
                    continue

                c_type = cable.get("type", {}).get("value") if isinstance(cable.get("type"), dict) else (cable.get("type") or "cat6")
                c_status = cable.get("status", {}).get("value") if isinstance(cable.get("status"), dict) else (cable.get("status") or "connected")

                nodes_a = dev_to_nodes[dev_a_id]
                nodes_b = dev_to_nodes[dev_b_id]

                for n_a in nodes_a:
                    for n_b in nodes_b:
                        map_a = n_a["map_id"]
                        map_b = n_b["map_id"]

                        # Si se especificó un map_id de filtro, omitir si ninguno coincide
                        if map_id and map_a != map_id and map_b != map_id:
                            continue

                        if map_a == map_b:
                            # ─── CONEXIÓN INTRA-MAPA (Mismo Lienzo) ──────────────────────
                            # Buscar si ya existe enlace entre estos dos nodos
                            c_exist = await db.execute("""
                                SELECT id, netbox_cable_id FROM links
                                WHERE map_id = ? AND ((source_node_id = ? AND target_node_id = ?) OR (source_node_id = ? AND target_node_id = ?))
                            """, (map_a, n_a["id"], n_b["id"], n_b["id"], n_a["id"]))
                            existing_link = await c_exist.fetchone()

                            if existing_link:
                                await db.execute("""
                                    UPDATE links
                                    SET source_interface = ?, target_interface = ?,
                                        source_interface_id = ?, target_interface_id = ?,
                                        netbox_cable_id = ?, cable_type = ?, cable_status = ?,
                                        status = 'ok', updated_at = CURRENT_TIMESTAMP
                                    WHERE id = ?
                                """, (if_a_name, if_b_name, if_a_id, if_b_id, cid, c_type, c_status, existing_link["id"]))
                            else:
                                new_link_id = f"link-{uuid.uuid4().hex[:8]}"
                                await db.execute("""
                                    INSERT INTO links (
                                        id, map_id, source_node_id, target_node_id,
                                        source_interface, target_interface,
                                        source_interface_id, target_interface_id,
                                        netbox_cable_id, cable_type, cable_status, status
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok')
                                """, (new_link_id, map_a, n_a["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id, cid, c_type, c_status))
                            intra_links_synced += 1

                        else:
                            # ─── CONEXIÓN INTER-MAPA (AP Mapa A ➔ CPE Mapa B) ──────────
                            pin_id = f"pin-nb-cable-{cid}"

                            # 1. Asegurar portal en Mapa A apuntando a Mapa B
                            portal_a = portals_by_map.get(map_a, {}).get(map_b)
                            if not portal_a:
                                b_map_name = all_maps_dict.get(map_b, {}).get("name", "Submapa")
                                new_portal_id = f"node-{uuid.uuid4().hex[:8]}"
                                extra_p = {"target_map_id": map_b}
                                await db.execute("""
                                    INSERT INTO nodes (id, map_id, name, device_type, x, y, extra_data)
                                    VALUES (?, ?, ?, 'submap', 650.0, 150.0, ?)
                                """, (new_portal_id, map_a, f"📁 {b_map_name}", json.dumps(extra_p)))
                                portal_a = {"id": new_portal_id, "map_id": map_a, "name": f"📁 {b_map_name}", "device_type": "submap"}
                                portals_by_map.setdefault(map_a, {})[map_b] = portal_a

                            # 2. Asegurar portal en Mapa B apuntando a Mapa A
                            portal_b = portals_by_map.get(map_b, {}).get(map_a)
                            if not portal_b:
                                a_map_name = all_maps_dict.get(map_a, {}).get("name", "Mapa Superior")
                                new_portal_id = f"node-{uuid.uuid4().hex[:8]}"
                                extra_p = {"target_map_id": map_a, "is_parent_shortcut": True, "parent_map_name": a_map_name}
                                await db.execute("""
                                    INSERT INTO nodes (id, map_id, name, device_type, x, y, extra_data)
                                    VALUES (?, ?, ?, 'parent_map', 100.0, 80.0, ?)
                                """, (new_portal_id, map_b, f"📁 ⬆ {a_map_name}", json.dumps(extra_p)))
                                portal_b = {"id": new_portal_id, "map_id": map_b, "name": f"📁 ⬆ {a_map_name}", "device_type": "parent_map"}
                                portals_by_map.setdefault(map_b, {})[map_a] = portal_b

                            # 3. Arista en Mapa A (Equipo A ➔ Portal B)
                            extra_link_a = {
                                "is_intermap": True,
                                "pin_id": pin_id,
                                "local_node_id": n_a["id"],
                                "local_node_name": n_a["name"],
                                "remote_node_id": n_b["id"],
                                "remote_node_name": n_b["name"],
                                "remote_map_id": map_b,
                                "target_submap_device_id": dev_b_id,
                                "target_submap_device_name": n_b["name"],
                                "direction": "source_to_target",
                                "netbox_cable_id": cid,
                                "sync_zabbix": True
                            }
                            c_exist_a = await db.execute("""
                                SELECT id FROM links WHERE map_id = ? AND source_node_id = ? AND target_node_id = ?
                            """, (map_a, n_a["id"], portal_a["id"]))
                            ex_a = await c_exist_a.fetchone()
                            if ex_a:
                                await db.execute("UPDATE links SET extra_data = ?, netbox_cable_id = ?, source_interface = ?, target_interface = ?, status = 'ok', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(extra_link_a), cid, if_a_name, if_b_name, ex_a["id"]))
                            else:
                                await db.execute("""
                                    INSERT INTO links (id, map_id, source_node_id, target_node_id, source_interface, target_interface, source_interface_id, target_interface_id, netbox_cable_id, cable_type, cable_status, status, extra_data)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                                """, (f"link-{uuid.uuid4().hex[:8]}", map_a, n_a["id"], portal_a["id"], if_a_name, if_b_name, if_a_id, if_b_id, cid, c_type, c_status, json.dumps(extra_link_a)))

                            # 4. Arista en Mapa B (Portal A ➔ Equipo B)
                            extra_link_b = {
                                "is_intermap": True,
                                "pin_id": pin_id,
                                "local_node_id": n_b["id"],
                                "local_node_name": n_b["name"],
                                "remote_node_id": n_a["id"],
                                "remote_node_name": n_a["name"],
                                "remote_map_id": map_a,
                                "target_submap_device_id": dev_a_id,
                                "target_submap_device_name": n_a["name"],
                                "direction": "source_to_target",
                                "netbox_cable_id": cid,
                                "sync_zabbix": True
                            }
                            c_exist_b = await db.execute("""
                                SELECT id FROM links WHERE map_id = ? AND source_node_id = ? AND target_node_id = ?
                            """, (map_b, portal_b["id"], n_b["id"]))
                            ex_b = await c_exist_b.fetchone()
                            if ex_b:
                                await db.execute("UPDATE links SET extra_data = ?, netbox_cable_id = ?, source_interface = ?, target_interface = ?, status = 'ok', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps(extra_link_b), cid, if_a_name, if_b_name, ex_b["id"]))
                            else:
                                await db.execute("""
                                    INSERT INTO links (id, map_id, source_node_id, target_node_id, source_interface, target_interface, source_interface_id, target_interface_id, netbox_cable_id, cable_type, cable_status, status, extra_data)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                                """, (f"link-{uuid.uuid4().hex[:8]}", map_b, portal_b["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id, cid, c_type, c_status, json.dumps(extra_link_b)))

                            inter_links_synced += 1

            # Procesar Wireless Links de NetBox
            for wlink in wireless_links:
                wid = wlink.get("id")
                if_a = wlink.get("interface_a") or {}
                if_b = wlink.get("interface_b") or {}

                dev_a_id = if_a.get("device", {}).get("id")
                if_a_name = if_a.get("name")
                if_a_id = if_a.get("id")

                dev_b_id = if_b.get("device", {}).get("id")
                if_b_name = if_b.get("name")
                if_b_id = if_b.get("id")

                if not dev_a_id or not dev_b_id or dev_a_id not in dev_to_nodes or dev_b_id not in dev_to_nodes:
                    continue

                nodes_a = dev_to_nodes[dev_a_id]
                nodes_b = dev_to_nodes[dev_b_id]

                for n_a in nodes_a:
                    for n_b in nodes_b:
                        map_a = n_a["map_id"]
                        map_b = n_b["map_id"]

                        if map_id and map_a != map_id and map_b != map_id:
                            continue

                        pin_id = f"pin-nb-wlink-{wid}"

                        if map_a == map_b:
                            c_exist = await db.execute("""
                                SELECT id FROM links
                                WHERE map_id = ? AND ((source_node_id = ? AND target_node_id = ?) OR (source_node_id = ? AND target_node_id = ?))
                            """, (map_a, n_a["id"], n_b["id"], n_b["id"], n_a["id"]))
                            existing_link = await c_exist.fetchone()
                            if existing_link:
                                await db.execute("""
                                    UPDATE links
                                    SET source_interface = ?, target_interface = ?,
                                        source_interface_id = ?, target_interface_id = ?,
                                        cable_type = 'wireless', cable_status = 'connected',
                                        status = 'ok', updated_at = CURRENT_TIMESTAMP
                                    WHERE id = ?
                                """, (if_a_name, if_b_name, if_a_id, if_b_id, existing_link["id"]))
                            else:
                                await db.execute("""
                                    INSERT INTO links (
                                        id, map_id, source_node_id, target_node_id,
                                        source_interface, target_interface,
                                        source_interface_id, target_interface_id,
                                        cable_type, cable_status, status
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'wireless', 'connected', 'ok')
                                """, (f"link-{uuid.uuid4().hex[:8]}", map_a, n_a["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id))
                            intra_links_synced += 1
                        else:
                            # Inter-map wireless link
                            portal_a = portals_by_map.get(map_a, {}).get(map_b)
                            portal_b = portals_by_map.get(map_b, {}).get(map_a)
                            if portal_a and portal_b:
                                extra_a = {
                                    "is_intermap": True,
                                    "pin_id": pin_id,
                                    "local_node_id": n_a["id"],
                                    "local_node_name": n_a["name"],
                                    "remote_node_id": n_b["id"],
                                    "remote_node_name": n_b["name"],
                                    "remote_map_id": map_b,
                                    "direction": "source_to_target",
                                    "sync_zabbix": True
                                }
                                extra_b = {
                                    "is_intermap": True,
                                    "pin_id": pin_id,
                                    "local_node_id": n_b["id"],
                                    "local_node_name": n_b["name"],
                                    "remote_node_id": n_a["id"],
                                    "remote_node_name": n_a["name"],
                                    "remote_map_id": map_a,
                                    "direction": "source_to_target",
                                    "sync_zabbix": True
                                }
                                await db.execute("""
                                    INSERT INTO links (id, map_id, source_node_id, target_node_id, source_interface, target_interface, cable_type, cable_status, status, extra_data)
                                    VALUES (?, ?, ?, ?, ?, ?, 'wireless', 'connected', 'ok', ?)
                                """, (f"link-{uuid.uuid4().hex[:8]}", map_a, n_a["id"], portal_a["id"], if_a_name, if_b_name, json.dumps(extra_a)))
                                await db.execute("""
                                    INSERT INTO links (id, map_id, source_node_id, target_node_id, source_interface, target_interface, cable_type, cable_status, status, extra_data)
                                    VALUES (?, ?, ?, ?, ?, ?, 'wireless', 'connected', 'ok', ?)
                                """, (f"link-{uuid.uuid4().hex[:8]}", map_b, portal_b["id"], n_b["id"], if_a_name, if_b_name, json.dumps(extra_b)))
                                inter_links_synced += 1

            await db.commit()

        return {
            "status": "success",
            "intra_links_synced": intra_links_synced,
            "inter_links_synced": inter_links_synced,
            "message": f"Conexiones NetBox sincronizadas: {intra_links_synced} intra-mapa, {inter_links_synced} inter-mapa."
        }

    async def delete_netbox_cable(self, cable_id: int) -> bool:
        """Elimina un Cable de NetBox al desconectar un enlace en NexusDude."""
        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                res = await client.delete(f"{settings.NETBOX_URL}/api/dcim/cables/{cable_id}/", headers=headers)
                if res.status_code in (200, 204):
                    logger.info(f"Cable ID {cable_id} eliminado exitosamente de NetBox.")
                    return True
                elif res.status_code == 404:
                    logger.warning(f"Cable ID {cable_id} no existía en NetBox.")
                    return True
                else:
                    logger.error(f"Error eliminando cable ID {cable_id} en NetBox: {res.status_code} - {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Excepción eliminando cable NetBox: {e}")
            return False

inventory_service = InventoryService()




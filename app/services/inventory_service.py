import httpx
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings

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



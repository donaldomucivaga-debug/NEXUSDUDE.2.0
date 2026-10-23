import httpx
import logging
import asyncio
import uuid
import json
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime
from app.config import settings
from app.database import get_db_connection

logger = logging.getLogger("nexusdude.inventory")

class InventoryService:
    def __init__(self):
        self._devices_cache: List[Dict[str, Any]] = []
        self._sites_cache: List[Dict[str, Any]] = []
        self._site_status_map: Dict[int, str] = {}
        self._roles_cache: List[Dict[str, Any]] = []
        self._manufacturers_cache: List[Dict[str, Any]] = []
        self._wireless_links_cache: Optional[List[Dict[str, Any]]] = None
        self._wireless_links_last_fetch: Optional[datetime] = None
        self._cables_cache: Optional[List[Dict[str, Any]]] = None
        self._cables_last_fetch: Optional[datetime] = None
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

                # 1. Cargar Sitios (mapeando estados y excluyendo inactivos del catálogo activo)
                try:
                    res_sites = await client.get(f"{settings.NETBOX_URL}/api/dcim/sites/?limit=1000", headers=headers)
                    if res_sites.status_code == 200:
                        results = res_sites.json().get("results", [])
                        self._sites_cache = sorted(
                            [
                                {"id": s["id"], "name": s["name"], "slug": s["slug"]}
                                for s in results
                                if (s.get("status", {}).get("value") if isinstance(s.get("status"), dict) else s.get("status")) == "active"
                            ],
                            key=lambda x: x["name"]
                        )
                        self._site_status_map = {
                            s["id"]: (s.get("status", {}).get("value") if isinstance(s.get("status"), dict) else s.get("status") or "active")
                            for s in results
                        }
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

    def is_site_retired(self, netbox_site_id: Optional[int]) -> bool:
        """Determina si un ID de sitio NetBox se encuentra en estado retirado o fuera de servicio."""
        if not netbox_site_id:
            return False
        status = (self._site_status_map.get(netbox_site_id) or "active").lower()
        return status in ("retired", "decommissioning", "deprecate", "offline")

    async def get_roles(self) -> List[Dict[str, Any]]:
        if not self._roles_cache:
            await self.refresh_cache()
        return self._roles_cache

    async def get_manufacturers(self) -> List[Dict[str, Any]]:
        if not self._manufacturers_cache:
            await self.refresh_cache()
        return self._manufacturers_cache

    async def get_device_by_id(self, device_id: int, force_live: bool = False) -> Optional[Dict[str, Any]]:
        if force_live:
            try:
                headers = await self.get_headers()
                async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                    res = await client.get(f"{settings.NETBOX_URL}/api/dcim/devices/{device_id}/", headers=headers)
                    if res.status_code == 200:
                        d = res.json()
                        primary_ip = d.get("primary_ip") or {}
                        raw_ip = primary_ip.get("address", "")
                        clean_ip = raw_ip.split("/")[0] if raw_ip else ""
                        site_obj = d.get("site") or {}
                        role_obj = d.get("device_role") or d.get("role") or {}
                        role_name = role_obj.get("name", "Desconocido")
                        type_obj = d.get("device_type") or {}
                        mfr_obj = type_obj.get("manufacturer") or {}
                        cf = d.get("custom_fields") or {}
                        dev_info = {
                            "id": d["id"],
                            "name": d.get("name") or f"Device-{d['id']}",
                            "ip": clean_ip,
                            "raw_ip": raw_ip,
                            "site": site_obj.get("name", "Desconocido"),
                            "site_id": site_obj.get("id"),
                            "role": role_name,
                            "role_slug": role_obj.get("slug", ""),
                            "role_color": (role_obj.get("color") or "").strip(),
                            "manufacturer": mfr_obj.get("name", "Genérico"),
                            "model": type_obj.get("model", ""),
                            "device_type_id": type_obj.get("id"),
                            "status": d.get("status", {}).get("value", "active") if isinstance(d.get("status"), dict) else str(d.get("status", "active")),
                            "serial": d.get("serial") or "",
                            "custom_fields": cf,
                            "azimuth": cf.get("azimuth"),
                            "tilt": cf.get("tilt"),
                            "height": cf.get("height")
                        }
                        # Actualizar caché
                        found_in_cache = False
                        for idx, cached in enumerate(self._devices_cache):
                            if cached.get("id") == device_id:
                                self._devices_cache[idx] = dev_info
                                found_in_cache = True
                                break
                        if not found_in_cache:
                            self._devices_cache.append(dev_info)
                        return dev_info
            except Exception as e:
                logger.warning(f"Error consultando dispositivo {device_id} en vivo desde NetBox: {e}")

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
        Sincroniza el tipo de dispositivo en tiempo real, sanea interfaces inalámbricas huérfanas
        en infraestructura cableada/fibra (OLT, Switch, Router) y auto-crea los puertos oficiales
        según las plantillas del Device Type si están pendientes.
        """
        headers = await self.get_headers()
        interfaces_list = []

        try:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                # 1. Consultar dispositivo en tiempo real desde NetBox para obtener modelo y rol actualizados
                dev_type_id = None
                dev_role_slug = ""
                dev_role_name = ""
                res_dev = await client.get(f"{settings.NETBOX_URL}/api/dcim/devices/{device_id}/", headers=headers)
                if res_dev.status_code == 200:
                    d_data = res_dev.json()
                    type_obj = d_data.get("device_type") or {}
                    mfr_obj = type_obj.get("manufacturer") or {}
                    role_obj = d_data.get("device_role") or d_data.get("role") or {}
                    dev_type_id = type_obj.get("id") or d_data.get("device_type_id")
                    dev_role_slug = role_obj.get("slug", "").lower()
                    dev_role_name = role_obj.get("name", "").lower()

                    # Mantener caché en memoria sincronizado en tiempo real
                    for d in self._devices_cache:
                        if d.get("id") == device_id:
                            d["device_type_id"] = dev_type_id
                            d["model"] = type_obj.get("model", d.get("model"))
                            d["manufacturer"] = mfr_obj.get("name", d.get("manufacturer"))
                            d["role"] = role_obj.get("name", d.get("role"))
                            d["role_slug"] = dev_role_slug
                            break

                # 2. Consultar interfaces existentes en NetBox
                res = await client.get(f"{settings.NETBOX_URL}/api/dcim/interfaces/?device_id={device_id}&limit=200", headers=headers)
                existing_ifaces = res.json().get("results", []) if res.status_code == 200 else []
                existing_names = {i["name"]: i for i in existing_ifaces}

                # 3. Limpieza automática de interfaces Wireless huérfanas en infraestructura cableada/fibra
                is_wired_infrastructure = any(r in dev_role_slug or r in dev_role_name for r in ["olt", "switch", "router", "core"])
                if is_wired_infrastructure:
                    for iface in list(existing_ifaces):
                        if_name = iface.get("name", "")
                        if_type_val = iface.get("type", {}).get("value") if isinstance(iface.get("type"), dict) else (iface.get("type") or "")
                        if if_name == "Wireless" or if_type_val.startswith("ieee802.11"):
                            if not iface.get("cable"):
                                try:
                                    del_res = await client.delete(f"{settings.NETBOX_URL}/api/dcim/interfaces/{iface['id']}/", headers=headers)
                                    if del_res.status_code in (204, 200):
                                        logger.info(f"Purged orphan Wireless interface ID {iface['id']} from wired device {device_id}")
                                        existing_ifaces.remove(iface)
                                        existing_names.pop(if_name, None)
                                except Exception as e:
                                    logger.warning(f"Error purgando interfaz Wireless huérfana en device {device_id}: {e}")

                # 4. Consultar Interface Templates del Device Type y auto-crear puertos faltantes
                if dev_type_id:
                    res_tmpl = await client.get(f"{settings.NETBOX_URL}/api/dcim/interface-templates/?device_type_id={dev_type_id}&limit=100", headers=headers)
                    if res_tmpl.status_code == 200:
                        templates = res_tmpl.json().get("results", [])
                        if templates:
                            for tmpl in templates:
                                t_name = tmpl["name"]
                                if t_name not in existing_names:
                                    try:
                                        t_type = tmpl.get("type", {}).get("value") if isinstance(tmpl.get("type"), dict) else (tmpl.get("type") or "1000base-t")
                                        # Normalizar tipo para NetBox 4.x
                                        if "10gbase-x-sfp" in str(t_type) and not str(t_type).endswith("sfpp"):
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

                            # Si el tipo define plantillas específicas (ej. gpon0/1/0, 10ge) y quedó interfaz genérica 'Ethernet' sin cablear, purgarla
                            template_names = {t["name"] for t in templates}
                            if is_wired_infrastructure and "Ethernet" in existing_names and "Ethernet" not in template_names:
                                eth_iface = existing_names["Ethernet"]
                                if not eth_iface.get("cable"):
                                    try:
                                        del_eth = await client.delete(f"{settings.NETBOX_URL}/api/dcim/interfaces/{eth_iface['id']}/", headers=headers)
                                        if del_eth.status_code in (204, 200):
                                            logger.info(f"Purged orphan generic Ethernet interface ID {eth_iface['id']} from device {device_id}")
                                            existing_ifaces.remove(eth_iface)
                                            existing_names.pop("Ethernet", None)
                                    except Exception as e:
                                        logger.warning(f"Error purgando Ethernet genérica: {e}")

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

    def build_switch_ports_from_interfaces(self, interfaces_list: List[Dict[str, Any]], model_name: str = "") -> List[Dict[str, Any]]:
        """
        Convierte la lista de interfaces de NetBox en la especificación ordenada de bahías (switch_ports)
        para representar el panel frontal del switch en el lienzo de NexusDude.
        """
        import re
        bays = []
        m_low = (model_name or "").strip().lower()

        for iface in interfaces_list:
            if_name = (iface.get("name") or "").strip()
            if_type = (iface.get("type") or "1000base-t").strip()
            mgmt_only = bool(iface.get("mgmt_only", False))
            is_conn = bool(iface.get("is_connected", False))
            n_low = if_name.lower()
            t_low = if_type.lower()

            if not is_conn:
                if mgmt_only or n_low in ("mgmt", "mgmt0", "wireless", "wirelles", "poe", "ethernet", "lo", "loopback", "vlan1"):
                    continue
                if t_low.startswith("ieee802.11") or t_low in ("other", "virtual"):
                    continue

            media = "rj45"
            if "copper" in n_low:
                media = "rj45"
            elif any(k in n_low for k in ("sfp", "xfp", "qsfp", "10ge", "fiber", "pon")) or any(
                k in t_low for k in ("sfp", "xfp", "qsfp", "10gbase-x", "1000base-x", "gpon", "fiber")
            ):
                media = "sfp"
            elif "24t4x" in m_low or "24upl4x" in m_low:
                nums = re.findall(r"\d+", n_low)
                if nums and int(nums[-1]) >= 25:
                    media = "sfp"

            bays.append({
                "id": iface.get("id"),
                "name": if_name,
                "type": if_type,
                "media": media,
                "is_connected": is_conn,
                "cable_id": iface.get("cable_id"),
                "peer_device": iface.get("connected_device"),
                "peer_interface": iface.get("connected_interface"),
            })

        def sort_key(item):
            nums = [int(x) for x in re.findall(r"\d+", item["name"])]
            media_rank = 1 if item["media"] == "sfp" else 0
            if not nums and "sfp" in item["name"].lower():
                nums = [999]
            return (media_rank, nums, item["name"].lower())

        bays.sort(key=sort_key)
        for idx, b in enumerate(bays, 1):
            nums = [int(x) for x in re.findall(r"\d+", b["name"])]
            if "copper" in b["name"].lower() and nums:
                b["label"] = f"{nums[-1]}C"
            elif "sfp" in b["name"].lower():
                if nums:
                    if nums[-1] <= 4:
                        b["label"] = f"S{nums[-1]}"
                    elif any(k in b["name"].lower() for k in ("ge", "gi", "eth", "port")):
                        b["label"] = f"{nums[-1]}S"
                    else:
                        b["label"] = str(nums[-1])
                else:
                    b["label"] = "SFP"
            elif nums:
                b["label"] = str(nums[-1])
            else:
                b["label"] = b["name"][:3].upper() or str(idx)

        return bays

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

    async def get_all_cables(self, force: bool = False) -> List[Dict[str, Any]]:
        """Obtiene todos los cables registrados en NetBox con caché ligera."""
        if not force and self._cables_cache is not None and self._cables_last_fetch:
            if (datetime.now() - self._cables_last_fetch).total_seconds() < 20:
                return self._cables_cache

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
            self._cables_cache = cables
            self._cables_last_fetch = datetime.now()
        except Exception as e:
            logger.error(f"Error consultando cables en NetBox: {e}")
            if self._cables_cache is not None:
                return self._cables_cache
        return cables

    async def get_wireless_links(self, force: bool = False) -> List[Dict[str, Any]]:
        """Obtiene todos los enlaces inalámbricos registrados en NetBox con caché ligera."""
        if not force and self._wireless_links_cache is not None and self._wireless_links_last_fetch:
            if (datetime.now() - self._wireless_links_last_fetch).total_seconds() < 20:
                return self._wireless_links_cache

        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                res = await client.get(f"{settings.NETBOX_URL}/api/wireless/wireless-links/?limit=1000", headers=headers)
                if res.status_code == 200:
                    links = res.json().get("results", [])
                    self._wireless_links_cache = links
                    self._wireless_links_last_fetch = datetime.now()
                    return links
        except Exception as e:
            logger.error(f"Error consultando wireless-links en NetBox: {e}")
            if self._wireless_links_cache is not None:
                return self._wireless_links_cache
        return []

    async def get_wireless_link_by_id(self, wid: int) -> Optional[Dict[str, Any]]:
        """Obtiene un WirelessLink específico desde NetBox."""
        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                res = await client.get(f"{settings.NETBOX_URL}/api/wireless/wireless-links/{wid}/", headers=headers)
                if res.status_code == 200:
                    return res.json()
        except Exception as e:
            logger.error(f"Error consultando wireless link {wid} en NetBox: {e}")
        return None

    async def create_netbox_wireless_link(self, dev1_id: int, if1_name: str, dev2_id: int, if2_name: str, ssid: str = "", description: str = "") -> Dict[str, Any]:
        """Crea un WirelessLink en NetBox entre dos interfaces inalámbricas."""
        headers = await self.get_headers()
        if1 = await self.get_or_create_interface(dev1_id, if1_name, if_type="ieee802.11n")
        if2 = await self.get_or_create_interface(dev2_id, if2_name, if_type="ieee802.11n")

        payload = {
            "interface_a": if1["id"],
            "interface_b": if2["id"],
            "status": "connected",
            "ssid": ssid or "",
            "description": description or "Radioenlace inalámbrico creado desde NexusDude"
        }

        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            res = await client.post(f"{settings.NETBOX_URL}/api/wireless/wireless-links/", headers=headers, json=payload)
            if res.status_code in (200, 201):
                wl_data = res.json()
                logger.info(f"WirelessLink creado en NetBox: ID {wl_data.get('id')} ({if1_name} <-> {if2_name})")
                return {
                    "wireless_link_id": wl_data.get("id"),
                    "if1_id": if1["id"],
                    "if2_id": if2["id"],
                    "status": wl_data.get("status", {}).get("value") if isinstance(wl_data.get("status"), dict) else "connected"
                }
            else:
                logger.error(f"Error creando WirelessLink en NetBox: {res.status_code} - {res.text}")
                raise Exception(f"NetBox no pudo crear WirelessLink: {res.text}")

    async def delete_netbox_wireless_link(self, wireless_link_id: int) -> bool:
        """Elimina un WirelessLink en NetBox por su ID."""
        headers = await self.get_headers()
        try:
            async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                res = await client.delete(f"{settings.NETBOX_URL}/api/wireless/wireless-links/{wireless_link_id}/", headers=headers)
                return res.status_code in (204, 200)
        except Exception as e:
            logger.error(f"Error eliminando WirelessLink {wireless_link_id} en NetBox: {e}")
            return False

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
                        site_data = loc.get("site") or {}
                        if site_data.get("id") in map_by_site_id:
                            parent_map_id = map_by_site_id[site_data["id"]]["id"]

                    if parent_map_id and child_map_id and parent_map_id != child_map_id:
                        await db.execute("UPDATE maps SET parent_map_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (parent_map_id, child_map_id))
                        c_h = await db.execute("SELECT id FROM map_hierarchy WHERE child_map_id = ? AND parent_map_id = ?", (child_map_id, parent_map_id))
                        if not await c_h.fetchone():
                            h_id = f"h-{uuid.uuid4().hex[:8]}"
                            await db.execute("INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, is_primary) VALUES (?, ?, ?, 1)", (h_id, parent_map_id, child_map_id))

            # 2.1. Establecer jerarquía basada en sitios_origen / sitios_dependientes de NetBox
            headers = await self.get_headers()
            try:
                async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                    res_raw_sites = await client.get(f"{settings.NETBOX_URL}/api/dcim/sites/?limit=1000", headers=headers)
                    raw_sites = res_raw_sites.json().get("results", []) if res_raw_sites.status_code == 200 else []

                    for s in raw_sites:
                        site_id = s.get("id")
                        cf = s.get("custom_fields") or {}
                        p_list = cf.get("sitios_origen") or []
                        if site_id in map_by_site_id and p_list:
                            child_map_id = map_by_site_id[site_id]["id"]
                            is_first = True
                            for p_item in p_list:
                                p_site_id = p_item["id"] if isinstance(p_item, dict) else p_item
                                if p_site_id in map_by_site_id:
                                    parent_map_id = map_by_site_id[p_site_id]["id"]
                                    if parent_map_id and child_map_id and parent_map_id != child_map_id:
                                        if is_first:
                                            await db.execute("UPDATE maps SET parent_map_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (parent_map_id, child_map_id))
                                            is_first = False
                                        c_h = await db.execute("SELECT id FROM map_hierarchy WHERE child_map_id = ? AND parent_map_id = ?", (child_map_id, parent_map_id))
                                        if not await c_h.fetchone():
                                            h_id = f"h-{uuid.uuid4().hex[:8]}"
                                            await db.execute("INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, is_primary) VALUES (?, ?, ?, 1)", (h_id, parent_map_id, child_map_id))
            except Exception as e:
                logger.warning(f"Error procesando jerarquía de Sitios Origen: {e}")

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

    def _find_free_canvas_coords(
        self,
        existing_nodes: List[Dict[str, Any]],
        start_x: float,
        start_y: float,
        min_dist: float = 90.0
    ) -> Tuple[float, float]:
        """Calcula coordenadas libres en el lienzo evitando superposiciones con nodos existentes."""
        x = max(60.0, float(start_x))
        y = max(60.0, float(start_y))
        attempts = 0
        while attempts < 40:
            collision = False
            for n in existing_nodes:
                nx = float(n.get("x") or 0)
                ny = float(n.get("y") or 0)
                if abs(nx - x) < min_dist and abs(ny - y) < min_dist:
                    collision = True
                    break
            if not collision:
                return round(x, 1), round(y, 1)
            y += 80.0
            if y > 750.0:
                y = 80.0
                x += 160.0
            attempts += 1
        return round(x, 1), round(y, 1)

    async def sync_intermap_topology_for_connection(
        self,
        db,
        conn_type: str,
        conn_id: int,
        dev_a_id: int,
        dev_b_id: int,
        if_a_name: str,
        if_b_name: str,
        if_a_id: Optional[int] = None,
        if_b_id: Optional[int] = None,
        cable_type: str = "cat6",
        cable_status: str = "connected"
    ) -> Dict[str, Any]:
        """
        Sincroniza una conexión documentada en NetBox (Cable o WirelessLink) asegurando:
        1. Si es Intra-mapa: el enlace directo entre ambos nodos en el mismo mapa.
        2. Si es Inter-mapa (AP Mapa A <-> CPE Mapa B):
           - Identifica rol Padre (Proveedor/AP/Router/Switch) vs Hijo (Consumidor/CPE/Station).
           - En Mapa Padre: asegura portal submapa (📁 Submapa) y arista AP -> 📁 Submapa.
           - En Mapa Hijo: asegura portal padre (📁 ⬆ Padre) y arista 📁 ⬆ Padre -> CPE.
           - Registra jerarquía en map_hierarchy y maps.parent_map_id.
           - Sanea portales padre huérfanos sin enlaces para evitar superposiciones.
        """
        c_a = await db.execute("SELECT * FROM nodes WHERE device_id = ? AND device_type NOT IN ('submap', 'parent_map')", (dev_a_id,))
        nodes_a = [dict(r) for r in await c_a.fetchall()]
        c_b = await db.execute("SELECT * FROM nodes WHERE device_id = ? AND device_type NOT IN ('submap', 'parent_map')", (dev_b_id,))
        nodes_b = [dict(r) for r in await c_b.fetchall()]

        if not nodes_a or not nodes_b:
            return {"status": "skipped", "reason": f"No se encontraron nodos para dispositivos {dev_a_id} o {dev_b_id}"}

        results = []
        pin_id = f"pin-nb-{conn_type}-{conn_id}"

        for n_a in nodes_a:
            for n_b in nodes_b:
                map_a = n_a["map_id"]
                map_b = n_b["map_id"]

                # ─────────────────────────────────────────────────────────────
                # 1. CASO INTRA-MAPA (Mismo mapa / lienzo)
                # ─────────────────────────────────────────────────────────────
                if map_a == map_b:
                    c_exist = await db.execute("""
                        SELECT id FROM links
                        WHERE map_id = ? AND (
                            (source_node_id = ? AND target_node_id = ?)
                            OR (source_node_id = ? AND target_node_id = ?)
                            OR (? = 'wireless' AND netbox_wirelesslink_id = ?)
                            OR (? = 'cable' AND netbox_cable_id = ?)
                        )
                    """, (map_a, n_a["id"], n_b["id"], n_b["id"], n_a["id"], conn_type, conn_id, conn_type, conn_id))
                    ex_row = await c_exist.fetchone()

                    if ex_row:
                        if conn_type == "wireless":
                            await db.execute("""
                                UPDATE links
                                SET source_node_id = ?, target_node_id = ?,
                                    source_interface = ?, target_interface = ?,
                                    source_interface_id = ?, target_interface_id = ?,
                                    netbox_wirelesslink_id = ?, cable_type = 'wireless', cable_status = 'connected',
                                    status = 'ok', updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (n_a["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id, conn_id, ex_row["id"]))
                        else:
                            await db.execute("""
                                UPDATE links
                                SET source_node_id = ?, target_node_id = ?,
                                    source_interface = ?, target_interface = ?,
                                    source_interface_id = ?, target_interface_id = ?,
                                    netbox_cable_id = ?, cable_type = ?, cable_status = ?,
                                    status = 'ok', updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (n_a["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id, conn_id, cable_type, cable_status, ex_row["id"]))
                    else:
                        new_link_id = f"link-{uuid.uuid4().hex[:8]}"
                        if conn_type == "wireless":
                            await db.execute("""
                                INSERT INTO links (
                                    id, map_id, source_node_id, target_node_id,
                                    source_interface, target_interface,
                                    source_interface_id, target_interface_id,
                                    netbox_wirelesslink_id, cable_type, cable_status, status
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'wireless', 'connected', 'ok')
                            """, (new_link_id, map_a, n_a["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id, conn_id))
                        else:
                            await db.execute("""
                                INSERT INTO links (
                                    id, map_id, source_node_id, target_node_id,
                                    source_interface, target_interface,
                                    source_interface_id, target_interface_id,
                                    netbox_cable_id, cable_type, cable_status, status
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok')
                            """, (new_link_id, map_a, n_a["id"], n_b["id"], if_a_name, if_b_name, if_a_id, if_b_id, conn_id, cable_type, cable_status))

                    results.append({"type": "intra_map", "map_id": map_a})
                    continue

                # ─────────────────────────────────────────────────────────────
                # 2. CASO INTER-MAPA (Diferentes mapas / sitios)
                # ─────────────────────────────────────────────────────────────
                def _calc_provider_score(n: Dict[str, Any]) -> int:
                    score = 0
                    name = (n.get("name") or "").upper()
                    dtype = (n.get("device_type") or "").upper()
                    extra = {}
                    if n.get("extra_data"):
                        try:
                            extra = json.loads(n["extra_data"]) if isinstance(n["extra_data"], str) else n["extra_data"]
                        except Exception:
                            pass
                    role = (extra.get("role") or "").upper()

                    if name.startswith("AP_") or name.startswith("SECTOR_"):
                        score += 15
                    elif " AP" in name or "_AP" in name or "CORE" in name or "SW_" in name:
                        score += 8

                    if any(k in role for k in ("ACCESS POINT", "PTP AP", "SECTOR", "CORE", "SWITCH")):
                        score += 10
                    if any(k in dtype for k in ("ACCESS POINT", "SECTOR", "SWITCH", "RB CORE")):
                        score += 8

                    if name.startswith("CPE_") or "CPE" in name or "STATION" in name:
                        score -= 15
                    if any(k in role for k in ("CPE", "STATION", "CLIENT")):
                        score -= 10
                    if any(k in dtype for k in ("CPE", "STATION")):
                        score -= 8
                    return score

                score_a = _calc_provider_score(n_a)
                score_b = _calc_provider_score(n_b)

                if score_a > score_b:
                    parent_node, parent_map = n_a, map_a
                    child_node, child_map = n_b, map_b
                    parent_if, child_if = if_a_name, if_b_name
                    parent_if_id, child_if_id = if_a_id, if_b_id
                elif score_b > score_a:
                    parent_node, parent_map = n_b, map_b
                    child_node, child_map = n_a, map_a
                    parent_if, child_if = if_b_name, if_a_name
                    parent_if_id, child_if_id = if_b_id, if_a_id
                else:
                    # Fallback a jerarquía preexistente
                    c_h = await db.execute("SELECT parent_map_id FROM map_hierarchy WHERE child_map_id = ? AND parent_map_id = ?", (map_b, map_a))
                    if await c_h.fetchone():
                        parent_node, parent_map = n_a, map_a
                        child_node, child_map = n_b, map_b
                        parent_if, child_if = if_a_name, if_b_name
                        parent_if_id, child_if_id = if_a_id, if_b_id
                    else:
                        parent_node, parent_map = n_a, map_a
                        child_node, child_map = n_b, map_b
                        parent_if, child_if = if_a_name, if_b_name
                        parent_if_id, child_if_id = if_a_id, if_b_id

                # Obtener nombres de mapas
                c_pm = await db.execute("SELECT id, name, site_name FROM maps WHERE id = ?", (parent_map,))
                pm_row = await c_pm.fetchone()
                parent_map_name = pm_row["name"] if pm_row else "Parent Map"

                c_cm = await db.execute("SELECT id, name, site_name FROM maps WHERE id = ?", (child_map,))
                cm_row = await c_cm.fetchone()
                child_map_name = cm_row["name"] if cm_row else "Child Map"

                # Nodos existentes en ambos mapas para evitar colisiones
                c_pnodes = await db.execute("SELECT id, x, y, name, device_type, extra_data FROM nodes WHERE map_id = ?", (parent_map,))
                parent_canvas_nodes = [dict(r) for r in await c_pnodes.fetchall()]

                c_cnodes = await db.execute("SELECT id, x, y, name, device_type, extra_data FROM nodes WHERE map_id = ?", (child_map,))
                child_canvas_nodes = [dict(r) for r in await c_cnodes.fetchall()]

                # 2.A: PORTAL SUBMAPA EN MAPA PADRE (📁 [Child Map Name])
                submap_portal = None
                for pn in parent_canvas_nodes:
                    if pn.get("device_type") == "submap":
                        ped = {}
                        if pn.get("extra_data"):
                            try:
                                ped = json.loads(pn["extra_data"]) if isinstance(pn["extra_data"], str) else pn["extra_data"]
                            except Exception:
                                pass
                        if ped.get("target_map_id") == child_map or pn.get("name") == f"📁 {child_map_name}":
                            submap_portal = pn
                            break

                if not submap_portal:
                    start_x = (parent_node.get("x") or 400.0) + 160.0
                    start_y = (parent_node.get("y") or 200.0) + 20.0
                    portal_x, portal_y = self._find_free_canvas_coords(parent_canvas_nodes, start_x, start_y)
                    portal_id = f"node-{uuid.uuid4().hex[:8]}"
                    portal_extra = {
                        "target_map_id": child_map,
                        "role": "Submapa",
                        "site_name": child_map_name,
                        "auto_generated": True
                    }
                    await db.execute("""
                        INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                        VALUES (?, ?, ?, '', 'submap', ?, ?, ?, 'ok', ?)
                    """, (portal_id, parent_map, f"📁 {child_map_name}", child_map_name, portal_x, portal_y, json.dumps(portal_extra)))
                    submap_portal = {"id": portal_id, "map_id": parent_map, "name": f"📁 {child_map_name}", "x": portal_x, "y": portal_y}
                    parent_canvas_nodes.append(submap_portal)

                # 2.B: ARISTA EN MAPA PADRE (Equipo Padre ➔ Portal Submapa)
                extra_link_parent = {
                    "is_intermap": True,
                    "pin_id": pin_id,
                    "local_node_id": parent_node["id"],
                    "local_node_name": parent_node["name"],
                    "remote_node_id": child_node["id"],
                    "remote_node_name": child_node["name"],
                    "remote_map_id": child_map,
                    "target_submap_device_id": child_node.get("device_id"),
                    "target_submap_device_name": child_node["name"],
                    "direction": "source_to_target",
                    "sync_zabbix": True
                }
                if conn_type == "wireless":
                    extra_link_parent["wireless_link_id"] = conn_id
                    extra_link_parent["netbox_wirelesslink_id"] = conn_id
                else:
                    extra_link_parent["netbox_cable_id"] = conn_id

                c_lp = await db.execute("""
                    SELECT id FROM links
                    WHERE map_id = ? AND (
                        (source_node_id = ? AND target_node_id = ?)
                        OR (source_node_id = ? AND target_node_id = ?)
                        OR (? = 'wireless' AND netbox_wirelesslink_id = ?)
                        OR (? = 'cable' AND netbox_cable_id = ?)
                    )
                """, (parent_map, parent_node["id"], submap_portal["id"], submap_portal["id"], parent_node["id"], conn_type, conn_id, conn_type, conn_id))
                ex_lp = await c_lp.fetchone()

                if ex_lp:
                    if conn_type == "wireless":
                        await db.execute("""
                            UPDATE links
                            SET source_node_id = ?, target_node_id = ?,
                                source_interface = ?, target_interface = ?,
                                source_interface_id = ?, target_interface_id = ?,
                                netbox_wirelesslink_id = ?, cable_type = 'wireless', cable_status = 'connected',
                                status = 'ok', extra_data = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (parent_node["id"], submap_portal["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, json.dumps(extra_link_parent), ex_lp["id"]))
                    else:
                        await db.execute("""
                            UPDATE links
                            SET source_node_id = ?, target_node_id = ?,
                                source_interface = ?, target_interface = ?,
                                source_interface_id = ?, target_interface_id = ?,
                                netbox_cable_id = ?, cable_type = ?, cable_status = ?,
                                status = 'ok', extra_data = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (parent_node["id"], submap_portal["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, cable_type, cable_status, json.dumps(extra_link_parent), ex_lp["id"]))
                else:
                    new_link_id = f"link-{uuid.uuid4().hex[:8]}"
                    if conn_type == "wireless":
                        await db.execute("""
                            INSERT INTO links (
                                id, map_id, source_node_id, target_node_id,
                                source_interface, target_interface,
                                source_interface_id, target_interface_id,
                                netbox_wirelesslink_id, cable_type, cable_status, status, extra_data
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'wireless', 'connected', 'ok', ?)
                        """, (new_link_id, parent_map, parent_node["id"], submap_portal["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, json.dumps(extra_link_parent)))
                    else:
                        await db.execute("""
                            INSERT INTO links (
                                id, map_id, source_node_id, target_node_id,
                                source_interface, target_interface,
                                source_interface_id, target_interface_id,
                                netbox_cable_id, cable_type, cable_status, status, extra_data
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                        """, (new_link_id, parent_map, parent_node["id"], submap_portal["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, cable_type, cable_status, json.dumps(extra_link_parent)))

                # 2.C: PORTAL PADRE EN MAPA HIJO (📁 ⬆ [Parent Map Name])
                parent_portal = None
                for cn in child_canvas_nodes:
                    if cn.get("device_type") == "parent_map" or "is_parent_shortcut" in str(cn.get("extra_data") or ""):
                        ced = {}
                        if cn.get("extra_data"):
                            try:
                                ced = json.loads(cn["extra_data"]) if isinstance(cn["extra_data"], str) else cn["extra_data"]
                            except Exception:
                                pass
                        if ced.get("target_map_id") == parent_map or cn.get("name") == f"📁 ⬆ {parent_map_name}":
                            parent_portal = cn
                            break

                if not parent_portal:
                    start_x = max(80.0, (child_node.get("x") or 300.0) - 160.0)
                    start_y = max(80.0, (child_node.get("y") or 260.0) - 40.0)
                    portal_x, portal_y = self._find_free_canvas_coords(child_canvas_nodes, start_x, start_y)
                    portal_id = f"node-{uuid.uuid4().hex[:8]}"
                    portal_extra = {
                        "target_map_id": parent_map,
                        "is_parent_shortcut": True,
                        "parent_map_name": parent_map_name,
                        "role": "Mapa Superior",
                        "auto_generated": True
                    }
                    await db.execute("""
                        INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                        VALUES (?, ?, ?, '', 'parent_map', ?, ?, ?, 'ok', ?)
                    """, (portal_id, child_map, f"📁 ⬆ {parent_map_name}", parent_map_name, portal_x, portal_y, json.dumps(portal_extra)))
                    parent_portal = {"id": portal_id, "map_id": child_map, "name": f"📁 ⬆ {parent_map_name}", "x": portal_x, "y": portal_y}
                    child_canvas_nodes.append(parent_portal)

                # 2.D: ARISTA EN MAPA HIJO (Portal Padre ➔ Equipo Hijo)
                extra_link_child = {
                    "is_intermap": True,
                    "pin_id": pin_id,
                    "local_node_id": child_node["id"],
                    "local_node_name": child_node["name"],
                    "remote_node_id": parent_node["id"],
                    "remote_node_name": parent_node["name"],
                    "remote_map_id": parent_map,
                    "target_submap_device_id": parent_node.get("device_id"),
                    "target_submap_device_name": parent_node["name"],
                    "direction": "source_to_target",
                    "sync_zabbix": True
                }
                if conn_type == "wireless":
                    extra_link_child["wireless_link_id"] = conn_id
                    extra_link_child["netbox_wirelesslink_id"] = conn_id
                else:
                    extra_link_child["netbox_cable_id"] = conn_id

                c_lc = await db.execute("""
                    SELECT id FROM links
                    WHERE map_id = ? AND (
                        (source_node_id = ? AND target_node_id = ?)
                        OR (source_node_id = ? AND target_node_id = ?)
                        OR (? = 'wireless' AND netbox_wirelesslink_id = ?)
                        OR (? = 'cable' AND netbox_cable_id = ?)
                    )
                """, (child_map, parent_portal["id"], child_node["id"], child_node["id"], parent_portal["id"], conn_type, conn_id, conn_type, conn_id))
                ex_lc = await c_lc.fetchone()

                if ex_lc:
                    if conn_type == "wireless":
                        await db.execute("""
                            UPDATE links
                            SET source_node_id = ?, target_node_id = ?,
                                source_interface = ?, target_interface = ?,
                                source_interface_id = ?, target_interface_id = ?,
                                netbox_wirelesslink_id = ?, cable_type = 'wireless', cable_status = 'connected',
                                status = 'ok', extra_data = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (parent_portal["id"], child_node["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, json.dumps(extra_link_child), ex_lc["id"]))
                    else:
                        await db.execute("""
                            UPDATE links
                            SET source_node_id = ?, target_node_id = ?,
                                source_interface = ?, target_interface = ?,
                                source_interface_id = ?, target_interface_id = ?,
                                netbox_cable_id = ?, cable_type = ?, cable_status = ?,
                                status = 'ok', extra_data = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (parent_portal["id"], child_node["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, cable_type, cable_status, json.dumps(extra_link_child), ex_lc["id"]))
                else:
                    new_link_id = f"link-{uuid.uuid4().hex[:8]}"
                    if conn_type == "wireless":
                        await db.execute("""
                            INSERT INTO links (
                                id, map_id, source_node_id, target_node_id,
                                source_interface, target_interface,
                                source_interface_id, target_interface_id,
                                netbox_wirelesslink_id, cable_type, cable_status, status, extra_data
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'wireless', 'connected', 'ok', ?)
                        """, (new_link_id, child_map, parent_portal["id"], child_node["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, json.dumps(extra_link_child)))
                    else:
                        await db.execute("""
                            INSERT INTO links (
                                id, map_id, source_node_id, target_node_id,
                                source_interface, target_interface,
                                source_interface_id, target_interface_id,
                                netbox_cable_id, cable_type, cable_status, status, extra_data
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
                        """, (new_link_id, child_map, parent_portal["id"], child_node["id"], parent_if, child_if, parent_if_id, child_if_id, conn_id, cable_type, cable_status, json.dumps(extra_link_child)))

                # 2.E: JERARQUÍA Y SANEO DE NODOS HUÉRFANOS
                if child_map != parent_map:
                    curr_p = parent_map
                    visited_p = set([child_map])
                    is_cycle = False
                    while curr_p:
                        if curr_p in visited_p:
                            is_cycle = True
                            break
                        visited_p.add(curr_p)
                        c_pcheck = await db.execute("SELECT parent_map_id FROM maps WHERE id = ?", (curr_p,))
                        r_pcheck = await c_pcheck.fetchone()
                        curr_p = r_pcheck["parent_map_id"] if r_pcheck else None

                    if not is_cycle:
                        await db.execute("UPDATE maps SET parent_map_id = ? WHERE id = ?", (parent_map, child_map))
                        c_he = await db.execute("SELECT id FROM map_hierarchy WHERE parent_map_id = ? AND child_map_id = ?", (parent_map, child_map))
                        he_row = await c_he.fetchone()
                        if he_row:
                            await db.execute("UPDATE map_hierarchy SET is_primary = 1 WHERE id = ?", (he_row["id"],))
                        else:
                            await db.execute("""
                                INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, position, is_primary)
                                VALUES (?, ?, ?, 0, 1)
                            """, (f"hier-{uuid.uuid4().hex[:8]}", parent_map, child_map))

                        await db.execute("UPDATE map_hierarchy SET is_primary = 0 WHERE child_map_id = ? AND parent_map_id != ?", (child_map, parent_map))
                    else:
                        logger.warning(f"Omitiendo asignación de parent_map_id en '{child_map_name}' hacia '{parent_map_name}' para evitar ciclo jerárquico.")

                c_ghosts = await db.execute("""
                    SELECT id, name FROM nodes
                    WHERE map_id = ? AND device_type = 'parent_map' AND id != ?
                """, (child_map, parent_portal["id"]))
                for g in await c_ghosts.fetchall():
                    c_chk = await db.execute("SELECT id FROM links WHERE source_node_id = ? OR target_node_id = ?", (g["id"], g["id"]))
                    if not await c_chk.fetchone():
                        await db.execute("DELETE FROM nodes WHERE id = ?", (g["id"],))
                        logger.info(f"Portal padre huérfano '{g['name']}' eliminado de {child_map_name} para evitar superposición.")

                results.append({"type": "inter_map", "parent_map": parent_map, "child_map": child_map})

        return {"status": "success", "results": results}

    async def sync_all_links_from_netbox(self, map_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Sincroniza masivamente cables y wireless-links de NetBox hacia aristas de NexusDude:
        - Conexiones Intra-mapa: actualiza/crea aristas directas entre nodos del mismo mapa.
        - Conexiones Inter-mapa (ej. AP en Mapa Padre ➔ CPE en Submapa):
          Asegura portales bidireccionales (📁 Submapa y 📁 ⬆ Padre) y aristas recíprocas
          vinculadas por pin_id y netbox_wirelesslink_id / netbox_cable_id.
        """
        cables = await self.get_all_cables(force=True)
        wireless_links = await self.get_wireless_links(force=True)

        intra_links_synced = 0
        inter_links_synced = 0

        async with get_db_connection() as db:
            target_dev_ids = set()
            if map_id:
                c_devs = await db.execute("SELECT device_id FROM nodes WHERE map_id = ? AND device_id IS NOT NULL", (map_id,))
                target_dev_ids = {r["device_id"] for r in await c_devs.fetchall()}

            for wl in wireless_links:
                wid = wl.get("id")
                ifa = wl.get("interface_a") or {}
                ifb = wl.get("interface_b") or {}
                da = ifa.get("device", {}).get("id")
                db_id = ifb.get("device", {}).get("id")
                if not da or not db_id:
                    continue
                if map_id and da not in target_dev_ids and db_id not in target_dev_ids:
                    continue

                res = await self.sync_intermap_topology_for_connection(
                    db=db,
                    conn_type="wireless",
                    conn_id=wid,
                    dev_a_id=da,
                    dev_b_id=db_id,
                    if_a_name=ifa.get("name") or "wlan",
                    if_b_name=ifb.get("name") or "wlan",
                    if_a_id=ifa.get("id"),
                    if_b_id=ifb.get("id"),
                    cable_type="wireless",
                    cable_status="connected"
                )
                for item in res.get("results", []):
                    if item.get("type") == "intra_map":
                        intra_links_synced += 1
                    elif item.get("type") == "inter_map":
                        inter_links_synced += 1

            for cb in cables:
                cid = cb.get("id")
                at = cb.get("a_terminations", [])
                bt = cb.get("b_terminations", [])
                if not at or not bt:
                    continue
                ob_a = at[0].get("object", {})
                ob_b = bt[0].get("object", {})
                da = ob_a.get("device", {}).get("id")
                db_id = ob_b.get("device", {}).get("id")
                if not da or not db_id:
                    continue
                if map_id and da not in target_dev_ids and db_id not in target_dev_ids:
                    continue

                c_type = cb.get("type", {}).get("value") if isinstance(cb.get("type"), dict) else (cb.get("type") or "cat6")
                c_status = cb.get("status", {}).get("value") if isinstance(cb.get("status"), dict) else (cb.get("status") or "connected")

                res = await self.sync_intermap_topology_for_connection(
                    db=db,
                    conn_type="cable",
                    conn_id=cid,
                    dev_a_id=da,
                    dev_b_id=db_id,
                    if_a_name=ob_a.get("name") or "eth",
                    if_b_name=ob_b.get("name") or "eth",
                    if_a_id=at[0].get("object_id"),
                    if_b_id=bt[0].get("object_id"),
                    cable_type=c_type,
                    cable_status=c_status
                )
                for item in res.get("results", []):
                    if item.get("type") == "intra_map":
                        intra_links_synced += 1
                    elif item.get("type") == "inter_map":
                        inter_links_synced += 1

            await db.commit()

        return {
            "status": "success",
            "intra_links_synced": intra_links_synced,
            "inter_links_synced": inter_links_synced,
            "message": f"Conexiones NetBox sincronizadas: {intra_links_synced} intra-mapa, {inter_links_synced} inter-mapa."
        }

    async def sync_single_wireless_link(self, wid: int, wlink_data: Optional[Dict[str, Any]] = None):
        """Sincroniza un WirelessLink de NetBox en tiempo real reactivamente vía Webhook."""
        self._wireless_links_cache = None
        if not wlink_data or not wlink_data.get("interface_a") or not wlink_data.get("interface_b"):
            wlink_data = await self.get_wireless_link_by_id(wid)
        if not wlink_data:
            logger.warning(f"No se pudo obtener datos del WirelessLink {wid}")
            return

        ifa = wlink_data.get("interface_a") or {}
        ifb = wlink_data.get("interface_b") or {}
        da = ifa.get("device", {}).get("id")
        db_id = ifb.get("device", {}).get("id")

        if not da or not db_id:
            logger.warning(f"WirelessLink {wid} no tiene dispositivos válidos en ambos extremos ({da}, {db_id})")
            return

        async with get_db_connection() as db:
            await self.sync_intermap_topology_for_connection(
                db=db,
                conn_type="wireless",
                conn_id=wid,
                dev_a_id=da,
                dev_b_id=db_id,
                if_a_name=ifa.get("name") or "wlan",
                if_b_name=ifb.get("name") or "wlan",
                if_a_id=ifa.get("id"),
                if_b_id=ifb.get("id"),
                cable_type="wireless",
                cable_status="connected"
            )
            await db.commit()

    async def sync_single_cable(self, cid: int, cable_data: Optional[Dict[str, Any]] = None):
        """Sincroniza un Cable físico de NetBox en tiempo real reactivamente vía Webhook."""
        self._cables_cache = None
        if not cable_data or not cable_data.get("a_terminations") or not cable_data.get("b_terminations"):
            cable_data = await self.get_netbox_cable(cid)
        if not cable_data:
            logger.warning(f"No se pudo obtener datos del Cable {cid}")
            return

        a_terms = cable_data.get("a_terminations", [])
        b_terms = cable_data.get("b_terminations", [])
        if not a_terms or not b_terms:
            return

        ob_a = a_terms[0].get("object", {})
        ob_b = b_terms[0].get("object", {})
        da = ob_a.get("device", {}).get("id")
        db_id = ob_b.get("device", {}).get("id")

        if not da or not db_id:
            return

        c_type = cable_data.get("type", {}).get("value") if isinstance(cable_data.get("type"), dict) else (cable_data.get("type") or "cat6")
        c_status = cable_data.get("status", {}).get("value") if isinstance(cable_data.get("status"), dict) else (cable_data.get("status") or "connected")

        async with get_db_connection() as db:
            await self.sync_intermap_topology_for_connection(
                db=db,
                conn_type="cable",
                conn_id=cid,
                dev_a_id=da,
                dev_b_id=db_id,
                if_a_name=ob_a.get("name") or "eth",
                if_b_name=ob_b.get("name") or "eth",
                if_a_id=a_terms[0].get("object_id"),
                if_b_id=bt[0].get("object_id"),
                cable_type=c_type,
                cable_status=c_status
            )
            await db.commit()

    async def delete_link_by_netbox_id(self, cable_id: Optional[int] = None, wirelesslink_id: Optional[int] = None):
        """Elimina enlaces de NexusDude cuando se borra el Cable o WirelessLink en NetBox."""
        self._wireless_links_cache = None
        self._cables_cache = None
        async with get_db_connection() as db:
            if wirelesslink_id:
                await db.execute("""
                    DELETE FROM links 
                    WHERE netbox_wirelesslink_id = ? 
                       OR (extra_data IS NOT NULL AND (
                           json_extract(extra_data, '$.netbox_wirelesslink_id') = ?
                           OR json_extract(extra_data, '$.wireless_link_id') = ?
                       ))
                """, (wirelesslink_id, wirelesslink_id, wirelesslink_id))
                logger.info(f"Enlaces de WirelessLink ID {wirelesslink_id} eliminados de NexusDude.")
            if cable_id:
                await db.execute("""
                    DELETE FROM links 
                    WHERE netbox_cable_id = ? 
                       OR (extra_data IS NOT NULL AND json_extract(extra_data, '$.netbox_cable_id') = ?)
                """, (cable_id, cable_id))
                logger.info(f"Enlaces de Cable ID {cable_id} eliminados de NexusDude.")
            await db.commit()

    async def sync_map_connections_from_netbox(self, db, map_id: str):
        """
        Sincroniza en tiempo real las conexiones de NetBox (WirelessLinks y Cables)
        que involucran a equipos del mapa actual (map_id), garantizando portales y aristas bidireccionales.
        """
        c_nodes = await db.execute("SELECT device_id FROM nodes WHERE map_id = ? AND device_id IS NOT NULL", (map_id,))
        dev_rows = await c_nodes.fetchall()
        if not dev_rows:
            return

        dev_ids = {r["device_id"] for r in dev_rows if r["device_id"]}
        if not dev_ids:
            return

        wlinks = await self.get_wireless_links()
        for wl in wlinks:
            wid = wl.get("id")
            ifa = wl.get("interface_a") or {}
            ifb = wl.get("interface_b") or {}
            da = ifa.get("device", {}).get("id")
            db_id = ifb.get("device", {}).get("id")
            if da in dev_ids or db_id in dev_ids:
                await self.sync_intermap_topology_for_connection(
                    db=db,
                    conn_type="wireless",
                    conn_id=wid,
                    dev_a_id=da,
                    dev_b_id=db_id,
                    if_a_name=ifa.get("name") or "wlan",
                    if_b_name=ifb.get("name") or "wlan",
                    if_a_id=ifa.get("id"),
                    if_b_id=ifb.get("id"),
                    cable_type="wireless",
                    cable_status="connected"
                )

        cables = await self.get_all_cables()
        for cb in cables:
            cid = cb.get("id")
            at = cb.get("a_terminations", [])
            bt = cb.get("b_terminations", [])
            if at and bt:
                ob_a = at[0].get("object", {})
                ob_b = bt[0].get("object", {})
                da = ob_a.get("device", {}).get("id")
                db_id = ob_b.get("device", {}).get("id")
                if da in dev_ids or db_id in dev_ids:
                    c_type = cb.get("type", {}).get("value") if isinstance(cb.get("type"), dict) else (cb.get("type") or "cat6")
                    c_status = cb.get("status", {}).get("value") if isinstance(cb.get("status"), dict) else (cb.get("status") or "connected")
                    await self.sync_intermap_topology_for_connection(
                        db=db,
                        conn_type="cable",
                        conn_id=cid,
                        dev_a_id=da,
                        dev_b_id=db_id,
                        if_a_name=ob_a.get("name") or "eth",
                        if_b_name=ob_b.get("name") or "eth",
                        if_a_id=at[0].get("object_id"),
                        if_b_id=bt[0].get("object_id"),
                        cable_type=c_type,
                        cable_status=c_status
                    )

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

    async def sync_site_group_parent(
        self,
        child_map_name: str,
        parent_map_name: Optional[str] = None,
        netbox_site_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Sincroniza en NetBox el cambio de jerarquía de un mapa hacia los Grupos de Sitios (SiteGroup) y Sitios (Site).
        """
        headers = await self.get_headers()
        clean_child = child_map_name.strip().upper()
        clean_parent = parent_map_name.strip().upper() if parent_map_name and parent_map_name not in ("TOPOLOGÍA PRINCIPAL", "DEFAULT-MAP", "ROOT") else None

        result = {
            "child_map": child_map_name,
            "parent_map": parent_map_name,
            "netbox_updated": False,
            "site_group_id": None,
            "parent_site_group_id": None,
            "error": None
        }

        try:
            async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
                res_sg = await client.get(f"{settings.NETBOX_URL}/api/dcim/site-groups/?limit=1000", headers=headers)
                if res_sg.status_code != 200:
                    result["error"] = f"NetBox API error HTTP {res_sg.status_code}"
                    return result

                sg_list = res_sg.json().get("results", [])
                sg_by_name = {sg["name"].strip().upper(): sg for sg in sg_list}

                child_sg = sg_by_name.get(clean_child)
                parent_sg = sg_by_name.get(clean_parent) if clean_parent else None
                target_parent_id = parent_sg["id"] if parent_sg else None

                # 1. Actualizar SiteGroup en NetBox
                if child_sg:
                    child_sg_id = child_sg["id"]
                    result["site_group_id"] = child_sg_id
                    result["parent_site_group_id"] = target_parent_id

                    current_p = child_sg.get("parent")
                    current_p_id = current_p["id"] if isinstance(current_p, dict) else current_p

                    if current_p_id != target_parent_id:
                        patch_res = await client.patch(
                            f"{settings.NETBOX_URL}/api/dcim/site-groups/{child_sg_id}/",
                            headers=headers,
                            json={"parent": target_parent_id}
                        )
                        if patch_res.status_code in (200, 201):
                            result["netbox_updated"] = True
                            logger.info(f"NetBox SiteGroup '{clean_child}' (id={child_sg_id}) actualizado con parent={target_parent_id}")
                        else:
                            result["error"] = f"Error actualizando SiteGroup en NetBox: {patch_res.text}"
                    else:
                        result["netbox_updated"] = True

                # 2. Actualizar Sitio NetBox si está vinculado
                if netbox_site_id or not child_sg:
                    target_site_id = netbox_site_id
                    if not target_site_id:
                        res_sites = await client.get(f"{settings.NETBOX_URL}/api/dcim/sites/?name={child_map_name}", headers=headers)
                        if res_sites.status_code == 200 and res_sites.json().get("results"):
                            target_site_id = res_sites.json()["results"][0]["id"]

                    if target_site_id:
                        patch_site = await client.patch(
                            f"{settings.NETBOX_URL}/api/dcim/sites/{target_site_id}/",
                            headers=headers,
                            json={"group": target_parent_id}
                        )
                        if patch_site.status_code in (200, 201):
                            result["netbox_updated"] = True
                            logger.info(f"NetBox Site '{clean_child}' (id={target_site_id}) actualizado con group={target_parent_id}")

        except Exception as e:
            logger.error(f"Error en sync_site_group_parent: {e}")
            result["error"] = str(e)

        return result

    async def sync_hierarchy_bidirectional(self) -> Dict[str, Any]:
        """
        Sincronización bidireccional completa entre Grupos de Sitios de NetBox y la Jerarquía de Mapas de NexusDude.
        """
        headers = await self.get_headers()
        stats = {
            "success": True,
            "netbox_site_groups_total": 0,
            "nexusdude_maps_total": 0,
            "site_groups_updated_in_netbox": 0,
            "maps_updated_in_nexusdude": 0,
            "details": []
        }

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                # 1. Obtener todos los Grupos de Sitios de NetBox
                res_sg = await client.get(f"{settings.NETBOX_URL}/api/dcim/site-groups/?limit=1000", headers=headers)
                if res_sg.status_code != 200:
                    return {"success": False, "error": f"Error NetBox: {res_sg.status_code}"}

                sg_list = res_sg.json().get("results", [])
                stats["netbox_site_groups_total"] = len(sg_list)
                sg_by_id = {sg["id"]: sg for sg in sg_list}
                sg_by_name = {sg["name"].strip().upper(): sg for sg in sg_list}

                # 2. Obtener todos los mapas de NexusDude
                async with get_db_connection() as db:
                    c_maps = await db.execute("SELECT id, name, parent_map_id, netbox_site_id FROM maps")
                    all_maps = [dict(r) for r in await c_maps.fetchall()]
                    stats["nexusdude_maps_total"] = len(all_maps)

                    map_by_id = {m["id"]: m for m in all_maps}
                    map_by_name = {m["name"].strip().upper(): m for m in all_maps}

                    # A. EXPORTAR: De NexusDude hacia NetBox SiteGroups
                    for m in all_maps:
                        if m["id"] == "default-map":
                            continue
                        clean_m = m["name"].strip().upper()
                        child_sg = sg_by_name.get(clean_m)
                        if not child_sg:
                            continue

                        pid = m.get("parent_map_id")
                        parent_m = map_by_id.get(pid) if pid else None
                        parent_name = parent_m["name"].strip().upper() if parent_m and parent_m["id"] != "default-map" else None
                        parent_sg = sg_by_name.get(parent_name) if parent_name else None
                        target_parent_sg_id = parent_sg["id"] if parent_sg else None

                        curr_p = child_sg.get("parent")
                        curr_p_id = curr_p["id"] if isinstance(curr_p, dict) else curr_p

                        if curr_p_id != target_parent_sg_id:
                            patch_res = await client.patch(
                                f"{settings.NETBOX_URL}/api/dcim/site-groups/{child_sg['id']}/",
                                headers=headers,
                                json={"parent": target_parent_sg_id}
                            )
                            if patch_res.status_code in (200, 201):
                                stats["site_groups_updated_in_netbox"] += 1
                                stats["details"].append(f"NetBox: '{m['name']}' parent ➔ '{parent_name or 'Raíz'}'")

                    # B. IMPORTAR: De NetBox SiteGroups hacia NexusDude
                    for sg in sg_list:
                        sg_name = sg["name"].strip().upper()
                        nd_map = map_by_name.get(sg_name)
                        if not nd_map or nd_map["id"] == "default-map":
                            continue

                        p_sg = sg.get("parent")
                        p_sg_obj = sg_by_id.get(p_sg["id"] if isinstance(p_sg, dict) else p_sg) if p_sg else None
                        target_nd_parent = map_by_name.get(p_sg_obj["name"].strip().upper()) if p_sg_obj else None
                        target_parent_map_id = target_nd_parent["id"] if target_nd_parent else None

                        curr_nd_pid = nd_map.get("parent_map_id")
                        if curr_nd_pid in ("default-map", ""):
                            curr_nd_pid = None

                        if curr_nd_pid != target_parent_map_id:
                            final_pid = target_parent_map_id if target_parent_map_id else "default-map"
                            await db.execute(
                                "UPDATE maps SET parent_map_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (final_pid, nd_map["id"])
                            )
                            await db.execute("""
                                UPDATE map_hierarchy SET parent_map_id = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE child_map_id = ? AND is_primary = 1
                            """, (final_pid, nd_map["id"]))
                            stats["maps_updated_in_nexusdude"] += 1
                            stats["details"].append(f"NexusDude: '{nd_map['name']}' parent_map_id ➔ '{final_pid}'")

                    await db.commit()

        except Exception as e:
            logger.error(f"Error en sync_hierarchy_bidirectional: {e}")
            stats["success"] = False
            stats["error"] = str(e)

        return stats

inventory_service = InventoryService()




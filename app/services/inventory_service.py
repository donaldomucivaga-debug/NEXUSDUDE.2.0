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
            async with httpx.AsyncClient(timeout=30.0) as client:
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

                # 2. Cargar Roles
                try:
                    res_roles = await client.get(f"{settings.NETBOX_URL}/api/dcim/device-roles/?limit=100", headers=headers)
                    if res_roles.status_code == 200:
                        self._roles_cache = sorted(
                            [{"id": r["id"], "name": r["name"], "slug": r["slug"]} for r in res_roles.json().get("results", [])],
                            key=lambda x: x["name"]
                        )
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
                        type_obj = d.get("device_type") or {}
                        mfr_obj = type_obj.get("manufacturer") or {}

                        all_devices.append({
                            "id": d["id"],
                            "name": d.get("name") or f"Device-{d['id']}",
                            "ip": clean_ip,
                            "raw_ip": raw_ip,
                            "site": site_obj.get("name", "Desconocido"),
                            "site_id": site_obj.get("id"),
                            "role": role_obj.get("name", "Desconocido"),
                            "role_slug": role_obj.get("slug", ""),
                            "manufacturer": mfr_obj.get("name", "Genérico"),
                            "model": type_obj.get("model", ""),
                            "status": d.get("status", {}).get("value", "active") if isinstance(d.get("status"), dict) else str(d.get("status", "active")),
                            "serial": d.get("serial") or ""
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

    async def get_devices_by_site(self, site_name: str) -> List[Dict[str, Any]]:
        """Retorna todos los dispositivos asignados a un sitio."""
        if not self._devices_cache:
            await self.refresh_cache()
        s_lower = site_name.strip().lower()
        return [d for d in self._devices_cache if (d.get("site") or "").strip().lower() == s_lower]

inventory_service = InventoryService()

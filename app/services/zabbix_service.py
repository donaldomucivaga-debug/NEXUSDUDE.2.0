import httpx
import logging
import asyncio
import time
import json
import re
from typing import List, Dict, Any, Optional, Set, Tuple
from app.config import settings
from app.database import get_db_connection

logger = logging.getLogger("nexusdude.zabbix")

# Jerarquía de roles de telecomunicaciones para determinar flujo de alimentación (Rank menor = más jerarquía)
ROLE_RANKS = {
    "core": 1,
    "border": 1,
    "gateway": 1,
    "router": 2,
    "olt": 3,
    "switch core": 3,
    "distribution": 3,
    "switch distribucion": 3,
    "switch": 4,
    "access": 4,
    "switch acceso": 4,
    "submap": 2,
    "ap": 5,
    "access point": 5,
    "radio": 5,
    "sector": 5,
    "server": 4,
    "cpe": 6,
    "antenna": 6,
    "antena": 6,
    "client": 6,
    "cliente": 6,
    "generic": 5
}

def get_role_rank(device_type: Optional[str]) -> int:
    """Calcula el rango jerárquico de un tipo de dispositivo en la topología."""
    if not device_type:
        return 5
    dt = device_type.lower()
    for key, rank in ROLE_RANKS.items():
        if key in dt:
            return rank
    return 5

class ZabbixService:
    def __init__(self):
        self.base_url = settings.ZABBIX_URL.rstrip('/')
        self.api_url = f"{self.base_url}/api_jsonrpc.php"
        self.auth_token: Optional[str] = None
        self._host_cache_by_name: Dict[str, Dict[str, Any]] = {}
        self._host_cache_by_ip: Dict[str, Dict[str, Any]] = {}
        self._cache_timestamp: float = 0

    async def _call_api(self, method: str, params: Any = None, auth: bool = True) -> Any:
        """Ejecuta una llamada JSON-RPC a la API de Zabbix con reintentos y manejo de token."""
        if auth and not self.auth_token:
            await self.login()

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
            "id": 1
        }
        if auth and self.auth_token:
            payload["auth"] = self.auth_token

        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            try:
                res = await client.post(self.api_url, json=payload)
                res.raise_for_status()
                data = res.json()
            except Exception as e:
                logger.error(f"Error de red comunicando con Zabbix ({method}): {e}")
                raise

            if "error" in data:
                err = data["error"]
                # Si el token expiró o no es válido, intentar re-login una vez
                if auth and err.get("code") in (-32602, -32500) and "Session terminated" in err.get("data", ""):
                    logger.info("Sesión Zabbix expirada. Renovando token...")
                    await self.login()
                    payload["auth"] = self.auth_token
                    res = await client.post(self.api_url, json=payload)
                    data = res.json()
                    if "error" in data:
                        raise Exception(f"Zabbix API Error ({method}): {data['error']}")
                else:
                    raise Exception(f"Zabbix API Error ({method}): {err.get('message')} - {err.get('data')}")

            return data.get("result")

    async def login(self) -> str:
        """Inicia sesión en Zabbix y guarda el token de autorización."""
        res = await self._call_api("user.login", {
            "username": settings.ZABBIX_USER,
            "password": settings.ZABBIX_PASS
        }, auth=False)

        if isinstance(res, str):
            self.auth_token = res
            logger.info("Autenticación con Zabbix 7.0 exitosa.")
            return self.auth_token
        raise Exception("Respuesta inesperada al autenticar en Zabbix")

    async def get_status(self) -> Dict[str, Any]:
        """Obtiene el estado de conexión con Zabbix y conteo de servicios."""
        try:
            version = await self._call_api("apiinfo.version", [], auth=False)
            token = await self.login()
            # Conteo de servicios administrados por NexusDude
            services = await self._call_api("service.get", {
                "output": ["serviceid", "name"],
                "tags": [{"tag": "managed_by", "value": "nexusdude"}]
            })
            total_hosts = await self._call_api("host.get", {"countOutput": True})

            return {
                "connected": True,
                "version": version,
                "zabbix_url": self.base_url,
                "managed_services_count": len(services) if services else 0,
                "total_zabbix_hosts": int(total_hosts) if total_hosts else 0
            }
        except Exception as e:
            logger.error(f"Error verificando estado de Zabbix: {e}")
            return {
                "connected": False,
                "error": str(e),
                "zabbix_url": self.base_url
            }

    async def refresh_zabbix_hosts_cache(self, force: bool = False):
        """Carga en memoria todos los hosts de Zabbix con sus IPs y Tags para matching rápido."""
        import time
        if not force and self._host_cache_by_name and (time.time() - self._cache_timestamp < 300):
            return

        logger.info("Cargando caché de hosts desde Zabbix...")
        hosts = await self._call_api("host.get", {
            "output": ["hostid", "host", "name", "status"],
            "selectInterfaces": ["interfaceid", "ip", "dns", "main", "type", "available", "error", "details"],
            "selectTags": "extend"
        })

        self._host_cache_by_name.clear()
        self._host_cache_by_ip.clear()

        for h in hosts:
            h_name = h.get("name", "").strip().lower()
            h_host = h.get("host", "").strip().lower()
            self._host_cache_by_name[h_name] = h
            self._host_cache_by_name[h_host] = h

            for iface in h.get("interfaces", []):
                ip = iface.get("ip", "").strip()
                if ip and ip != "127.0.0.1":
                    self._host_cache_by_ip[ip] = h

        self._cache_timestamp = time.time()
        logger.info(f"Caché Zabbix actualizada: {len(hosts)} hosts indexados.")

    def find_zabbix_host(self, name: str, ip: Optional[str]) -> Optional[Dict[str, Any]]:
        """Busca un host en la caché de Zabbix por nombre exacto, normalizado o IP."""
        if not name and not ip:
            return None

        # 1. Búsqueda por IP
        if ip and ip in self._host_cache_by_ip:
            return self._host_cache_by_ip[ip]

        # 2. Búsqueda por nombre exacto
        name_clean = name.strip().lower()
        if name_clean in self._host_cache_by_name:
            return self._host_cache_by_name[name_clean]

        # 3. Búsqueda normalizada (espacios a guiones bajos)
        name_norm = name_clean.replace(" ", "_").replace("-", "_")
        for k, v in self._host_cache_by_name.items():
            k_norm = k.replace(" ", "_").replace("-", "_")
            if name_norm == k_norm:
                return v

        return None

    async def ensure_host_tag(self, host_id: str, host_name: str, site_name: Optional[str] = None):
        """Garantiza que el host en Zabbix tenga la etiqueta 'host' para correlación con Zabbix Services."""
        try:
            current_tags = []
            h_cached = self._host_cache_by_name.get(host_name.lower())
            if h_cached:
                current_tags = h_cached.get("tags", [])

            tag_names = [t.get("tag") for t in current_tags]
            needs_update = False
            new_tags = list(current_tags)

            if "host" not in tag_names:
                new_tags.append({"tag": "host", "value": host_name})
                needs_update = True
            if "managed_by" not in tag_names:
                new_tags.append({"tag": "managed_by", "value": "nexusdude"})
                needs_update = True
            if site_name and "site" not in tag_names:
                new_tags.append({"tag": "site", "value": site_name})
                needs_update = True

            if needs_update:
                await self._call_api("host.update", {
                    "hostid": host_id,
                    "tags": new_tags
                })
                if h_cached:
                    h_cached["tags"] = new_tags
        except Exception as e:
            logger.warning(f"No se pudieron actualizar tags para host {host_name} ({host_id}): {e}")

    async def analyze_topology(self, map_id: str) -> Dict[str, Any]:
        """
        Analiza la topología de un mapa:
        - Determina la dirección del flujo de conectividad (Uplink -> Downlink).
        - Clasifica aristas en Directas (árbol serial) o Indirectas (redundancia / multipath / anillo).
        - Identifica nodos raíz (gateways / core).
        - Cruza cada nodo contra la base de datos de Zabbix.
        """
        await self.refresh_zabbix_hosts_cache()

        async with get_db_connection() as db:
            # Obtener datos del mapa
            c_map = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
            map_row = await c_map.fetchone()
            if not map_row:
                raise Exception(f"Mapa no encontrado: {map_id}")

            # Nodos
            c_nodes = await db.execute("SELECT * FROM nodes WHERE map_id = ?", (map_id,))
            node_rows = await c_nodes.fetchall()

            # Enlaces
            c_links = await db.execute("SELECT * FROM links WHERE map_id = ?", (map_id,))
            link_rows = await c_links.fetchall()

        nodes_dict = {r["id"]: dict(r) for r in node_rows}
        links = [dict(r) for r in link_rows]

        # Grados de entrada y salida
        in_degree: Dict[str, List[Dict[str, Any]]] = {nid: [] for nid in nodes_dict}
        out_degree: Dict[str, List[Dict[str, Any]]] = {nid: [] for nid in nodes_dict}
        adjacency: Dict[str, Set[str]] = {nid: set() for nid in nodes_dict}

        # Orientación jerárquica de aristas
        processed_links = []
        for l in links:
            s_id = l["source_node_id"]
            t_id = l["target_node_id"]
            if s_id not in nodes_dict or t_id not in nodes_dict:
                continue

            s_node = nodes_dict[s_id]
            t_node = nodes_dict[t_id]

            # Parsear extra_data del enlace
            l_extra = {}
            if l.get("extra_data"):
                try:
                    l_extra = json.loads(l["extra_data"]) if isinstance(l["extra_data"], str) else l["extra_data"]
                except Exception:
                    l_extra = {}

            # Ignorar enlaces marcados como sólo visuales o sin sincronización Zabbix para el grafo de servicios
            is_visual_only = bool(l_extra.get("is_visual_only") or l_extra.get("is_simple_link") or l_extra.get("sync_zabbix") is False)
            if is_visual_only:
                processed_links.append({
                    "link_id": l["id"],
                    "source_node_id": s_id,
                    "target_node_id": t_id,
                    "uplink_node_id": s_id,
                    "downlink_node_id": t_id,
                    "uplink_node_name": nodes_dict[s_id]["name"],
                    "downlink_node_name": nodes_dict[t_id]["name"],
                    "direction": l_extra.get("direction", "source_to_target"),
                    "status": l["status"],
                    "rtt_ms": l["rtt_ms"],
                    "is_visual_only": True
                })
                continue

            is_s_nav = s_node.get("device_type") in ("submap", "parent_map")
            is_t_nav = t_node.get("device_type") in ("submap", "parent_map")
            remote_node_id = l_extra.get("remote_node_id")

            if (is_s_nav or is_t_nav) and remote_node_id:
                local_dev_id = t_id if is_s_nav else s_id
                nav_node = s_node if is_s_nav else t_node
                is_parent_portal = nav_node.get("device_type") == "parent_map"

                if is_parent_portal:
                    in_degree[local_dev_id].append({"uplink_id": remote_node_id, "link": l, "is_intermap": True, "remote_name": l_extra.get("remote_node_name", "Uplink Remoto")})
                else:
                    out_degree[local_dev_id].append({"downlink_id": remote_node_id, "link": l, "is_intermap": True, "remote_name": l_extra.get("remote_node_name", "Downlink Remoto")})
            else:
                # ─── DETERMINAR DIRECCIÓN DEL SERVICIO ──────────────────────────
                # 1. Dirección explícita en extra_data del enlace ('source_to_target' vs 'target_to_source')
                direction = l_extra.get("direction", "source_to_target")

                if direction == "target_to_source":
                    # El nodo destino alimenta / da servicio al nodo origen
                    uplink_id, downlink_id = t_id, s_id
                else:
                    # Por defecto: el nodo origen alimenta / da servicio al nodo destino
                    uplink_id, downlink_id = s_id, t_id

                in_degree[downlink_id].append({"uplink_id": uplink_id, "link": l})
                out_degree[uplink_id].append({"downlink_id": downlink_id, "link": l})
                adjacency[uplink_id].add(downlink_id)

            processed_links.append({
                "link_id": l["id"],
                "source_node_id": s_id,
                "target_node_id": t_id,
                "uplink_node_id": uplink_id if not (is_s_nav or is_t_nav) else s_id,
                "downlink_node_id": downlink_id if not (is_s_nav or is_t_nav) else t_id,
                "uplink_node_name": nodes_dict[uplink_id]["name"] if not (is_s_nav or is_t_nav) and uplink_id in nodes_dict else nodes_dict[s_id]["name"],
                "downlink_node_name": nodes_dict[downlink_id]["name"] if not (is_s_nav or is_t_nav) and downlink_id in nodes_dict else nodes_dict[t_id]["name"],
                "direction": l_extra.get("direction", "source_to_target"),
                "status": l["status"],
                "rtt_ms": l["rtt_ms"]
            })

        # Clasificación de nodos
        node_analysis = []
        direct_relations_count = 0
        root_nodes_count = 0
        connected_nodes_count = 0
        isolated_nodes_count = 0

        for nid, n in nodes_dict.items():
            if n.get("device_type") in ("submap", "parent_map"):
                continue
            uplinks = in_degree[nid]
            downlinks = out_degree[nid]
            matched_host = self.find_zabbix_host(n["name"], n.get("ip"))

            has_links = len(uplinks) > 0 or len(downlinks) > 0

            if not has_links:
                relation_type = "isolated"
                isolated_nodes_count += 1
            elif len(uplinks) == 0:
                relation_type = "root"
                root_nodes_count += 1
                connected_nodes_count += 1
            else:
                relation_type = "direct"
                direct_relations_count += len(uplinks)
                connected_nodes_count += 1

            node_analysis.append({
                "node_id": nid,
                "name": n["name"],
                "ip": n.get("ip"),
                "device_type": n.get("device_type"),
                "rank": get_role_rank(n.get("device_type")),
                "relation_type": relation_type,
                "is_connected": has_links,
                "uplink_ids": [u["uplink_id"] for u in uplinks],
                "uplink_names": [nodes_dict[u["uplink_id"]]["name"] if u["uplink_id"] in nodes_dict else u.get("remote_name", "Remoto") for u in uplinks],
                "downlink_ids": [d["downlink_id"] for d in downlinks],
                "downlink_names": [nodes_dict[d["downlink_id"]]["name"] if d["downlink_id"] in nodes_dict else d.get("remote_name", "Remoto") for d in downlinks],
                "zabbix_matched": matched_host is not None,
                "zabbix_hostid": matched_host["hostid"] if matched_host else None,
                "zabbix_hostname": matched_host["name"] if matched_host else None
            })

        return {
            "map_id": map_id,
            "map_name": map_row["name"],
            "parent_map_id": map_row["parent_map_id"],
            "total_nodes": len(nodes_dict),
            "connected_nodes_count": connected_nodes_count,
            "isolated_nodes_count": isolated_nodes_count,
            "total_links": len(links),
            "root_nodes_count": root_nodes_count,
            "direct_relations_count": direct_relations_count,
            "nodes": node_analysis,
            "links": processed_links
        }

    async def get_descendant_map_ids(self, root_map_id: str) -> List[str]:
        """Obtiene recursivamente todos los IDs de mapas descendientes partiendo de root_map_id (incluyéndolo)."""
        async with get_db_connection() as db:
            c = await db.execute("SELECT id, parent_map_id FROM maps")
            all_maps = [dict(r) for r in await c.fetchall()]

        children_by_parent: Dict[str, List[str]] = {}
        for m in all_maps:
            p = m.get("parent_map_id")
            if p:
                children_by_parent.setdefault(p, []).append(m["id"])

        descendants = []
        queue = [root_map_id]
        visited = set()
        while queue:
            curr = queue.pop(0)
            if curr not in visited:
                visited.add(curr)
                descendants.append(curr)
                for child_id in children_by_parent.get(curr, []):
                    queue.append(child_id)
        return descendants

    async def clear_branch_services(self, map_ids: List[str]) -> int:
        """Elimina de Zabbix únicamente los servicios asociados a los mapas especificados."""
        if not map_ids:
            return 0
        await self.login()
        services = await self._call_api("service.get", {
            "output": ["serviceid", "name"],
            "selectTags": "extend",
            "tags": [{"tag": "managed_by", "value": "nexusdude"}]
        })
        if not services:
            return 0

        target_map_set = set(map_ids)
        to_delete_ids = []
        for s in services:
            for t in s.get("tags", []):
                if t.get("tag") == "map_id" and t.get("value") in target_map_set:
                    to_delete_ids.append(s["serviceid"])
                    break

        if to_delete_ids:
            await self._call_api("service.delete", to_delete_ids)
            logger.info(f"Se eliminaron {len(to_delete_ids)} servicios de la rama ({len(map_ids)} mapas) en Zabbix.")
            return len(to_delete_ids)
        return 0

    async def clear_nexus_services(self) -> int:
        """Elimina todos los servicios en Zabbix generados previamente por NexusDude."""
        await self.login()
        services = await self._call_api("service.get", {
            "output": ["serviceid", "name"],
            "tags": [{"tag": "managed_by", "value": "nexusdude"}]
        })

        if not services:
            return 0

        sids = [s["serviceid"] for s in services]
        await self._call_api("service.delete", sids)
        logger.info(f"Se eliminaron {len(sids)} servicios previos de NexusDude en Zabbix.")
        return len(sids)

    async def sync_all_to_zabbix(self, clear_first: bool = True) -> Dict[str, Any]:
        """Wrapper de compatibilidad para sincronización global completa."""
        return await self.sync_to_zabbix(scope="global", clear_first=clear_first)

    async def sync_to_zabbix(self, map_id: Optional[str] = None, scope: str = "global", clear_first: bool = True) -> Dict[str, Any]:
        """
        Sincroniza la arquitectura de NexusDude a Zabbix Services (BSM) como un árbol puro Host-a-Host:
        - Cada nodo dispositivo es un Servicio en Zabbix.
        - Las aristas direccionales (flechas Padre ➔ Hijo) definen quién es el padre/proveedor y quién es el hijo/consumidor.
        - No se crean contenedores de mapas ni submapas; la jerarquía es 100% de dispositivos de red.
        - scope="global": Sincroniza todos los nodos del sistema.
        - scope="branch": Sincroniza los nodos del mapa actual y sus submapas descendientes.
        """
        await self.login()
        await self.refresh_zabbix_hosts_cache(force=True)

        async with get_db_connection() as db:
            c_maps = await db.execute("SELECT * FROM maps ORDER BY created_at ASC")
            all_maps_rows = [dict(r) for r in await c_maps.fetchall()]

        if not all_maps_rows:
            return {"error": "No hay mapas configurados", "nodes_synced": 0}

        all_maps_dict = {m["id"]: m for m in all_maps_rows}

        if scope == "branch" and map_id:
            if map_id not in all_maps_dict:
                raise Exception(f"Mapa no encontrado: {map_id}")
            branch_map_ids = await self.get_descendant_map_ids(map_id)
            target_maps = [all_maps_dict[mid] for mid in branch_map_ids if mid in all_maps_dict]
            if clear_first:
                await self.clear_branch_services(branch_map_ids)
        else:
            scope = "global"
            target_maps = all_maps_rows
            if clear_first:
                await self.clear_nexus_services()

        target_map_ids = [m["id"] for m in target_maps]
        target_map_ids_set = set(target_map_ids)

        # ─── Paso 1: Obtener todos los nodos y enlaces de los mapas seleccionados ───
        async with get_db_connection() as db:
            map_ids_ph = ",".join(["?"] * len(target_map_ids))
            c_nodes = await db.execute(f"SELECT * FROM nodes WHERE map_id IN ({map_ids_ph})", target_map_ids)
            all_node_rows = [dict(r) for r in await c_nodes.fetchall()]

            c_links = await db.execute(f"SELECT * FROM links WHERE map_id IN ({map_ids_ph})", target_map_ids)
            all_link_rows = [dict(r) for r in await c_links.fetchall()]

        devices_dict: Dict[str, Dict[str, Any]] = {}
        portals_dict: Dict[str, Dict[str, Any]] = {}
        for n in all_node_rows:
            dtype = n.get("device_type")
            if dtype in ("submap", "parent_map"):
                portals_dict[n["id"]] = n
            elif dtype == "note":
                # Los nodos nota son puramente visuales; se excluyen de la sincronización Zabbix/BSM
                pass
            else:
                devices_dict[n["id"]] = n

        # ─── Paso 2: Construir Grafo Dirigido (Padre/Proveedor ➔ Hijo/Consumidor) ───
        uplink_parents: Dict[str, Set[str]] = {nid: set() for nid in devices_dict}
        downlink_children: Dict[str, Set[str]] = {nid: set() for nid in devices_dict}

        for l in all_link_rows:
            s_id = l["source_node_id"]
            t_id = l["target_node_id"]

            l_extra = {}
            if l.get("extra_data"):
                try:
                    l_extra = json.loads(l["extra_data"]) if isinstance(l["extra_data"], str) else l["extra_data"]
                except Exception:
                    l_extra = {}

            # Ignorar enlaces marcados como sólo visuales o sin sincronización Zabbix
            if l_extra.get("is_visual_only") or l_extra.get("is_simple_link") or l_extra.get("sync_zabbix") is False:
                continue

            direction = l_extra.get("direction", "source_to_target")

            is_s_portal = s_id in portals_dict
            is_t_portal = t_id in portals_dict

            if is_s_portal or is_t_portal:
                # Enlace inter-mapa a través de un portal de navegación
                local_dev_id = t_id if is_s_portal else s_id
                nav_node = portals_dict.get(s_id if is_s_portal else t_id, {})
                is_parent_portal = nav_node.get("device_type") == "parent_map"
                remote_node_id = l_extra.get("remote_node_id")

                if local_dev_id in devices_dict and remote_node_id:
                    if is_parent_portal:
                        # Portal a mapa padre: el remoto alimenta al local por defecto
                        p_id, c_id = (local_dev_id, remote_node_id) if direction == "target_to_source" else (remote_node_id, local_dev_id)
                    else:
                        # Portal a submapa: el local alimenta al remoto por defecto
                        p_id, c_id = (remote_node_id, local_dev_id) if direction == "target_to_source" else (local_dev_id, remote_node_id)

                    if c_id in uplink_parents:
                        uplink_parents[c_id].add(p_id)
                    if p_id in downlink_children:
                        downlink_children[p_id].add(c_id)

            elif s_id in devices_dict and t_id in devices_dict:
                # Enlace directo dentro del mapa
                if direction == "target_to_source":
                    p_id, c_id = t_id, s_id
                else:
                    p_id, c_id = s_id, t_id

                uplink_parents[c_id].add(p_id)
                downlink_children[p_id].add(c_id)

        # ─── Paso 2.5: Filtrar SOLO dispositivos que tengan relaciones activas ──────
        connected_device_ids = {
            nid for nid in devices_dict
            if len(uplink_parents[nid]) > 0 or len(downlink_children[nid]) > 0
        }

        # ─── Paso 3: Ordenación Topológica (Padres/Proveedores primero) ─────────────
        sorted_device_ids = []
        visited_ids = set()

        # Raíces: nodos conectados sin padres dentro del conjunto conectado
        for nid in connected_device_ids:
            parents_in_scope = [pid for pid in uplink_parents[nid] if pid in connected_device_ids]
            if not parents_in_scope:
                sorted_device_ids.append(nid)
                visited_ids.add(nid)

        # Nodos dependientes iterativamente
        remaining = [nid for nid in connected_device_ids if nid not in visited_ids]
        iterations = 0
        while remaining and iterations < 100:
            iterations += 1
            progress = False
            next_remaining = []
            for nid in remaining:
                parents_in_scope = [pid for pid in uplink_parents[nid] if pid in connected_device_ids]
                if all(pid in visited_ids for pid in parents_in_scope):
                    sorted_device_ids.append(nid)
                    visited_ids.add(nid)
                    progress = True
                else:
                    next_remaining.append(nid)
            remaining = next_remaining
            if not progress:
                sorted_device_ids.extend(remaining)
                break

        # ─── Paso 4: Crear Servicios Puros Host ➔ Host en Zabbix BSM ──────────────
        node_service_ids: Dict[str, str] = {}
        report = {
            "scope": scope,
            "root_map_id": map_id if scope == "branch" else None,
            "maps_processed": len(target_maps),
            "total_devices_in_maps": len(devices_dict),
            "connected_devices_count": len(connected_device_ids),
            "isolated_devices_skipped": len(devices_dict) - len(connected_device_ids),
            "root_devices_count": 0,
            "nodes_synced": 0,
            "nodes_matched_zabbix": 0,
            "direct_dependencies_created": 0,
            "details": []
        }

        for nid in sorted_device_ids:
            n_info = devices_dict[nid]
            node_name = n_info["name"]
            node_map_id = n_info["map_id"]

            matched = self.find_zabbix_host(node_name, n_info.get("ip"))
            z_name = matched["name"] if matched else None
            z_hostid = matched["hostid"] if matched else None

            # Problem tags
            problem_tags = []
            if matched and z_name:
                problem_tags.append({"tag": "host", "operator": 0, "value": z_name})
                if z_hostid:
                    await self.ensure_host_tag(str(z_hostid), z_name)
                report["nodes_matched_zabbix"] += 1

            # Determinar padres del servicio en Zabbix
            parent_node_ids = uplink_parents[nid]
            parents = []

            for p_id in parent_node_ids:
                if p_id in node_service_ids:
                    parents.append({"serviceid": node_service_ids[p_id]})
                    report["direct_dependencies_created"] += 1
                elif scope == "branch":
                    # Si el padre está fuera de la rama, buscar si ya existe en Zabbix
                    try:
                        ext_srv = await self._call_api("service.get", {
                            "output": ["serviceid", "name"],
                            "tags": [
                                {"tag": "managed_by", "value": "nexusdude"},
                                {"tag": "node_id", "value": p_id}
                            ]
                        })
                        if ext_srv:
                            parents.append({"serviceid": ext_srv[0]["serviceid"]})
                            report["direct_dependencies_created"] += 1
                    except Exception as p_err:
                        logger.warning(f"Error buscando servicio externo de nodo padre {p_id}: {p_err}")

            is_root = len(parents) == 0
            if is_root:
                report["root_devices_count"] += 1

            has_children = len(downlink_children[nid]) > 0
            algorithm = 2 if has_children else 0
            service_problem_tags = [] if has_children else problem_tags

            service_payload = {
                "name": f"[DEV] {node_name}",
                "algorithm": algorithm,
                "sortorder": 0,
                "parents": parents,
                "problem_tags": service_problem_tags,
                "tags": [
                    {"tag": "managed_by", "value": "nexusdude"},
                    {"tag": "nexus_type", "value": "device"},
                    {"tag": "node_id", "value": nid},
                    {"tag": "map_id", "value": node_map_id},
                    {"tag": "relation_type", "value": "root" if is_root else "direct"},
                    {"tag": "zabbix_matched", "value": "true" if matched else "false"}
                ]
            }

            try:
                s_res = await self._call_api("service.create", service_payload)
                node_s_id = s_res["serviceids"][0]
                node_service_ids[nid] = node_s_id
                report["nodes_synced"] += 1

                # Si tiene hijos y está vinculado a Zabbix, crear el servicio hoja [HOST]
                # para que las alarmas del propio equipo se propaguen al contenedor
                if has_children and problem_tags:
                    try:
                        host_leaf_payload = {
                            "name": f"[HOST] {node_name}",
                            "algorithm": 0,
                            "sortorder": 0,
                            "parents": [{"serviceid": node_s_id}],
                            "problem_tags": problem_tags,
                            "tags": [
                                {"tag": "managed_by", "value": "nexusdude"},
                                {"tag": "nexus_type", "value": "host_status"},
                                {"tag": "node_id", "value": nid},
                                {"tag": "map_id", "value": node_map_id}
                            ]
                        }
                        await self._call_api("service.create", host_leaf_payload)
                    except Exception as h_err:
                        logger.warning(f"Error creando servicio hoja [HOST] para {node_name}: {h_err}")

                report["details"].append({
                    "node_id": nid,
                    "node_name": node_name,
                    "service_id": node_s_id,
                    "is_root": is_root,
                    "parents_count": len(parents),
                    "children_count": len(downlink_children[nid]),
                    "zabbix_matched": matched is not None
                })
            except Exception as err:
                logger.error(f"Error creando servicio Zabbix para nodo {node_name}: {err}")

        logger.info(f"Sincronización NexusDude -> Zabbix Host-a-Host ({scope}) completada exitosamente: {report}")
        return report

    async def get_map_realtime_status(self, map_id: str) -> Dict[str, Any]:
        """
        Calcula y actualiza el estado operativo (online/offline/warning) en tiempo real
        de todos los nodos y submapas de un mapa, cruzando telemetría viva de Zabbix 7.0.
        """
        zabbix_connected = True
        try:
            await self.refresh_zabbix_hosts_cache()
        except Exception as zbx_err:
            zabbix_connected = False
            logger.warning(f"Zabbix inaccesible para mapa {map_id}: {zbx_err}")

        async with get_db_connection() as db:
            c_nodes = await db.execute("SELECT id, name, ip, device_type, site_name, extra_data, status FROM nodes WHERE map_id = ?", (map_id,))
            node_rows = await c_nodes.fetchall()

        if not node_rows:
            return {
                "map_id": map_id,
                "timestamp": time.time(),
                "zabbix_connected": zabbix_connected,
                "summary": {"total": 0, "online": 0, "offline": 0, "warning": 0, "unknown": 0},
                "nodes": {}
            }

        device_nodes = []
        submap_nodes = []
        branch_nodes = []
        for r in node_rows:
            nd = dict(r)
            dtype = nd.get("device_type")
            if dtype == "submap":
                submap_nodes.append(nd)
            elif dtype == "note":
                # Nodos nota son puramente visuales
                nodes_result[nd["id"]] = {
                    "node_id": nd["id"],
                    "name": nd["name"],
                    "status": "ok",
                    "ping_status": "ok",
                    "snmp_status": "ok",
                    "is_online": True,
                    "device_type": "note",
                    "zabbix_matched": False
                }
            elif dtype == "ftth_branch":
                branch_nodes.append(nd)
            else:
                device_nodes.append(nd)

        nodes_result = {}
        updates_to_db = []

        # SI ZABBIX ESTA CAIDO / INACCESIBLE: Todos los nodos se marcan en OFFLINE / DOWN
        if not zabbix_connected:
            for n in device_nodes:
                nid = n["id"]
                nodes_result[nid] = {
                    "node_id": nid,
                    "name": n["name"],
                    "ip": n.get("ip") or "",
                    "status": "down",
                    "ping_status": "down",
                    "snmp_status": "down",
                    "is_online": False,
                    "icmp_ping": 0,
                    "packet_loss": 100.0,
                    "rtt_ms": None,
                    "problems_count": 1,
                    "problems": [
                        {
                            "triggerid": "zbx_offline",
                            "description": "Zabbix Server inaccesible o fuera de línea (Telemetría perdida)",
                            "priority": 5,
                            "lastchange": int(time.time())
                        }
                    ],
                    "zabbix_matched": False
                }
                if n.get("status") != "down":
                    updates_to_db.append(("down", nid))

            for sm in submap_nodes:
                nid = sm["id"]
                nodes_result[nid] = {
                    "node_id": nid,
                    "name": sm["name"],
                    "ip": "",
                    "status": "down",
                    "ping_status": "down",
                    "snmp_status": "down",
                    "is_online": False,
                    "device_type": "submap",
                    "submap_down_devices": len(device_nodes),
                    "submap_warn_devices": 0,
                    "problems_count": 1,
                    "problems": [{"description": "Sitio con telemetría fuera de línea (Zabbix caído)", "priority": 5}],
                    "zabbix_matched": False
                }
                if sm.get("status") != "down":
                    updates_to_db.append(("down", nid))

        # SI ZABBIX ESTA CONECTADO: Procesar telemetría real
        elif device_nodes:
            host_map: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
            for n in device_nodes:
                h = self.find_zabbix_host(n["name"], n.get("ip"))
                if h:
                    hid = str(h["hostid"])
                    host_map.setdefault(hid, []).append((n, h))
                else:
                    nodes_result[n["id"]] = {
                        "node_id": n["id"],
                        "name": n["name"],
                        "ip": n.get("ip") or "",
                        "status": "unknown",
                        "ping_status": "unknown",
                        "snmp_status": "unknown",
                        "is_online": False,
                        "icmp_ping": None,
                        "packet_loss": None,
                        "rtt_ms": None,
                        "problems_count": 1,
                        "problems": [{"triggerid": "zbx_unmatched", "description": "Dispositivo sin host en Zabbix (Pendiente de sincronizar)", "priority": 1, "lastchange": int(time.time())}],
                        "zabbix_matched": False
                    }
                    if n.get("status") != "unknown":
                        updates_to_db.append(("unknown", n["id"]))

            if host_map:
                host_ids = list(host_map.keys())
                try:
                    triggers, items = await asyncio.gather(
                        self._call_api("trigger.get", {
                            "hostids": host_ids,
                            "filter": {"value": "1"},
                            "output": ["triggerid", "description", "priority", "lastchange"],
                            "selectHosts": ["hostid"]
                        }),
                        self._call_api("item.get", {
                            "hostids": host_ids,
                            "filter": {"key_": ["icmpping", "icmppingsec", "icmppingloss"]},
                            "output": ["hostid", "key_", "lastvalue", "lastclock"]
                        })
                    )
                except Exception as e:
                    logger.error(f"Error consultando telemetría en Zabbix: {e}")
                    triggers, items = [], []

                items_by_host: Dict[str, Dict[str, Any]] = {}
                for it in items:
                    hid = str(it["hostid"])
                    items_by_host.setdefault(hid, {})[it["key_"]] = it.get("lastvalue")

                triggers_by_host: Dict[str, List[Dict[str, Any]]] = {}
                for tr in triggers:
                    for h in tr.get("hosts", []):
                        hid = str(h["hostid"])
                        triggers_by_host.setdefault(hid, []).append(tr)

                for hid, node_host_pairs in host_map.items():
                    pdata = items_by_host.get(hid, {})
                    trig_list = triggers_by_host.get(hid, [])
                    z_host_info = node_host_pairs[0][1]

                    icmp_val = pdata.get("icmpping")
                    try:
                        loss_val = float(pdata.get("icmppingloss", 0) or 0)
                    except (ValueError, TypeError):
                        loss_val = 0.0

                    try:
                        rtt_sec = float(pdata.get("icmppingsec", 0) or 0)
                        rtt_ms = round(rtt_sec * 1000.0, 2) if rtt_sec > 0 else (0.0 if icmp_val == '0' else None)
                    except (ValueError, TypeError):
                        rtt_ms = None

                    has_down_trigger = any(
                        int(t.get("priority", 0)) >= 4 or
                        any(term in t.get("description", "").lower() for term in ("sin respuesta", "inaccesible", "unavailable", "offline"))
                        for t in trig_list
                    )
                    is_down = (icmp_val == '0') or has_down_trigger

                    has_warn_trigger = any(int(t.get("priority", 0)) in (2, 3) for t in trig_list)
                    is_warn = not is_down and (has_warn_trigger or loss_val > 10.0 or (rtt_ms and rtt_ms > 250))

                    if is_down:
                        calc_status = "down"
                        is_online = False
                    elif is_warn:
                        calc_status = "warning"
                        is_online = True
                    else:
                        calc_status = "ok"
                        is_online = True

                    # ─── PING STATUS (Exclusivo para conectividad ICMP y pérdida de paquetes) ───
                    if icmp_val == '0':
                        ping_status = "down"
                    elif icmp_val == '1':
                        if loss_val > 10.0 or (rtt_ms and rtt_ms > 250):
                            ping_status = "warning"
                        else:
                            ping_status = "ok"
                    else:
                        has_icmp_down = any(
                            any(term in t.get("description", "").lower() for term in ("sin respuesta de icmp", "icmp ping", "no icmp", "ping failed"))
                            for t in trig_list
                        )
                        if has_icmp_down:
                            ping_status = "down"
                        elif z_host_info:
                            ping_status = "ok"
                        else:
                            ping_status = "unknown"

                    # ─── SNMP STATUS (Exclusivo para interfaz y telemetría/sensores SNMP) ───
                    snmp_iface = next((i for i in z_host_info.get("interfaces", []) if str(i.get("type")) == "2"), None)
                    non_icmp_triggers = [
                        t for t in trig_list
                        if not any(term in t.get("description", "").lower() for term in ("sin respuesta de icmp", "icmp ping", "no icmp"))
                    ]
                    has_snmp_crit = any(int(t.get("priority", 0)) >= 4 for t in non_icmp_triggers)
                    has_snmp_warn = any(int(t.get("priority", 0)) in (2, 3) for t in non_icmp_triggers)

                    if snmp_iface:
                        snmp_avail = str(snmp_iface.get("available", "0"))
                        if snmp_avail == "1":
                            if has_snmp_crit:
                                snmp_status = "down"
                            elif has_snmp_warn:
                                snmp_status = "warning"
                            else:
                                snmp_status = "ok"
                        elif snmp_avail == "2":
                            snmp_status = "down"
                        else:
                            if has_snmp_crit:
                                snmp_status = "down"
                            elif has_snmp_warn:
                                snmp_status = "warning"
                            else:
                                snmp_status = "ok" if (pdata or not trig_list) else "unknown"
                    else:
                        if has_snmp_crit:
                            snmp_status = "down"
                        elif has_snmp_warn:
                            snmp_status = "warning"
                        else:
                            snmp_status = "unknown"

                    snmp_err = snmp_iface.get("error") if snmp_iface else None
                    snmp_comm = snmp_iface.get("details", {}).get("community") if snmp_iface else None
                    snmp_avail_int = int(snmp_iface.get("available", 0)) if snmp_iface else 0
                    has_snmp_issue = (ping_status in ("ok", "warning")) and (snmp_avail_int == 2 or snmp_status in ("down", "unknown") or bool(snmp_err))

                    formatted_problems = [
                        {
                            "triggerid": t.get("triggerid"),
                            "description": t.get("description"),
                            "priority": int(t.get("priority", 0)),
                            "lastchange": t.get("lastchange")
                        }
                        for t in trig_list
                    ]

                    for n, _ in node_host_pairs:
                        nid = n["id"]
                        nodes_result[nid] = {
                            "node_id": nid,
                            "name": n["name"],
                            "ip": n.get("ip") or "",
                            "status": calc_status,
                            "ping_status": ping_status,
                            "snmp_status": snmp_status,
                            "is_online": is_online,
                            "icmp_ping": int(icmp_val) if icmp_val in ('0', '1') else None,
                            "snmp_available": snmp_avail_int,
                            "snmp_error": snmp_err,
                            "snmp_community": snmp_comm,
                            "has_snmp_issue": has_snmp_issue,
                            "packet_loss": loss_val,
                            "rtt_ms": rtt_ms,
                            "problems_count": len(formatted_problems),
                            "problems": formatted_problems,
                            "zabbix_matched": True,
                            "zabbix_hostname": z_host_info.get("name"),
                            "zabbix_hostid": hid
                        }
                        if n.get("status") != calc_status:
                            updates_to_db.append((calc_status, nid))

        # 2. PROCESAR SUBMAPAS (Agregación de salud por sitio)
        if submap_nodes:
            from app.services.inventory_service import inventory_service
            site_by_name = {}
            for d in inventory_service._devices_cache:
                s = (d.get("site") or "").strip().lower()
                if s:
                    site_by_name[d["name"].strip().lower()] = s
                    if d.get("ip"):
                        site_by_name[d["ip"].strip()] = s

            try:
                all_triggers = await self._call_api("trigger.get", {
                    "output": ["triggerid", "description", "priority"],
                    "filter": {"value": "1"},
                    "selectHosts": ["hostid", "name"]
                })
            except Exception as e:
                logger.error(f"Error consultando triggers para submapas: {e}")
                all_triggers = []

            site_down_counts: Dict[str, int] = {}
            site_warn_counts: Dict[str, int] = {}

            for tr in all_triggers:
                prio = int(tr.get("priority", 0))
                desc = tr.get("description", "").lower()
                is_crit = prio >= 4 or any(w in desc for w in ("sin respuesta", "inaccesible", "unavailable", "offline"))
                for h in tr.get("hosts", []):
                    h_lower = h["name"].strip().lower()
                    st = site_by_name.get(h_lower)
                    if st:
                        if is_crit:
                            site_down_counts[st] = site_down_counts.get(st, 0) + 1
                        else:
                            site_warn_counts[st] = site_warn_counts.get(st, 0) + 1

            for sm in submap_nodes:
                sm_name_clean = (sm.get("name") or "").strip().lower()
                downs = site_down_counts.get(sm_name_clean, 0)
                warns = site_warn_counts.get(sm_name_clean, 0)

                if downs > 0:
                    sm_status = "down"
                    sm_ping_status = "down"
                elif warns > 0:
                    sm_status = "warning"
                    sm_ping_status = "warning"
                else:
                    sm_status = "ok"
                    sm_ping_status = "ok"

                sm_snmp_status = "warning" if warns > 0 else ("down" if downs > 0 else "ok")

                nid = sm["id"]
                prob_desc = []
                if downs:
                    prob_desc.append({"description": f"{downs} equipos fuera de línea", "priority": 4})
                if warns:
                    prob_desc.append({"description": f"{warns} equipos con alertas de degradación", "priority": 2})

                nodes_result[nid] = {
                    "node_id": nid,
                    "name": sm["name"],
                    "ip": "",
                    "status": sm_status,
                    "ping_status": sm_ping_status,
                    "snmp_status": sm_snmp_status,
                    "is_online": (sm_status == "ok"),
                    "device_type": "submap",
                    "submap_down_devices": downs,
                    "submap_warn_devices": warns,
                    "problems_count": downs + warns,
                    "problems": prob_desc,
                    "zabbix_matched": True
                }
                if sm.get("status") != sm_status:
                    updates_to_db.append((sm_status, nid))

        # 3. ACTUALIZAR BASE DE DATOS LOCAL
        if updates_to_db:
            async with get_db_connection() as db:
                await db.executemany("UPDATE nodes SET status = ? WHERE id = ?", updates_to_db)
                await db.commit()

        # 4. RESUMEN GLOBAL DEL MAPA
        total_count = len(nodes_result)
        online_count = sum(1 for n in nodes_result.values() if n["status"] == "ok")
        offline_count = sum(1 for n in nodes_result.values() if n["status"] == "down")
        warn_count = sum(1 for n in nodes_result.values() if n["status"] == "warning")
        unknown_count = total_count - (online_count + offline_count + warn_count)

        return {
            "map_id": map_id,
            "timestamp": time.time(),
            "zabbix_connected": zabbix_connected,
            "summary": {
                "total": total_count,
                "online": online_count,
                "offline": offline_count,
                "warning": warn_count,
                "unknown": unknown_count
            },
            "nodes": nodes_result
        }

    async def get_node_telemetry(self, node_id: str) -> Dict[str, Any]:
        """Obtiene la telemetría detallada en tiempo real de un nodo específico."""
        zabbix_connected = True
        try:
            await self.refresh_zabbix_hosts_cache()
        except Exception as zbx_err:
            zabbix_connected = False
            logger.warning(f"Zabbix inaccesible para telemetría de nodo {node_id}: {zbx_err}")

        async with get_db_connection() as db:
            c = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            n = await c.fetchone()
            if not n:
                raise Exception(f"Nodo no encontrado: {node_id}")

        n_dict = dict(n)
        if not zabbix_connected:
            return {
                "node_id": node_id,
                "name": n_dict["name"],
                "ip": n_dict.get("ip") or "",
                "status": "down",
                "is_online": False,
                "zabbix_matched": False,
                "message": "Servidor Zabbix fuera de línea o inaccesible",
                "problems": [
                    {
                        "triggerid": "zbx_offline",
                        "description": "Zabbix Server inaccesible o fuera de línea (Telemetría perdida)",
                        "priority": 5,
                        "lastchange": int(time.time())
                    }
                ]
            }

        h = self.find_zabbix_host(n_dict["name"], n_dict.get("ip"))
        if not h:
            return {
                "node_id": node_id,
                "name": n_dict["name"],
                "ip": n_dict.get("ip") or "",
                "status": "unknown",
                "is_online": False,
                "zabbix_matched": False,
                "message": "Dispositivo sin host en Zabbix (Pendiente de sincronizar)",
                "problems": [
                    {
                        "triggerid": "zbx_unmatched",
                        "description": "Dispositivo sin monitoreo activo en Zabbix",
                        "priority": 1,
                        "lastchange": int(time.time())
                    }
                ]
            }

        hid = str(h["hostid"])
        telemetry_keys = [
            # Ping e ICMP
            "icmpping", "icmppingsec", "icmppingloss",
            # Cambium ePMP / Force 4600 / 300 / 200 / 180 / 130
            "cambium.radio.chwidth", "cambium.radio.bandwidth", "cambium.radio.freq", "cambium.radio.tx_power",
            "cambium.radio.rssi", "cambium.cpe.rssi", "cambium.radio.snr", "cambium.cpe.snr",
            "cambium.cpe.rx_mcs", "cambium.cpe.tx_mcs", "cambium.cpe.distance_km", "cambium.cpe.ssid",
            "cambium.cpe.ap_mac", "cambium.ap.connected_sms", "cambium.ap.connected_sta_count",
            "cambium.serial", "cambium.sw.version", "cambium.model", "cambium.mac",
            "cambium.lan.speed", "net.if.status[LAN]", "net.if.in[LAN]", "net.if.out[LAN]",
            # Altai SuperWiFi C1n / C1xn / WA1011N-G
            "altai.radio.channel", "altai.radio.mode", "altai.radio.tx_power",
            "altai.hw.model", "altai.hw.serial", "altai.sw.version", "altai.mac", "altai.ip",
            "net.if.status[eth0]", "net.if.in[eth0]", "net.if.out[eth0]",
            # MikroTik Routers & Switches
            "system.cpu.util.total", "system.cpu.util[0]", "vm.memory.util",
            "sensor.temp.board", "sensor.temp.cpu", "sensor.voltage",
            "sensor.power.psu1.state", "sensor.power.psu2.state",
            # Ubiquiti AirMAX
            "ubnt.radio.freq", "ubnt.radio.rssi", "ubnt.radio.signal", "ubnt.radio.ccq",
            "ubnt.radio.distance", "ubnt.sw.version",
            # Mimosa
            "mimosa.radio.freq", "mimosa.radio.rssi", "mimosa.radio.tx_power", "mimosa.sw.version",
            # Sistema general
            "system.uptime", "system.name", "system.descr"
        ]

        triggers, items = await asyncio.gather(
            self._call_api("trigger.get", {
                "hostids": [hid],
                "filter": {"value": "1"},
                "output": ["triggerid", "description", "priority", "lastchange"]
            }),
            self._call_api("item.get", {
                "hostids": [hid],
                "filter": {
                    "key_": telemetry_keys
                },
                "output": ["itemid", "name", "key_", "lastvalue", "units", "lastclock"]
            })
        )

        pdata = {it["key_"]: it.get("lastvalue") for it in items}
        icmp_val = pdata.get("icmpping")
        loss_val = float(pdata.get("icmppingloss", 0) or 0)
        try:
            rtt_sec = float(pdata.get("icmppingsec", 0) or 0)
            rtt_ms = round(rtt_sec * 1000.0, 2) if rtt_sec > 0 else (0.0 if icmp_val == '0' else None)
        except Exception:
            rtt_ms = None

        has_down_trigger = any(
            int(t.get("priority", 0)) >= 4 or
            any(term in t.get("description", "").lower() for term in ("sin respuesta", "inaccesible", "unavailable", "offline"))
            for t in triggers
        )
        is_down = (icmp_val == '0') or has_down_trigger
        has_warn_trigger = any(int(t.get("priority", 0)) in (2, 3) for t in triggers)
        is_warn = not is_down and (has_warn_trigger or loss_val > 10.0 or (rtt_ms and rtt_ms > 250))

        status = "down" if is_down else ("warning" if is_warn else "ok")

        # Mapeo de Ancho de Canal Cambium (SNMP Enum -> MHz)
        CAMBIUM_BANDWIDTH_MAP = {
            "1": "20 MHz",
            "2": "40 MHz",
            "3": "10 MHz",
            "4": "5 MHz",
            "5": "80 MHz",
            "6": "160 MHz"
        }
        bw_raw = pdata.get("cambium.radio.chwidth") or pdata.get("cambium.radio.bandwidth")
        bw_text = CAMBIUM_BANDWIDTH_MAP.get(str(bw_raw)) if (bw_raw is not None and str(bw_raw) != '0') else (f"{bw_raw} MHz" if bw_raw and str(bw_raw).isdigit() and int(bw_raw) > 6 else None)

        # 1. Telemetría Inalámbrica
        wireless_info = None
        has_wireless = (
            bw_raw is not None or
            "cambium.radio.freq" in pdata or "cambium.radio.rssi" in pdata or "cambium.cpe.rssi" in pdata or
            "altai.radio.channel" in pdata or "ubnt.radio.freq" in pdata or "mimosa.radio.freq" in pdata
        )

        if has_wireless:
            freq_val = pdata.get("cambium.radio.freq") or pdata.get("ubnt.radio.freq") or pdata.get("mimosa.radio.freq")
            altai_chan = pdata.get("altai.radio.channel")
            if not freq_val and altai_chan:
                # Canal Wi-Fi 2.4 GHz
                try:
                    c_num = int(altai_chan)
                    freq_val = str(2407 + (c_num * 5)) if 1 <= c_num <= 14 else str(altai_chan)
                except Exception:
                    freq_val = str(altai_chan)

            rssi_val = pdata.get("cambium.cpe.rssi") or pdata.get("cambium.radio.rssi") or pdata.get("ubnt.radio.rssi") or pdata.get("ubnt.radio.signal") or pdata.get("mimosa.radio.rssi")
            snr_val = pdata.get("cambium.cpe.snr") or pdata.get("cambium.radio.snr")
            tx_pow = pdata.get("cambium.radio.tx_power") or pdata.get("altai.radio.tx_power") or pdata.get("mimosa.radio.tx_power")
            sta_cnt = pdata.get("cambium.ap.connected_sms") or pdata.get("cambium.ap.connected_sta_count")

            wireless_info = {
                "channel_width_id": bw_raw or (altai_chan if altai_chan else None),
                "channel_width_text": bw_text or ("20/40 MHz" if altai_chan else "—"),
                "frequency_mhz": freq_val,
                "tx_power_dbm": tx_pow,
                "rssi_dbm": rssi_val,
                "snr_db": snr_val,
                "rx_mcs": pdata.get("cambium.cpe.rx_mcs"),
                "tx_mcs": pdata.get("cambium.cpe.tx_mcs"),
                "distance_km": pdata.get("cambium.cpe.distance_km") or pdata.get("ubnt.radio.distance"),
                "ssid": pdata.get("cambium.cpe.ssid"),
                "connected_ap_mac": pdata.get("cambium.cpe.ap_mac"),
                "connected_sta_count": sta_cnt,
                "mode": pdata.get("altai.radio.mode") or ("AP" if sta_cnt is not None else "CPE")
            }

        # 2. Información del Sistema y Hardware
        def format_uptime(seconds_val):
            if not seconds_val:
                return "—"
            try:
                s = int(float(seconds_val))
                days, s = divmod(s, 86400)
                hours, s = divmod(s, 3600)
                minutes, _ = divmod(s, 60)
                parts = []
                if days > 0: parts.append(f"{days}d")
                if hours > 0: parts.append(f"{hours}h")
                parts.append(f"{minutes}m")
                return " ".join(parts)
            except Exception:
                return str(seconds_val)

        system_info = {
            "model": pdata.get("cambium.model") or pdata.get("altai.hw.model"),
            "serial": pdata.get("cambium.serial") or pdata.get("altai.hw.serial"),
            "firmware": pdata.get("cambium.sw.version") or pdata.get("altai.sw.version") or pdata.get("ubnt.sw.version") or pdata.get("mimosa.sw.version"),
            "mac": pdata.get("cambium.mac") or pdata.get("altai.mac"),
            "uptime_seconds": pdata.get("system.uptime"),
            "uptime_text": format_uptime(pdata.get("system.uptime")),
            "sys_name": pdata.get("system.name"),
            "sys_descr": pdata.get("system.descr")
        }

        # 3. Métricas de Hardware (Routers / Switches / MikroTik)
        hardware_info = None
        has_hw = any(k in pdata for k in ["system.cpu.util.total", "system.cpu.util[0]", "vm.memory.util", "sensor.temp.cpu", "sensor.temp.board", "sensor.voltage"])
        if has_hw:
            hardware_info = {
                "cpu_util_pct": pdata.get("system.cpu.util.total") or pdata.get("system.cpu.util[0]"),
                "memory_util_pct": pdata.get("vm.memory.util"),
                "temp_cpu_c": pdata.get("sensor.temp.cpu"),
                "temp_board_c": pdata.get("sensor.temp.board"),
                "voltage_v": pdata.get("sensor.voltage")
            }

        # 4. Estadísticas de Interfaz LAN
        lan_info = None
        has_lan = any(k in pdata for k in ["net.if.status[LAN]", "net.if.in[LAN]", "net.if.out[LAN]", "net.if.status[eth0]", "net.if.in[eth0]", "net.if.out[eth0]"])
        if has_lan:
            in_bps = pdata.get("net.if.in[LAN]") or pdata.get("net.if.in[eth0]")
            out_bps = pdata.get("net.if.out[LAN]") or pdata.get("net.if.out[eth0]")
            status_lan = pdata.get("net.if.status[LAN]") or pdata.get("net.if.status[eth0]")
            speed_mbps = pdata.get("cambium.lan.speed")

            def fmt_bps(val):
                if val is None: return "—"
                try:
                    b = float(val)
                    if b >= 1_000_000_000: return f"{b/1_000_000_000:.2f} Gbps"
                    if b >= 1_000_000: return f"{b/1_000_000:.2f} Mbps"
                    if b >= 1_000: return f"{b/1_000:.2f} Kbps"
                    return f"{b:.0f} bps"
                except Exception:
                    return str(val)

            lan_info = {
                "status": "Up" if str(status_lan) in ("1", "up") else "Down",
                "speed_mbps": speed_mbps,
                "in_bps": in_bps,
                "in_text": fmt_bps(in_bps),
                "out_bps": out_bps,
                "out_text": fmt_bps(out_bps)
            }

        # ─── PING STATUS (Exclusivo para conectividad ICMP) ───
        if icmp_val == '0':
            ping_status = "down"
        elif icmp_val == '1':
            if loss_val > 10.0 or (rtt_ms and rtt_ms > 250):
                ping_status = "warning"
            else:
                ping_status = "ok"
        else:
            has_icmp_down = any(
                any(term in t.get("description", "").lower() for term in ("sin respuesta de icmp", "icmp ping", "no icmp", "ping failed"))
                for t in triggers
            )
            if has_icmp_down:
                ping_status = "down"
            elif h:
                ping_status = "ok"
            else:
                ping_status = "unknown"

        # ─── SNMP STATUS (Exclusivo para interfaz y telemetría/sensores SNMP) ───
        snmp_iface = next((i for i in h.get("interfaces", []) if str(i.get("type")) == "2"), None)
        non_icmp_triggers = [
            t for t in triggers
            if not any(term in t.get("description", "").lower() for term in ("sin respuesta de icmp", "icmp ping", "no icmp"))
        ]
        has_snmp_crit = any(int(t.get("priority", 0)) >= 4 for t in non_icmp_triggers)
        has_snmp_warn = any(int(t.get("priority", 0)) in (2, 3) for t in non_icmp_triggers)

        if snmp_iface:
            snmp_avail = str(snmp_iface.get("available", "0"))
            if snmp_avail == "1":
                if has_snmp_crit:
                    snmp_status = "down"
                elif has_snmp_warn:
                    snmp_status = "warning"
                else:
                    snmp_status = "ok"
            elif snmp_avail == "2":
                snmp_status = "down"
            else:
                if has_snmp_crit:
                    snmp_status = "down"
                elif has_snmp_warn:
                    snmp_status = "warning"
                else:
                    snmp_status = "ok" if (pdata or not triggers) else "unknown"
        else:
            if has_snmp_crit:
                snmp_status = "down"
            elif has_snmp_warn:
                snmp_status = "warning"
            else:
                snmp_status = "unknown"

        snmp_err = snmp_iface.get("error") if snmp_iface else None
        snmp_comm = snmp_iface.get("details", {}).get("community") if snmp_iface else None
        snmp_avail_int = int(snmp_iface.get("available", 0)) if snmp_iface else 0
        has_snmp_issue = (ping_status in ("ok", "warning")) and (snmp_avail_int == 2 or snmp_status in ("down", "unknown") or bool(snmp_err))
        
        snmp_warning_msg = None
        if has_snmp_issue:
            if snmp_err:
                snmp_warning_msg = f"Ping responde correctamente (ICMP OK), pero SNMP reporta fallo: {snmp_err}"
            elif snmp_avail_int == 2:
                snmp_warning_msg = "Ping responde correctamente (ICMP OK), pero las consultas SNMP están en Timeout (puerto 161/UDP o comunidad no coincide)."
            else:
                snmp_warning_msg = "Ping responde correctamente (ICMP OK), pero no se recibe telemetría SNMP en las métricas de Zabbix."

        return {
            "node_id": node_id,
            "name": n_dict["name"],
            "ip": n_dict.get("ip") or "",
            "status": status,
            "ping_status": ping_status,
            "snmp_status": snmp_status,
            "is_online": (status != "down"),
            "icmp_ping": int(icmp_val) if icmp_val in ('0', '1') else None,
            "snmp_available": snmp_avail_int,
            "snmp_error": snmp_err,
            "snmp_community": snmp_comm,
            "has_snmp_issue": has_snmp_issue,
            "snmp_warning_message": snmp_warning_msg,
            "packet_loss": loss_val,
            "rtt_ms": rtt_ms,
            "wireless": wireless_info,
            "system": system_info,
            "hardware": hardware_info,
            "lan": lan_info,
            "zabbix_matched": True,
            "zabbix_hostid": hid,
            "zabbix_hostname": h.get("name"),
            "problems": [
                {
                    "triggerid": t.get("triggerid"),
                    "description": t.get("description"),
                    "priority": int(t.get("priority", 0)),
                    "lastchange": t.get("lastchange")
                }
                for t in triggers
            ]
        }

    async def get_map_spectrum_data(self, map_id: str = "default-map") -> Dict[str, Any]:
        """
        Obtiene los parámetros de espectro y radiofrecuencia (4850 MHz - 7250 MHz)
        de los dispositivos inalámbricos (APs, CPEs, Sectores, etc.) del mapa seleccionado
        o de todos los mapas cruzados con Zabbix 7.0.
        """
        await self.refresh_zabbix_hosts_cache()

        async with get_db_connection() as db:
            if map_id == "all":
                map_info = {"id": "all", "name": "Todos los Mapas (Global)"}
                c_nodes = await db.execute("""
                    SELECT n.id, n.map_id, n.name, n.ip, n.device_type, n.site_name, n.status, m.name as map_name
                    FROM nodes n
                    LEFT JOIN maps m ON n.map_id = m.id
                    WHERE n.device_type != 'submap'
                """)
            else:
                c_map = await db.execute("SELECT id, name FROM maps WHERE id = ?", (map_id,))
                map_row = await c_map.fetchone()
                map_info = dict(map_row) if map_row else {"id": map_id, "name": "Mapa"}
                c_nodes = await db.execute("""
                    SELECT n.id, n.map_id, n.name, n.ip, n.device_type, n.site_name, n.status, m.name as map_name
                    FROM nodes n
                    LEFT JOIN maps m ON n.map_id = m.id
                    WHERE n.map_id = ? AND n.device_type != 'submap'
                """, (map_id,))

            node_rows = await c_nodes.fetchall()

        if not node_rows:
            return {
                "map_id": map_id,
                "map_name": map_info.get("name", ""),
                "min_freq_mhz": 4850,
                "max_freq_mhz": 7250,
                "total_wireless_devices": 0,
                "devices": []
            }

        host_map: Dict[str, List[Dict[str, Any]]] = {}
        for r in node_rows:
            nd = dict(r)
            h = self.find_zabbix_host(nd["name"], nd.get("ip"))
            if h:
                hid = str(h["hostid"])
                host_map.setdefault(hid, []).append(nd)

        if not host_map:
            return {
                "map_id": map_id,
                "map_name": map_info.get("name", ""),
                "min_freq_mhz": 4850,
                "max_freq_mhz": 7250,
                "total_wireless_devices": 0,
                "devices": []
            }

        host_ids = list(host_map.keys())

        # Consultar métricas de radio de todos los hosts
        items = await self._call_api("item.get", {
            "hostids": host_ids,
            "filter": {
                "key_": [
                    "cambium.radio.chwidth", "cambium.radio.bandwidth", "cambium.radio.freq", "cambium.radio.tx_power",
                    "cambium.radio.rssi", "cambium.cpe.rssi", "cambium.radio.snr", "cambium.cpe.snr",
                    "cambium.sw.version", "cambium.ap.connected_sms", "cambium.ap.connected_sta_count",
                    "ubnt.radio.freq", "ubnt.radio.rssi", "ubnt.radio.signal", "ubnt.sw.version",
                    "mimosa.radio.freq", "mimosa.radio.rssi", "mimosa.radio.tx_power", "mimosa.sw.version",
                    "icmpping", "icmppingsec"
                ]
            },
            "output": ["hostid", "name", "key_", "lastvalue", "lastclock"]
        })

        host_items: Dict[str, Dict[str, Any]] = {}
        for it in items:
            hid = str(it["hostid"])
            host_items.setdefault(hid, {})[it["key_"]] = it.get("lastvalue")

        CAMBIUM_BW_MAP = {
            "1": 20,
            "2": 40,
            "3": 10,
            "4": 5,
            "5": 80,
            "6": 160
        }

        devices_with_rf = []
        for hid, nodes_list in host_map.items():
            pdata = host_items.get(hid, {})
            freq_str = pdata.get("cambium.radio.freq") or pdata.get("ubnt.radio.freq") or pdata.get("mimosa.radio.freq")
            bw_id_str = pdata.get("cambium.radio.chwidth") or pdata.get("cambium.radio.bandwidth")

            try:
                freq_mhz = float(freq_str) if freq_str and freq_str != "0" else None
            except Exception:
                freq_mhz = None

            bw_mhz = None
            if bw_id_str and str(bw_id_str) in CAMBIUM_BW_MAP:
                bw_mhz = CAMBIUM_BW_MAP[str(bw_id_str)]
            elif bw_id_str and str(bw_id_str).isdigit() and int(bw_id_str) > 0:
                if int(bw_id_str) in (5, 10, 20, 40, 80, 160):
                    bw_mhz = int(bw_id_str)
                else:
                    bw_mhz = CAMBIUM_BW_MAP.get(str(bw_id_str), 20)

            # Si tiene frecuencia válida en rango de microondas / wireless
            if freq_mhz and freq_mhz >= 4000 and freq_mhz <= 7500:
                bandwidth_final = bw_mhz if bw_mhz else 20
                freq_start = round(freq_mhz - (bandwidth_final / 2.0), 2)
                freq_end = round(freq_mhz + (bandwidth_final / 2.0), 2)

                for n in nodes_list:
                    devices_with_rf.append({
                        "node_id": n["id"],
                        "map_id": n.get("map_id"),
                        "map_name": n.get("map_name") or map_info.get("name", ""),
                        "name": n["name"],
                        "ip": n.get("ip") or "",
                        "device_type": n.get("device_type") or "generic",
                        "site_name": n.get("site_name") or "",
                        "status": n.get("status") or "ok",
                        "zabbix_hostid": hid,
                        "frequency_mhz": freq_mhz,
                        "bandwidth_id": bw_id_str,
                        "bandwidth_mhz": bandwidth_final,
                        "bandwidth_text": f"{bandwidth_final} MHz",
                        "freq_start_mhz": freq_start,
                        "freq_end_mhz": freq_end,
                        "tx_power_dbm": pdata.get("cambium.radio.tx_power") or pdata.get("mimosa.radio.tx_power"),
                        "rssi_dbm": pdata.get("cambium.cpe.rssi") or pdata.get("cambium.radio.rssi") or pdata.get("ubnt.radio.rssi"),
                        "snr_db": pdata.get("cambium.cpe.snr") or pdata.get("cambium.radio.snr"),
                        "firmware": pdata.get("cambium.sw.version") or pdata.get("ubnt.sw.version") or pdata.get("mimosa.sw.version"),
                        "connected_sta_count": pdata.get("cambium.ap.connected_sms") or pdata.get("cambium.ap.connected_sta_count"),
                        "rtt_ms": round(float(pdata.get("icmppingsec", 0)) * 1000, 1) if pdata.get("icmppingsec") else None
                    })

        # Ordenar por frecuencia ascendente
        devices_with_rf.sort(key=lambda d: (d["frequency_mhz"], d["name"]))

        return {
            "map_id": map_id,
            "map_name": map_info.get("name", ""),
            "min_freq_mhz": 4850,
            "max_freq_mhz": 7250,
            "total_wireless_devices": len(devices_with_rf),
            "devices": devices_with_rf
        }

    async def get_node_zabbix_interfaces(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Obtiene las interfaces de red/radio/ópticas monitoreadas en Zabbix para un nodo.
        Retorna la lista de fuentes de datos disponibles para vincular a las aristas (enlaces).
        """
        await self.refresh_zabbix_hosts_cache()

        async with get_db_connection() as db:
            c = await db.execute("SELECT id, name, ip, device_type, extra_data FROM nodes WHERE id = ?", (node_id,))
            n = await c.fetchone()
            if not n:
                return []

        h = self.find_zabbix_host(n["name"], n["ip"])
        if not h:
            return []

        hid = str(h["hostid"])
        items = await self._call_api("item.get", {
            "hostids": [hid],
            "output": ["itemid", "name", "key_", "lastvalue", "units", "status", "lastclock"],
            "limit": 300
        })

        interfaces_dict: Dict[str, Dict[str, Any]] = {}

        def fmt_bps(val):
            try:
                num = float(val)
                if num >= 1_000_000_000:
                    return f"{num / 1_000_000_000:.2f} Gbps"
                elif num >= 1_000_000:
                    return f"{num / 1_000_000:.2f} Mbps"
                elif num >= 1_000:
                    return f"{num / 1_000:.1f} Kbps"
                else:
                    return f"{num:.0f} bps"
            except Exception:
                return "0 bps"

        for it in items:
            name = it.get("name", "")
            key = it.get("key_", "")
            val = it.get("lastvalue")

            # 1. Interfaces SNMP Estándar o Ethernet / SFP
            if "net.if.in[" in key or "net.if.out[" in key or "sensor.optical." in key or "net.if.status[" in key or "net.if.speed[" in key or ("Interface" in name and any(k in name.lower() for k in ["traffic", "bits", "bytes", "octets", "speed", "optical", "status"])):
                if_name = None
                m_key = re.search(r'\[([^\]]+)\]', key)
                if m_key:
                    param = m_key.group(1).split(",")[-1].strip()
                    if not param.startswith("if") or len(param) < 15:
                        if_name = param

                if not if_name or if_name.startswith("if"):
                    m_name = re.match(r'(?:Interface|Port|Interfaz)\s+([^:]+)', name, re.IGNORECASE)
                    if m_name:
                        if_name = m_name.group(1).strip()
                    else:
                        m_name2 = re.match(r'^([^:]+):', name)
                        if m_name2 and not any(k in m_name2.group(1).lower() for k in ["icmp", "system", "cpu", "memory", "ping"]):
                            if_name = m_name2.group(1).strip()

                if not if_name:
                    if_name = key.split("[")[0] if "[" in key else name

                if if_name not in interfaces_dict:
                    is_opt = any(k in if_name.lower() or k in name.lower() for k in ["sfp", "optical", "fiber", "xg", "ge25", "ge26", "ge27", "ge28"])
                    interfaces_dict[if_name] = {
                        "name": if_name,
                        "key": if_name,
                        "display_name": f"⚡ {if_name}" if is_opt else f"🔌 {if_name}",
                        "type": "optical" if is_opt else "ethernet",
                        "status": "up",
                        "speed": "1 Gbps",
                        "traffic_in_bps": 0,
                        "traffic_out_bps": 0,
                        "traffic_in_fmt": "—",
                        "traffic_out_fmt": "—",
                        "optical": {},
                        "wireless": {},
                        "item_ids": []
                    }

                entry = interfaces_dict[if_name]
                entry["item_ids"].append(it.get("itemid"))

                if "net.if.in" in key or "bits received" in name.lower() or "inbound" in name.lower():
                    try:
                        entry["traffic_in_bps"] = float(val) if val else 0
                        entry["traffic_in_fmt"] = fmt_bps(val)
                    except Exception:
                        pass
                elif "net.if.out" in key or "bits sent" in name.lower() or "outbound" in name.lower():
                    try:
                        entry["traffic_out_bps"] = float(val) if val else 0
                        entry["traffic_out_fmt"] = fmt_bps(val)
                    except Exception:
                        pass
                elif "net.if.status" in key or "operational status" in name.lower():
                    entry["status"] = "up" if val in ("1", "up", "UP") else ("down" if val in ("2", "down", "DOWN") else "unknown")
                elif "net.if.speed" in key or "speed" in name.lower():
                    entry["speed"] = fmt_bps(val) if val else entry["speed"]
                elif "sensor.optical.rx_power" in key or "rx power" in name.lower() or "rx_power" in key:
                    try:
                        entry["optical"]["rx_power_dbm"] = round(float(val), 2)
                    except Exception:
                        pass
                elif "sensor.optical.tx_power" in key or "tx power" in name.lower() or "tx_power" in key:
                    try:
                        entry["optical"]["tx_power_dbm"] = round(float(val), 2)
                    except Exception:
                        pass
                elif "sensor.optical.temperature" in key or "temperature" in name.lower():
                    try:
                        entry["optical"]["temp_c"] = round(float(val), 1)
                    except Exception:
                        pass
                elif "sensor.optical.voltage" in key or "voltage" in name.lower():
                    try:
                        entry["optical"]["voltage_v"] = round(float(val), 2)
                    except Exception:
                        pass
                elif "sensor.optical.bias" in key or "bias" in name.lower():
                    try:
                        entry["optical"]["bias_ma"] = round(float(val), 2)
                    except Exception:
                        pass

            # 2. Interfaces Wireless / Radio (Cambium ePMP / Ubiquiti / Mimosa / Altai)
            elif any(k in key for k in ["cambium.radio", "cambium.cpe", "ubnt.radio", "mimosa.radio", "altai.radio"]):
                if "Wireless (RF)" not in interfaces_dict:
                    interfaces_dict["Wireless (RF)"] = {
                        "name": "Wireless (RF)",
                        "key": "wireless_rf",
                        "display_name": "📡 Enlace Inalámbrico (RF)",
                        "type": "wireless",
                        "status": "up",
                        "speed": "—",
                        "traffic_in_bps": 0,
                        "traffic_out_bps": 0,
                        "traffic_in_fmt": "—",
                        "traffic_out_fmt": "—",
                        "optical": {},
                        "wireless": {},
                        "item_ids": []
                    }

                w_entry = interfaces_dict["Wireless (RF)"]
                w_entry["item_ids"].append(it.get("itemid"))

                if "rssi" in key or "signal" in key:
                    try:
                        w_entry["wireless"]["rssi_dbm"] = round(float(val), 1)
                    except Exception:
                        pass
                elif "snr" in key:
                    try:
                        w_entry["wireless"]["snr_db"] = round(float(val), 1)
                    except Exception:
                        pass
                elif "freq" in key:
                    w_entry["wireless"]["freq_mhz"] = val
                elif "chwidth" in key or "bandwidth" in key:
                    w_entry["wireless"]["bandwidth"] = val
                elif "rx_mcs" in key:
                    w_entry["wireless"]["rx_mcs"] = val
                elif "tx_mcs" in key:
                    w_entry["wireless"]["tx_mcs"] = val
                elif "distance" in key:
                    w_entry["wireless"]["distance_km"] = val

            # 3. LAN Port de Antenas
            elif key in ("cambium.lan.speed", "net.if.status[LAN]", "net.if.in[LAN]", "net.if.out[LAN]", "net.if.status[eth0]", "net.if.in[eth0]", "net.if.out[eth0]"):
                lan_name = "eth0 / LAN"
                if lan_name not in interfaces_dict:
                    interfaces_dict[lan_name] = {
                        "name": lan_name,
                        "key": "eth0",
                        "display_name": f"🔌 {lan_name}",
                        "type": "ethernet",
                        "status": "up",
                        "speed": "1 Gbps",
                        "traffic_in_bps": 0,
                        "traffic_out_bps": 0,
                        "traffic_in_fmt": "—",
                        "traffic_out_fmt": "—",
                        "optical": {},
                        "wireless": {},
                        "item_ids": []
                    }
                lan_entry = interfaces_dict[lan_name]
                lan_entry["item_ids"].append(it.get("itemid"))
                if "in" in key:
                    try:
                        lan_entry["traffic_in_bps"] = float(val) if val else 0
                        lan_entry["traffic_in_fmt"] = fmt_bps(val)
                    except Exception:
                        pass
                elif "out" in key:
                    try:
                        lan_entry["traffic_out_bps"] = float(val) if val else 0
                        lan_entry["traffic_out_fmt"] = fmt_bps(val)
                    except Exception:
                        pass
                elif "speed" in key:
                    lan_entry["speed"] = val or "1 Gbps"

        result_list = list(interfaces_dict.values())
        result_list.sort(key=lambda x: (0 if x["type"] == "optical" else (1 if x["type"] == "ethernet" else 2), x["name"]))
        return result_list

    async def get_link_telemetry(self, link_id: str) -> Dict[str, Any]:
        """Obtiene la telemetría en tiempo real de una arista (enlace) combinando ambos extremos."""
        async with get_db_connection() as db:
            c = await db.execute("""
                SELECT l.*,
                       sn.name as src_name, sn.ip as src_ip, sn.device_type as src_type, sn.device_id as src_dev_id,
                       tn.name as tgt_name, tn.ip as tgt_ip, tn.device_type as tgt_type, tn.device_id as tgt_dev_id
                FROM links l
                LEFT JOIN nodes sn ON l.source_node_id = sn.id
                LEFT JOIN nodes tn ON l.target_node_id = tn.id
                WHERE l.id = ?
            """, (link_id,))
            row = await c.fetchone()
            if not row:
                raise Exception(f"Enlace {link_id} no encontrado")

        link = dict(row)
        src_node_id = link["source_node_id"]
        tgt_node_id = link["target_node_id"]

        extra = json.loads(link.get("extra_data") or "{}") if isinstance(link.get("extra_data"), str) else (link.get("extra_data") or {})

        # Si el origen es un submapa o portal, resolver el nodo interno vinculado
        actual_src_id = src_node_id
        if link.get("src_type") in ("submap", "parent_map") or not link.get("src_dev_id"):
            remote_src = extra.get("source_submap_node_id") or (extra.get("remote_node_id") if link.get("src_type") == "submap" else None)
            if remote_src:
                actual_src_id = remote_src

        # Si el destino es un submapa o portal, resolver el nodo interno vinculado (ej. CPE en Rucio)
        actual_tgt_id = tgt_node_id
        if link.get("tgt_type") in ("submap", "parent_map") or not link.get("tgt_dev_id"):
            remote_tgt = extra.get("target_submap_node_id") or extra.get("remote_node_id")
            if remote_tgt:
                actual_tgt_id = remote_tgt

        # Obtener interfaces de Zabbix para origen y destino
        src_ifaces = await self.get_node_zabbix_interfaces(actual_src_id)
        tgt_ifaces = await self.get_node_zabbix_interfaces(actual_tgt_id)

        # Encontrar telemetría coincidente para el puerto asignado
        src_match = None
        tgt_match = None

        target_src_name = (link.get("zabbix_src_interface") or link.get("source_interface") or "").strip().lower()
        target_tgt_name = (link.get("zabbix_tgt_interface") or link.get("target_interface") or "").strip().lower()

        if target_src_name:
            for iface in src_ifaces:
                if iface["name"].lower() == target_src_name or iface["key"].lower() == target_src_name or target_src_name in iface["name"].lower():
                    src_match = iface
                    break

        if target_tgt_name:
            for iface in tgt_ifaces:
                if iface["name"].lower() == target_tgt_name or iface["key"].lower() == target_tgt_name or target_tgt_name in iface["name"].lower():
                    tgt_match = iface
                    break

        # Si no hubo coincidencia explícita pero solo hay una interfaz disponible, asignarla
        if not src_match and len(src_ifaces) == 1:
            src_match = src_ifaces[0]
        if not tgt_match and len(tgt_ifaces) == 1:
            tgt_match = tgt_ifaces[0]

        # Determinar estado combinado del enlace
        is_down = (src_match and src_match.get("status") == "down") or (tgt_match and tgt_match.get("status") == "down")
        link_status = "down" if is_down else "ok"

        # Construir resumen para el tooltip
        parts = []
        traffic_in = src_match.get("traffic_in_fmt") if src_match else (tgt_match.get("traffic_in_fmt") if tgt_match else "—")
        traffic_out = src_match.get("traffic_out_fmt") if src_match else (tgt_match.get("traffic_out_fmt") if tgt_match else "—")

        if traffic_in != "—" or traffic_out != "—":
            parts.append(f"⬇ In: {traffic_in} | ⬆ Out: {traffic_out}")

        optical_info = (src_match.get("optical") if src_match else None) or (tgt_match.get("optical") if tgt_match else None)
        if optical_info and optical_info.get("rx_power_dbm") is not None:
            parts.append(f"📡 Rx: {optical_info.get('rx_power_dbm')} dBm")

        wireless_info = (src_match.get("wireless") if src_match else None) or (tgt_match.get("wireless") if tgt_match else None)
        if wireless_info and wireless_info.get("rssi_dbm") is not None:
            parts.append(f"📶 RSSI: {wireless_info.get('rssi_dbm')} dBm")

        cable_type_label = link.get("cable_type", "cat6").upper()
        summary_text = " · ".join(parts) if parts else f"Enlace {cable_type_label} (Operativo)"

        return {
            "link_id": link_id,
            "status": link_status,
            "cable": {
                "netbox_cable_id": link.get("netbox_cable_id"),
                "cable_type": link.get("cable_type", "cat6"),
                "cable_status": link.get("cable_status", "connected")
            },
            "source": {
                "node_id": src_node_id,
                "node_name": link.get("src_name"),
                "interface": link.get("source_interface"),
                "zabbix_interface": link.get("zabbix_src_interface"),
                "telemetry": src_match
            },
            "target": {
                "node_id": tgt_node_id,
                "node_name": link.get("tgt_name"),
                "interface": link.get("target_interface"),
                "zabbix_interface": link.get("zabbix_tgt_interface"),
                "telemetry": tgt_match
            },
            "summary": summary_text
        }

    async def get_map_links_telemetry(self, map_id: str) -> Dict[str, Any]:
        """Obtiene la telemetría en lote de todos los enlaces de un mapa."""
        async with get_db_connection() as db:
            c = await db.execute("SELECT id FROM links WHERE map_id = ?", (map_id,))
            rows = await c.fetchall()

        links_telemetry = {}
        for r in rows:
            try:
                lid = r["id"]
                links_telemetry[lid] = await self.get_link_telemetry(lid)
            except Exception as e:
                logger.warning(f"Error procesando telemetría para enlace {r['id']}: {e}")

        return {
            "map_id": map_id,
            "count": len(links_telemetry),
            "links": links_telemetry
        }

    async def get_olt_gpon_ports(self, olt_ip_or_name: str) -> List[Dict[str, Any]]:
        """
        Obtiene la lista de puertos GPON monitoreados para una OLT (ej. GPON 0/1/0 a GPON 0/1/15)
        con su ifIndex, estado operativo (Up/Down), tráfico y cantidad de ONUs.
        """
        await self.refresh_zabbix_hosts_cache()
        h = self.find_zabbix_host(olt_ip_or_name, olt_ip_or_name)
        if not h:
            return []

        host_id = h.get("hostid")
        items = await self._call_api("item.get", {
            "hostids": host_id,
            "output": ["itemid", "name", "key_", "lastvalue", "units", "status", "value_type"]
        })
        if not items:
            return []

        # Indexar items por SNMPINDEX / ifIndex
        ports_map: Dict[str, Dict[str, Any]] = {}
        for it in items:
            key = it.get("key_", "")
            name = it.get("name_", it.get("name", ""))
            val = it.get("lastvalue", "")

            # Detectar puertos GPON por nombre o key
            if "GPON" in name or "gpon" in key or "gpon." in key:
                import re
                idx_match = re.search(r'\[(\d+)\]', key) or re.search(r'\[(\d+)\]', name)
                snmp_idx = idx_match.group(1) if idx_match else None

                name_match = re.search(r'GPON\s*(\d+/\d+/\d+)', name, re.IGNORECASE) or re.search(r'GPON\s*\[?(\d+/\d+/\d+)\]?', name, re.IGNORECASE)
                port_label = f"GPON {name_match.group(1)}" if name_match else (f"GPON [{snmp_idx}]" if snmp_idx else name)

                if snmp_idx:
                    if snmp_idx not in ports_map:
                        ports_map[snmp_idx] = {
                            "index": snmp_idx,
                            "name": port_label,
                            "display_name": port_label,
                            "status": "up",
                            "status_code": 1,
                            "traffic_in_bps": 0,
                            "traffic_out_bps": 0,
                            "traffic_in_fmt": "—",
                            "traffic_out_fmt": "—",
                            "onus_online": 0,
                            "tx_power_dbm": None
                        }
                    
                    p = ports_map[snmp_idx]
                    if name_match:
                        p["name"] = f"GPON {name_match.group(1)}"
                        p["display_name"] = f"GPON {name_match.group(1)}"

                    if "net.if.status" in key:
                        p["status_code"] = int(val) if str(val).isdigit() else 1
                        p["status"] = "down" if p["status_code"] == 2 else "up"
                    elif "net.if.in" in key:
                        try:
                            p["traffic_in_bps"] = float(val)
                            p["traffic_in_fmt"] = fmt_bps(val)
                        except Exception:
                            pass
                    elif "net.if.out" in key:
                        try:
                            p["traffic_out_bps"] = float(val)
                            p["traffic_out_fmt"] = fmt_bps(val)
                        except Exception:
                            pass
                    elif "gpon.ont.online" in key:
                        try:
                            p["onus_online"] = int(float(val))
                        except Exception:
                            pass
                    elif "gpon.optical.txpower" in key:
                        try:
                            p["tx_power_dbm"] = round(float(val), 2)
                        except Exception:
                            pass

        result = list(ports_map.values())
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s["name"])]
        result.sort(key=natural_sort_key)
        return result


    async def get_gpon_branch_telemetry(self, node_id: str) -> Dict[str, Any]:
        """
        Calcula y obtiene la telemetría en tiempo real de un brazo FTTH / Ramal GPON:
        - Estado del puerto GPON (Up / Down)
        - Tráfico en tiempo real (In / Out) y volumen acumulado
        - Niveles de señal óptica de las ONUs conectadas a ese puerto GPON
        - Conteo y promedio de clientes Típicos (> -27 dBm) y Atípicos (<= -27 dBm)
        """
        async with get_db_connection() as db:
            c = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            node = await c.fetchone()

        if not node:
            raise ValueError(f"Nodo {node_id} no encontrado")

        node = dict(node)
        extra = {}
        if node.get("extra_data"):
            try:
                extra = json.loads(node["extra_data"]) if isinstance(node["extra_data"], str) else node["extra_data"]
            except Exception:
                extra = {}

        olt_ip = extra.get("olt_ip") or node.get("ip") or ""
        olt_name = extra.get("olt_name") or extra.get("olt_node_name") or ""
        gpon_port = extra.get("gpon_port") or extra.get("interface_name") or "GPON 0/1/0"
        gpon_index = str(extra.get("gpon_index") or extra.get("if_index") or "")
        typical_threshold = float(extra.get("typical_threshold_dbm", -27.0))

        # Si no tenemos olt_ip, intentar buscar nodo OLT en el mismo mapa
        if not olt_ip and extra.get("olt_node_id"):
            async with get_db_connection() as db:
                c_olt = await db.execute("SELECT ip, name FROM nodes WHERE id = ?", (extra["olt_node_id"],))
                olt_row = await c_olt.fetchone()
                if olt_row:
                    olt_ip = olt_row["ip"] or olt_ip
                    olt_name = olt_row["name"] or olt_name

        # Fallback Huizache
        if not olt_ip and "huizache" in node.get("name", "").lower():
            olt_ip = "10.20.0.2"
            olt_name = "OLT_HUAWEI"

        # Buscar host en Zabbix
        h = self.find_zabbix_host(olt_name or olt_ip, olt_ip or olt_name)
        host_id = h.get("hostid") if h else None

        port_status = "up"
        port_status_code = 1
        traffic_in_bps = 0.0
        traffic_out_bps = 0.0
        volume_in_bytes = 0.0
        volume_out_bytes = 0.0
        tx_power_dbm = None
        onus_online = 0
        onus_registered = 0

        # Si tenemos host_id en Zabbix, consultar items
        if host_id:
            try:
                items = await self._call_api("item.get", {
                    "hostids": host_id,
                    "output": ["itemid", "name", "key_", "lastvalue", "units", "status", "value_type"]
                })
                if items:
                    for it in items:
                        key = it.get("key_", "")
                        name = it.get("name_", it.get("name", ""))
                        val = it.get("lastvalue", "")

                        # Coincidencia con este puerto GPON
                        is_match = False
                        if gpon_index and f"[{gpon_index}]" in key:
                            is_match = True
                        elif gpon_port and gpon_port.lower() in name.lower():
                            is_match = True
                            if not gpon_index and "[" in key:
                                import re
                                m = re.search(r'\[(\d+)\]', key)
                                if m:
                                    gpon_index = m.group(1)

                        if is_match:
                            if "net.if.status" in key:
                                port_status_code = int(val) if str(val).isdigit() else 1
                                port_status = "down" if port_status_code == 2 else "up"
                            elif "net.if.in[" in key:
                                try:
                                    traffic_in_bps = float(val)
                                except Exception:
                                    pass
                            elif "net.if.out[" in key:
                                try:
                                    traffic_out_bps = float(val)
                                except Exception:
                                    pass
                            elif "net.if.in_bytes[" in key:
                                try:
                                    volume_in_bytes = float(val)
                                except Exception:
                                    pass
                            elif "net.if.out_bytes[" in key:
                                try:
                                    volume_out_bytes = float(val)
                                except Exception:
                                    pass
                            elif "gpon.ont.online[" in key:
                                try:
                                    onus_online = int(float(val))
                                except Exception:
                                    pass
                            elif "gpon.ont.registered[" in key:
                                try:
                                    onus_registered = int(float(val))
                                except Exception:
                                    pass
                            elif "gpon.optical.txpower[" in key:
                                try:
                                    tx_power_dbm = round(float(val), 2)
                                except Exception:
                                    pass
            except Exception as zbx_err:
                logger.warning(f"Error consultando items GPON en Zabbix: {zbx_err}")

        # ── Consulta de Niveles de Señal Óptica Rx de ONUs para este puerto GPON ──
        typical_onus: List[float] = []
        atypical_onus: List[float] = []
        offline_onus: List[Dict[str, Any]] = []
        onus_details: List[Dict[str, Any]] = []

        if olt_ip and gpon_index:
            try:
                import asyncio
                import re

                # Obtener la comunidad SNMP del host en Zabbix si existe, o usar comunidades conocidas
                community = await self.get_olt_community(olt_ip)

                # 1. Consultar descripciones / nombres de las ONUs
                descs: Dict[str, str] = {}
                try:
                    proc_desc = await asyncio.create_subprocess_exec(
                        "snmpbulkwalk", "-v2c", "-c", community, "-Cr10", "-t", "3", "-r", "2",
                        olt_ip, f"1.3.6.1.4.1.2011.6.128.1.1.2.43.1.9.{gpon_index}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout_d, _ = await asyncio.wait_for(proc_desc.communicate(), timeout=6.0)
                    for line in stdout_d.decode("latin-1", errors="ignore").splitlines():
                        m = re.search(r'\.' + str(gpon_index) + r'\.(\d+)\s+=\s+(?:STRING|Hex-STRING):\s+\"?([^\"]*)\"?', line)
                        if m:
                            ont_id_str = m.group(1)
                            raw_desc = m.group(2).strip()
                            if " " in raw_desc and all(len(c) == 2 and all(ch in "0123456789abcdefABCDEF" for ch in c) for c in raw_desc.split()):
                                try:
                                    raw_desc = bytes.fromhex(raw_desc.replace(" ", "")).decode("latin-1", errors="ignore").strip()
                                except Exception:
                                    pass
                            descs[ont_id_str] = raw_desc
                except Exception as d_err:
                    logger.debug(f"No se pudieron obtener descripciones de ONUs: {d_err}")

                # 2. Consultar causas de última caída
                causes: Dict[str, int] = {}
                try:
                    proc_cause = await asyncio.create_subprocess_exec(
                        "snmpbulkwalk", "-v2c", "-c", community, "-Cr10", "-t", "3", "-r", "2",
                        olt_ip, f"1.3.6.1.4.1.2011.6.128.1.1.2.46.1.15.{gpon_index}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout_c, _ = await asyncio.wait_for(proc_cause.communicate(), timeout=6.0)
                    for line in stdout_c.decode("utf-8", errors="ignore").splitlines():
                        m = re.search(r'\.' + str(gpon_index) + r'\.(\d+)\s+=\s+INTEGER:\s+(\d+)', line)
                        if m:
                            causes[m.group(1)] = int(m.group(2))
                except Exception:
                    pass

                # 3. Consultar potencias ópticas Rx de las ONUs
                proc_opt = await asyncio.create_subprocess_exec(
                    "snmpbulkwalk", "-v2c", "-c", community, "-Cr10", "-t", "3", "-r", "2",
                    olt_ip, f"1.3.6.1.4.1.2011.6.128.1.1.2.51.1.4.{gpon_index}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout_o, _ = await asyncio.wait_for(proc_opt.communicate(), timeout=6.0)
                out_lines = stdout_o.decode("utf-8", errors="ignore").strip().splitlines()

                for line in out_lines:
                    m = re.search(r'\.' + str(gpon_index) + r'\.(\d+)\s+=\s+INTEGER:\s+(-?\d+)', line)
                    if m:
                        ont_id = m.group(1)
                        raw_val = int(m.group(2))
                        desc = descs.get(ont_id, f"ONT {ont_id}")

                        if raw_val in (2147483647, 0, -2147483648):
                            cause_code = causes.get(ont_id, 0)
                            cause_label = "Dying-Gasp (Corte de Energía)" if cause_code == 1 else ("LOSi (Fibra Cortada)" if cause_code == 2 else "Offline / Inactivo")
                            offline_onus.append({
                                "ont_id": ont_id,
                                "description": desc,
                                "status": "offline",
                                "down_cause": cause_label,
                                "down_cause_code": cause_code
                            })
                        else:
                            rx_dbm = round(raw_val * 0.01, 2)
                            if -50.0 <= rx_dbm <= 0.0:
                                is_typical = rx_dbm > typical_threshold
                                if is_typical:
                                    typical_onus.append(rx_dbm)
                                else:
                                    atypical_onus.append(rx_dbm)

                                onus_details.append({
                                    "ont_id": ont_id,
                                    "description": desc,
                                    "rx_power_dbm": rx_dbm,
                                    "is_typical": is_typical,
                                    "quality": "optimal" if rx_dbm > -24.0 else ("acceptable" if is_typical else "critical")
                                })
            except Exception as snmp_err:
                logger.warning(f"SNMP bulkwalk ONUs no completado para {olt_ip}:{gpon_index} ({snmp_err})")

        # Cálculos de promedios
        typical_count = len(typical_onus)
        atypical_count = len(atypical_onus)
        offline_count = len(offline_onus)
        total_measured = typical_count + atypical_count

        typical_avg = round(sum(typical_onus) / typical_count, 2) if typical_count > 0 else None
        atypical_avg = round(sum(atypical_onus) / atypical_count, 2) if atypical_count > 0 else None
        overall_avg = round((sum(typical_onus) + sum(atypical_onus)) / total_measured, 2) if total_measured > 0 else None

        # Si no hubo lectura SNMP en vivo de ONUs, usar onus_online de Zabbix como aproximación
        if total_measured == 0 and onus_online > 0:
            typical_count = onus_online
            atypical_count = 0
            typical_avg = -22.5

        # Diagnóstico general del brazo
        branch_status = "ok"
        status_label = "Operativo (Link Up)"
        if port_status == "down" or port_status_code == 2:
            branch_status = "down"
            status_label = "Puerto Caído (Link Down)"
        elif atypical_count > 0:
            branch_status = "warning"
            status_label = f"Alerta Óptica ({atypical_count} cliente{'s' if atypical_count > 1 else ''} atípico{'s' if atypical_count > 1 else ''} <= {typical_threshold} dBm)"

        total_vol_bytes = volume_in_bytes + volume_out_bytes
        vol_fmt = fmt_bytes(total_vol_bytes) if total_vol_bytes > 0 else "—"

        return {
            "node_id": node_id,
            "branch_name": node.get("name", "Brazo FTTH"),
            "olt_name": olt_name or "OLT Huawei",
            "olt_ip": olt_ip or "10.20.0.2",
            "gpon_port": gpon_port,
            "gpon_index": gpon_index,
            "status": branch_status,
            "status_label": status_label,
            "port_status": port_status,
            "port_status_code": port_status_code,
            "traffic_in_bps": traffic_in_bps,
            "traffic_out_bps": traffic_out_bps,
            "traffic_in_fmt": fmt_bps(traffic_in_bps),
            "traffic_out_fmt": fmt_bps(traffic_out_bps),
            "volume_in_bytes": volume_in_bytes,
            "volume_out_bytes": volume_out_bytes,
            "volume_total_fmt": vol_fmt,
            "tx_power_dbm": tx_power_dbm,
            "onus_online": onus_online or total_measured,
            "onus_registered": onus_registered or total_measured,
            "typical_count": typical_count,
            "typical_avg_dbm": typical_avg,
            "atypical_count": atypical_count,
            "atypical_avg_dbm": atypical_avg,
            "offline_count": offline_count,
            "offline_onus": offline_onus,
            "overall_avg_dbm": overall_avg,
            "typical_threshold_dbm": typical_threshold,
            "onus_details": onus_details,
            "onus_sample": onus_details[:20],
            "timestamp": time.time()
        }

    async def _snmp_walk(self, ip: str, community: str, oid: str) -> str:
        """Ejecuta snmpbulkwalk asíncrono con timeout y parámetros optimizados para OLT."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "snmpbulkwalk", "-v2c", "-c", community, "-Cr10", "-t", "3", "-r", "2",
                ip, oid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            return stdout.decode("latin-1", errors="ignore")
        except Exception as e:
            logger.debug(f"SNMP walk error en {ip}:{oid} -> {e}")
            return ""

    async def get_olt_community(self, olt_ip: str, explicit_comm: Optional[str] = None) -> str:
        """
        Resuelve la comunidad SNMP para una OLT según el orden de prioridad:
        1. Comunidad explícita enviada desde la UI / API
        2. Credencial guardada en la base de datos local (olt_snmp_credentials)
        3. Comunidad registrada en la interfaz de Zabbix para ese host
        4. Fallback por defecto ('Muci!6508_rd')
        """
        if explicit_comm and explicit_comm.strip():
            return explicit_comm.strip()

        # 1. Buscar en BD local de credenciales verificadas
        try:
            from app.database import get_db_connection
            async with get_db_connection() as db:
                c = await db.execute("SELECT community FROM olt_snmp_credentials WHERE olt_ip = ?", (olt_ip,))
                row = await c.fetchone()
                if row and row["community"]:
                    return row["community"].strip()
        except Exception as e:
            logger.debug(f"Error consultando olt_snmp_credentials para {olt_ip}: {e}")

        # 2. Buscar en caché de Zabbix
        h = self.find_zabbix_host(olt_ip, olt_ip)
        if h and h.get("interfaces"):
            for iface in h["interfaces"]:
                c_str = iface.get("details", {}).get("community")
                if c_str and c_str.strip() and not c_str.startswith("{$"):
                    return c_str.strip()

        return "Muci!6508_rd"

    async def save_olt_community(self, olt_ip: str, community: str, vendor: str = "Huawei", notes: str = "") -> None:
        """Guarda o actualiza la comunidad SNMP para una OLT en la BD local."""
        try:
            from app.database import get_db_connection
            async with get_db_connection() as db:
                await db.execute("""
                    INSERT INTO olt_snmp_credentials (olt_ip, community, vendor, notes, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(olt_ip) DO UPDATE SET
                        community = excluded.community,
                        vendor = excluded.vendor,
                        notes = excluded.notes,
                        updated_at = CURRENT_TIMESTAMP
                """, (olt_ip, community.strip(), vendor, notes))
                await db.commit()
            logger.info(f"Comunidad SNMP para OLT {olt_ip} guardada exitosamente: '{community}'")
        except Exception as e:
            logger.error(f"Error guardando comunidad SNMP para {olt_ip}: {e}")
            raise

    async def test_olt_community(self, olt_ip: str, community: str) -> Tuple[bool, str]:
        """Prueba una comunidad SNMP contra una OLT consultando sysName/sysDescr."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "snmpget", "-v2c", "-c", community.strip(), "-t", "2", "-r", "2",
                olt_ip, "1.3.6.1.2.1.1.5.0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=6.0)
            if proc.returncode == 0:
                out = stdout.decode("latin-1", errors="ignore").strip()
                if "=" in out:
                    out = out.split("=", 1)[1].strip().strip('"')
                return True, out or "OK"
            else:
                err = stderr.decode("latin-1", errors="ignore").strip()
                return False, err or "Timeout o comunidad incorrecta"
        except asyncio.TimeoutError:
            return False, "Timeout: El equipo no respondió en 3.5 segundos"
        except Exception as e:
            return False, str(e)


    async def get_olt_diagnostic_summary(self) -> Dict[str, Any]:
        """
        Descubre y consolida el estado de todas las OLTs registradas en Zabbix.
        Retorna la lista de OLTs con su IP, fabricante, estado y total de puertos GPON.
        """
        await self.refresh_zabbix_hosts_cache()
        olts: List[Dict[str, Any]] = []
        seen_hosts = set()

        for ip, h in self._host_cache_by_ip.items():
            hid = h.get("hostid")
            if not hid or hid in seen_hosts:
                continue

            h_name = h.get("name", "")
            h_host = h.get("host", "")
            name_lower = f"{h_name} {h_host}".lower()

            is_olt = any(w in name_lower for w in ["olt", "ea58", "ma58", "vsol", "v-sol", "v1600"])
            if not is_olt:
                for t in h.get("tags", []):
                    if "olt" in t.get("tag", "").lower() or "olt" in t.get("value", "").lower():
                        is_olt = True
                        break

            if is_olt:
                seen_hosts.add(hid)
                comm = await self.get_olt_community(ip)

                vendor = "Huawei" if any(x in name_lower for x in ["huawei", "ea58", "ma58"]) else ("V-SOL" if any(x in name_lower for x in ["vsol", "v-sol", "v1600"]) else "Generic OLT")

                olts.append({
                    "id": hid,
                    "name": h_name,
                    "host": h_host,
                    "ip": ip,
                    "status": "online" if h.get("status") == "0" else "offline",
                    "vendor": vendor,
                    "community": comm
                })

        olts.sort(key=lambda x: x["name"])

        from app.services.iwisp_service import iwisp_service
        cache_status = await iwisp_service.get_cache_status()

        return {
            "total_olts": len(olts),
            "olts": olts,
            "iwisp_cache": cache_status,
            "timestamp": time.time()
        }

    async def get_olt_port_onts_detailed(
        self,
        olt_ip_or_name: str,
        port_index: str,
        typical_threshold: float = -27.0,
        community: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Obtiene el diagnóstico detallado de las ONTs de un puerto GPON específico,
        cruzando en tiempo real con la base de datos de i-WISP por Serial Number de ONT.
        Permite comunidad SNMP explícita o resolución inteligente por base de datos / fallback.
        """
        await self.refresh_zabbix_hosts_cache()
        h = self.find_zabbix_host(olt_ip_or_name, olt_ip_or_name)
        olt_ip = olt_ip_or_name
        olt_name = olt_ip_or_name

        if h:
            olt_name = h.get("name", olt_ip_or_name)
            if h.get("interfaces"):
                for iface in h["interfaces"]:
                    if iface.get("ip"):
                        olt_ip = iface["ip"]
                        break

        resolved_comm = await self.get_olt_community(olt_ip, community)

        from app.services.iwisp_service import iwisp_service, normalize_onu_serial

        # Ejecutar snmpbulkwalk de forma secuencial para evitar descarte de paquetes UDP en la OLT
        desc_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.43.1.9.{port_index}")
        serial_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.43.1.3.{port_index}")
        rx_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.51.1.4.{port_index}")
        cause_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.46.1.15.{port_index}")

        # Si todo vino vacío y el usuario NO forzó una comunidad explícita, probar lista de candidatos
        if not desc_raw and not serial_raw and not rx_raw and not cause_raw and not community:
            candidates = ["Muci!6508_rd", "Mucivaga6508_rd", "admin6508", "public"]
            for cand in candidates:
                if cand == resolved_comm:
                    continue
                ok, sysname = await self.test_olt_community(olt_ip, cand)
                if ok:
                    logger.info(f"Fallback exitoso para OLT {olt_ip} con comunidad '{cand}' ({sysname}). Guardando en BD...")
                    resolved_comm = cand
                    await self.save_olt_community(olt_ip, cand, notes=f"Detectado automáticamente ({sysname})")
                    desc_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.43.1.9.{port_index}")
                    serial_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.43.1.3.{port_index}")
                    rx_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.51.1.4.{port_index}")
                    cause_raw = await self._snmp_walk(olt_ip, resolved_comm, f"1.3.6.1.4.1.2011.6.128.1.1.2.46.1.15.{port_index}")
                    break


        def parse_snmp_output(text: str) -> Dict[str, Tuple[str, str]]:
            items: Dict[str, Tuple[str, str]] = {}
            cur_id = None
            cur_type = ''
            cur_lines = []
            if not isinstance(text, str):
                return items
            for line in text.splitlines():
                if " = " in line:
                    if cur_id:
                        items[cur_id] = (cur_type, " ".join(cur_lines).strip())
                    left, right = line.split(" = ", 1)
                    m = re.search(r'\.' + str(port_index) + r'\.(\d+)', left)
                    if m:
                        cur_id = m.group(1)
                        if ":" in right:
                            cur_type, val = right.split(":", 1)
                            cur_lines = [val.strip()]
                        else:
                            cur_type = ""
                            cur_lines = [right.strip()]
                    else:
                        cur_id = None
                elif cur_id:
                    cur_lines.append(line.strip())
            if cur_id:
                items[cur_id] = (cur_type, " ".join(cur_lines).strip())
            return items

        descs_raw_map = parse_snmp_output(desc_raw)
        descs: Dict[str, str] = {}
        for ont_id, (val_type, val_str) in descs_raw_map.items():
            if val_type.strip() == "Hex-STRING":
                hex_clean = re.sub(r'[^0-9a-fA-F]', '', val_str)
                try:
                    descs[ont_id] = bytes.fromhex(hex_clean).decode("latin-1", errors="ignore").strip()
                except Exception:
                    descs[ont_id] = val_str
            else:
                descs[ont_id] = val_str.strip('"').strip("'").strip()

        serials_raw_map = parse_snmp_output(serial_raw)
        serials: Dict[str, str] = {}
        for ont_id, (_, val_str) in serials_raw_map.items():
            serials[ont_id] = normalize_onu_serial(val_str)

        causes_raw_map = parse_snmp_output(cause_raw)
        causes: Dict[str, int] = {}
        for ont_id, (_, val_str) in causes_raw_map.items():
            if val_str.isdigit():
                causes[ont_id] = int(val_str)

        rx_raw_map = parse_snmp_output(rx_raw)
        rx_powers: Dict[str, float] = {}
        for ont_id, (_, val_str) in rx_raw_map.items():
            try:
                raw_p = int(val_str)
                if raw_p not in (2147483647, 0, -2147483648):
                    p_val = round(raw_p * 0.01, 2)
                    if -50.0 <= p_val <= 0.0:
                        rx_powers[ont_id] = p_val
            except Exception:
                pass



        all_ont_ids = sorted(list(set(list(descs.keys()) + list(serials.keys()) + list(rx_powers.keys()) + list(causes.keys()))), key=lambda x: int(x) if x.isdigit() else 9999)

        onts_list = []
        typical_count = 0
        atypical_count = 0
        dying_gasp_count = 0
        losi_count = 0
        offline_count = 0

        for ont_id in all_ont_ids:
            serial = serials.get(ont_id, "")
            raw_desc = descs.get(ont_id, f"ONT {ont_id}")
            cause_code = causes.get(ont_id, 0)
            rx_dbm = rx_powers.get(ont_id)

            iwisp_info = await iwisp_service.get_client_by_serial(serial) if serial else None

            client_id = ""
            client_name = ""
            package_name = ""
            package_cost = ""
            is_iwisp_matched = False

            if iwisp_info:
                client_id = iwisp_info.get("client_id", "")
                client_name = iwisp_info.get("client_name", "")
                package_name = iwisp_info.get("plan_name", "")
                package_cost = iwisp_info.get("plan_cost", "")
                is_iwisp_matched = True
            else:
                m_desc = re.match(r'^\s*(\d+)\s*[-_:]\s*(.+)$', raw_desc)
                if m_desc:
                    client_id = m_desc.group(1)
                    client_name = m_desc.group(2).strip()
                else:
                    client_name = raw_desc

            is_online = rx_dbm is not None
            if is_online:
                if rx_dbm > typical_threshold:
                    typical_count += 1
                    quality = "optimal" if rx_dbm > -24.0 else "acceptable"
                else:
                    atypical_count += 1
                    quality = "critical"
                status_label = "Online"
                status_color = "#10b981"
            else:
                offline_count += 1
                quality = "offline"
                if cause_code == 1:
                    dying_gasp_count += 1
                    status_label = "⚡ Sin Luz (Dying-Gasp)"
                    status_color = "#f59e0b"
                elif cause_code == 2:
                    losi_count += 1
                    status_label = "✂️ Corte Fibra (LOSi)"
                    status_color = "#ef4444"
                else:
                    status_label = "Offline / Inactivo"
                    status_color = "#94a3b8"

            onts_list.append({
                "ont_id": ont_id,
                "serial": serial,
                "client_id": client_id,
                "client_name": client_name,
                "package_name": package_name,
                "package_cost": package_cost,
                "is_iwisp_matched": is_iwisp_matched,
                "raw_description": raw_desc,
                "rx_power_dbm": rx_dbm,
                "rx_power_fmt": f"{rx_dbm} dBm" if rx_dbm is not None else "—",
                "is_online": is_online,
                "quality": quality,
                "down_cause_code": cause_code,
                "status_label": status_label,
                "status_color": status_color
            })

        return {
            "olt_name": olt_name,
            "olt_ip": olt_ip,
            "port_index": port_index,
            "community_used": resolved_comm,
            "total_onts": len(onts_list),
            "online_count": typical_count + atypical_count,
            "typical_count": typical_count,
            "atypical_count": atypical_count,
            "dying_gasp_count": dying_gasp_count,
            "losi_count": losi_count,
            "offline_count": offline_count,
            "onts": onts_list,
            "timestamp": time.time()
        }

def fmt_bps(bps_val: Any) -> str:

    """Formatea bits por segundo (bps) a Kbps, Mbps, Gbps legibles."""
    if not bps_val:
        return "—"
    try:
        v = float(bps_val)
        if v <= 0:
            return "0 bps"
        units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
        i = 0
        while v >= 1000.0 and i < len(units) - 1:
            v /= 1000.0
            i += 1
        return f"{v:.2f} {units[i]}"
    except Exception:
        return "—"

def fmt_bytes(bytes_val: float) -> str:
    """Formatea bytes a KB, MB, GB, TB legibles."""
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    v = float(bytes_val)
    while v >= 1024.0 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    return f"{v:.2f} {units[i]}"

zabbix_service = ZabbixService()


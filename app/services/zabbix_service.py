import httpx
import logging
import asyncio
import time
import json
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
            "selectInterfaces": ["ip", "dns", "main", "type"],
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
        for r in node_rows:
            nd = dict(r)
            if nd.get("device_type") == "submap":
                submap_nodes.append(nd)
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
                            "snmp_available": int(snmp_iface.get("available", 0)) if snmp_iface else 0,
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

        return {
            "node_id": node_id,
            "name": n_dict["name"],
            "ip": n_dict.get("ip") or "",
            "status": status,
            "ping_status": ping_status,
            "snmp_status": snmp_status,
            "is_online": (status != "down"),
            "icmp_ping": int(icmp_val) if icmp_val in ('0', '1') else None,
            "snmp_available": int(snmp_iface.get("available", 0)) if snmp_iface else 0,
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

zabbix_service = ZabbixService()


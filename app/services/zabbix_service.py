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

        for nid, n in nodes_dict.items():
            uplinks = in_degree[nid]
            downlinks = out_degree[nid]
            matched_host = self.find_zabbix_host(n["name"], n.get("ip"))

            if len(uplinks) == 0:
                relation_type = "root"
                root_nodes_count += 1
            else:
                relation_type = "direct"
                direct_relations_count += len(uplinks)

            node_analysis.append({
                "node_id": nid,
                "name": n["name"],
                "ip": n.get("ip"),
                "device_type": n.get("device_type"),
                "rank": get_role_rank(n.get("device_type")),
                "relation_type": relation_type,
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
        Sincroniza la arquitectura de NexusDude a Zabbix Services (BSM):
        - scope="global": Sincroniza todos los mapas y nodos del sistema.
        - scope="branch": Sincroniza únicamente el mapa especificado (map_id) y todos sus submapas descendientes.
        """
        await self.login()
        await self.refresh_zabbix_hosts_cache(force=True)

        async with get_db_connection() as db:
            c_maps = await db.execute("SELECT * FROM maps ORDER BY created_at ASC")
            all_maps_rows = [dict(r) for r in await c_maps.fetchall()]

        if not all_maps_rows:
            return {"error": "No hay mapas configurados", "maps_synced": 0}

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

        target_map_ids_set = {m["id"] for m in target_maps}

        # Mapa ID -> Zabbix Service ID
        map_service_ids: Dict[str, str] = {}
        # Node ID -> Zabbix Service ID
        node_service_ids: Dict[str, str] = {}

        report = {
            "scope": scope,
            "root_map_id": map_id if scope == "branch" else None,
            "maps_synced": 0,
            "submaps_synced": 0,
            "nodes_synced": 0,
            "nodes_matched_zabbix": 0,
            "direct_dependencies_created": 0,
            "details": []
        }

        # ─── Paso 1: Crear Servicios para Mapas Raíz y Submapas ─────────────
        if scope == "branch" and target_maps:
            root_map = target_maps[0]
            root_parent_map_id = root_map.get("parent_map_id")
            external_parent_service_id = None

            if root_parent_map_id:
                try:
                    existing_services = await self._call_api("service.get", {
                        "output": ["serviceid", "name"],
                        "tags": [
                            {"tag": "managed_by", "value": "nexusdude"},
                            {"tag": "map_id", "value": root_parent_map_id}
                        ]
                    })
                    if existing_services:
                        for es in existing_services:
                            if es.get("name", "").startswith("[MAP]") or es.get("name", "").startswith("[SUBMAP]"):
                                external_parent_service_id = es["serviceid"]
                                break
                        if not external_parent_service_id:
                            external_parent_service_id = existing_services[0]["serviceid"]
                except Exception as e:
                    logger.warning(f"No se pudo consultar servicio del mapa padre {root_parent_map_id}: {e}")

            is_sub = bool(root_parent_map_id)
            p_list = [{"serviceid": external_parent_service_id}] if external_parent_service_id else []
            tag_type = "submap" if is_sub else "map"
            prefix = "[SUBMAP]" if is_sub else "[MAP]"

            s_res = await self._call_api("service.create", {
                "name": f"{prefix} {root_map['name']}",
                "algorithm": 2,
                "sortorder": 0,
                "parents": p_list,
                "tags": [
                    {"tag": "managed_by", "value": "nexusdude"},
                    {"tag": "nexus_type", "value": tag_type},
                    {"tag": "map_id", "value": root_map["id"]},
                    {"tag": "parent_map_id", "value": root_parent_map_id or ""}
                ]
            })
            map_service_ids[root_map["id"]] = s_res["serviceids"][0]
            if is_sub:
                report["submaps_synced"] += 1
            else:
                report["maps_synced"] += 1

            remaining_maps = [m for m in target_maps if m["id"] != root_map["id"]]
            while remaining_maps:
                progress = False
                next_remaining = []
                for sm in remaining_maps:
                    p_mid = sm.get("parent_map_id")
                    if p_mid in map_service_ids:
                        parent_service_id = map_service_ids[p_mid]
                        s_res = await self._call_api("service.create", {
                            "name": f"[SUBMAP] {sm['name']}",
                            "algorithm": 2,
                            "sortorder": 0,
                            "parents": [{"serviceid": parent_service_id}],
                            "tags": [
                                {"tag": "managed_by", "value": "nexusdude"},
                                {"tag": "nexus_type", "value": "submap"},
                                {"tag": "map_id", "value": sm["id"]},
                                {"tag": "parent_map_id", "value": p_mid or ""}
                            ]
                        })
                        map_service_ids[sm["id"]] = s_res["serviceids"][0]
                        report["submaps_synced"] += 1
                        progress = True
                    else:
                        next_remaining.append(sm)
                if not progress:
                    for sm in next_remaining:
                        s_res = await self._call_api("service.create", {
                            "name": f"[SUBMAP] {sm['name']}",
                            "algorithm": 2,
                            "sortorder": 0,
                            "tags": [
                                {"tag": "managed_by", "value": "nexusdude"},
                                {"tag": "nexus_type", "value": "submap"},
                                {"tag": "map_id", "value": sm["id"]}
                            ]
                        })
                        map_service_ids[sm["id"]] = s_res["serviceids"][0]
                        report["submaps_synced"] += 1
                    break
                remaining_maps = next_remaining

        else:
            root_maps = [m for m in target_maps if not m.get("parent_map_id") or m.get("parent_map_id") not in target_map_ids_set]
            sub_maps = [m for m in target_maps if m.get("parent_map_id") and m.get("parent_map_id") in target_map_ids_set]

            for m in root_maps:
                s_res = await self._call_api("service.create", {
                    "name": f"[MAP] {m['name']}",
                    "algorithm": 2,
                    "sortorder": 0,
                    "tags": [
                        {"tag": "managed_by", "value": "nexusdude"},
                        {"tag": "nexus_type", "value": "map"},
                        {"tag": "map_id", "value": m["id"]}
                    ]
                })
                s_id = s_res["serviceids"][0]
                map_service_ids[m["id"]] = s_id
                report["maps_synced"] += 1

            remaining_submaps = list(sub_maps)
            while remaining_submaps:
                progress = False
                next_rem = []
                for sm in remaining_submaps:
                    p_mid = sm.get("parent_map_id")
                    if p_mid in map_service_ids:
                        parent_service_id = map_service_ids[p_mid]
                        s_res = await self._call_api("service.create", {
                            "name": f"[SUBMAP] {sm['name']}",
                            "algorithm": 2,
                            "sortorder": 0,
                            "parents": [{"serviceid": parent_service_id}],
                            "tags": [
                                {"tag": "managed_by", "value": "nexusdude"},
                                {"tag": "nexus_type", "value": "submap"},
                                {"tag": "map_id", "value": sm["id"]},
                                {"tag": "parent_map_id", "value": p_mid or ""}
                            ]
                        })
                        map_service_ids[sm["id"]] = s_res["serviceids"][0]
                        report["submaps_synced"] += 1
                        progress = True
                    else:
                        next_rem.append(sm)
                if not progress:
                    for sm in next_rem:
                        s_res = await self._call_api("service.create", {
                            "name": f"[SUBMAP] {sm['name']}",
                            "algorithm": 2,
                            "sortorder": 0,
                            "tags": [
                                {"tag": "managed_by", "value": "nexusdude"},
                                {"tag": "nexus_type", "value": "submap"},
                                {"tag": "map_id", "value": sm["id"]}
                            ]
                        })
                        map_service_ids[sm["id"]] = s_res["serviceids"][0]
                        report["submaps_synced"] += 1
                    break
                remaining_submaps = next_rem

        # ─── Paso 2: Procesar Nodos y Aristas por cada Mapa ──────────────────
        for m in target_maps:
            cur_map_id = m["id"]
            cur_map_service_id = map_service_ids.get(cur_map_id)
            analysis = await self.analyze_topology(cur_map_id)

            map_nodes_details = []

            # Filtrar y ordenar nodos en orden topológico (Proveedores/Padres primero)
            candidate_nodes = [n for n in analysis["nodes"] if n.get("device_type") not in ("submap", "parent_map")]

            sorted_nodes = []
            visited_ids = set()

            # Nodos raíz (sin uplinks) primero
            for n in candidate_nodes:
                if not n.get("uplink_ids"):
                    sorted_nodes.append(n)
                    visited_ids.add(n["node_id"])

            # Nodos dependientes iterativamente
            remaining = [n for n in candidate_nodes if n["node_id"] not in visited_ids]
            iterations = 0
            while remaining and iterations < 50:
                iterations += 1
                progress = False
                next_remaining = []
                for n in remaining:
                    if all(u_id in visited_ids or u_id in node_service_ids for u_id in n.get("uplink_ids", [])):
                        sorted_nodes.append(n)
                        visited_ids.add(n["node_id"])
                        progress = True
                    else:
                        next_remaining.append(n)
                remaining = next_remaining
                if not progress:
                    sorted_nodes.extend(remaining)
                    break

            # Crear servicios para cada nodo del mapa en orden de flujo de servicio
            for n_info in sorted_nodes:
                nid = n_info["node_id"]
                node_name = n_info["name"]

                matched = n_info.get("zabbix_matched")
                z_name = n_info.get("zabbix_hostname") or node_name
                z_hostid = n_info.get("zabbix_hostid")

                # Problem tags para vincular alarmas de ping / disponibilidad de Zabbix
                problem_tags = []
                if matched and z_name:
                    problem_tags.append({"tag": "host", "operator": 0, "value": z_name})
                    if z_hostid:
                        await self.ensure_host_tag(str(z_hostid), z_name)
                    report["nodes_matched_zabbix"] += 1

                # Determinar padres del servicio en función de las aristas directas
                relation_type = n_info["relation_type"]
                uplink_ids = n_info["uplink_ids"]
                parents = []

                if uplink_ids:
                    for p_nid in uplink_ids:
                        if p_nid in node_service_ids:
                            parents.append({"serviceid": node_service_ids[p_nid]})
                            report["direct_dependencies_created"] += 1

                if not parents and cur_map_service_id:
                    parents.append({"serviceid": cur_map_service_id})

                has_children = len(n_info.get("downlink_ids", [])) > 0

                # Algoritmo de cálculo:
                # 2 = Most critical of child services (para propagar fallas hacia arriba)
                algorithm = 2 if has_children else 0

                # En Zabbix 7.0, un servicio con hijos NO puede tener problem_tags directamente
                service_problem_tags = [] if has_children else problem_tags

                service_payload = {
                    "name": f"[DEVICE] {node_name}",
                    "algorithm": algorithm,
                    "sortorder": 0,
                    "parents": parents,
                    "problem_tags": service_problem_tags,
                    "tags": [
                        {"tag": "managed_by", "value": "nexusdude"},
                        {"tag": "nexus_type", "value": "device"},
                        {"tag": "node_id", "value": nid},
                        {"tag": "map_id", "value": cur_map_id},
                        {"tag": "relation_type", "value": relation_type},
                        {"tag": "zabbix_matched", "value": "true" if matched else "false"}
                    ]
                }

                try:
                    s_res = await self._call_api("service.create", service_payload)
                    node_s_id = s_res["serviceids"][0]
                    node_service_ids[nid] = node_s_id
                    report["nodes_synced"] += 1

                    # Si tiene hijos y está vinculado a Zabbix, crear un servicio hoja [HOST]
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
                                    {"tag": "map_id", "value": cur_map_id}
                                ]
                            }
                            await self._call_api("service.create", host_leaf_payload)
                        except Exception as h_err:
                            logger.warning(f"Error creando servicio hoja [HOST] para {node_name}: {h_err}")

                    map_nodes_details.append({
                        "node_name": node_name,
                        "service_id": node_s_id,
                        "relation_type": relation_type,
                        "parents_count": len(parents),
                        "zabbix_matched": matched
                    })
                except Exception as err:
                    logger.error(f"Error creando servicio Zabbix para nodo {node_name}: {err}")

            report["details"].append({
                "map_id": cur_map_id,
                "map_name": m["name"],
                "nodes_processed": len(map_nodes_details),
                "nodes": map_nodes_details
            })

        logger.info(f"Sincronización NexusDude -> Zabbix ({scope}) completada exitosamente: {report}")
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
                            "is_online": is_online,
                            "icmp_ping": int(icmp_val) if icmp_val in ('0', '1') else None,
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
                elif warns > 0:
                    sm_status = "warning"
                else:
                    sm_status = "ok"

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

        return {
            "node_id": node_id,
            "name": n_dict["name"],
            "ip": n_dict.get("ip") or "",
            "status": status,
            "is_online": (status != "down"),
            "icmp_ping": int(icmp_val) if icmp_val in ('0', '1') else None,
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

zabbix_service = ZabbixService()


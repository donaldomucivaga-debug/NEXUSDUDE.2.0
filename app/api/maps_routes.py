import json
import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.database import get_db_connection
from app.models import (
    MapOut, MapDetailOut, MapCreate, MapUpdate,
    NodeOut, NodeCreate, NodeUpdate,
    LinkOut, LinkCreate, LinkUpdate,
    CreateMapFromSiteRequest, PopulateMapFromSiteRequest,
    BulkCreateMapsFromSitesRequest, BulkDeleteNodesRequest
)
from app.services.inventory_service import inventory_service
from app.services.zabbix_service import zabbix_service

router = APIRouter(prefix="/maps", tags=["Maps & Topology"])

@router.get("", response_model=List[MapOut])
async def list_maps(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista todos los mapas disponibles."""
    async with get_db_connection() as db:
        cursor = await db.execute("""
            SELECT m.*,
                   (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id) as nodes_count,
                   (SELECT COUNT(*) FROM links l WHERE l.map_id = m.id) as links_count
            FROM maps m
            ORDER BY m.created_at ASC
        """)
        rows = await cursor.fetchall()
        return [
            MapOut(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                parent_map_id=r["parent_map_id"],
                grid_size=r["grid_size"],
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
                nodes_count=r["nodes_count"],
                links_count=r["links_count"]
            )
            for r in rows
        ]

@router.get("/sites-status")
async def get_sites_mapping_status(user: Dict[str, Any] = Depends(get_current_user)):
    """
    Retorna el estado de mapeo de todos los sitios de NetBox:
    cuáles ya tienen submapa (para no tocarlos) y cuáles están pendientes por generar.
    """
    if not inventory_service._sites_cache or not inventory_service._devices_cache:
        await inventory_service.refresh_cache()

    sites_summary = await inventory_service.get_sites_summary()

    async with get_db_connection() as db:
        c_maps = await db.execute("SELECT id, name FROM maps")
        existing_maps_rows = await c_maps.fetchall()
        existing_map_names = {r["name"].strip().lower(): r["id"] for r in existing_maps_rows}

    status_list = []
    mapped_count = 0
    unmapped_count = 0

    for s in sites_summary:
        s_name = (s.get("name") or "").strip()
        s_lower = s_name.lower()
        has_map = s_lower in existing_map_names
        map_id = existing_map_names.get(s_lower)
        if has_map:
            mapped_count += 1
        else:
            unmapped_count += 1

        status_list.append({
            "site_id": s.get("id"),
            "site_name": s_name,
            "device_count": s.get("device_count", 0),
            "has_map": has_map,
            "existing_map_id": map_id
        })

    return {
        "total_sites": len(sites_summary),
        "mapped_count": mapped_count,
        "unmapped_count": unmapped_count,
        "sites": status_list
    }

async def ensure_map_navigation_nodes(db, map_id: str):
    """
    Garantiza automáticamente la presencia y sincronía de los nodos de navegación en el lienzo:
    1. Si este mapa tiene parent_map_id válido, asegura el nodo de retorno hacia el mapa padre ('parent_map').
       Si el mapa padre no existe o el mapa es raíz, elimina nodos 'parent_map' huérfanos.
    2. Si este mapa tiene submapas hijos (maps con parent_map_id = map_id), asegura un nodo de navegación ('submap')
       por cada submapa hijo.
       Si existen nodos 'submap' que apuntan a submapas inexistentes o desvinculados, los limpia.
    """
    c_map = await db.execute("SELECT id, name, parent_map_id FROM maps WHERE id = ?", (map_id,))
    m = await c_map.fetchone()
    if not m:
        return

    # --- 1. Sincronizar Nodo de Retorno a Mapa Padre ---
    parent_id = m["parent_map_id"]
    if parent_id and parent_id != map_id:
        c_parent = await db.execute("SELECT id, name FROM maps WHERE id = ?", (parent_id,))
        p_row = await c_parent.fetchone()
        if p_row:
            parent_name = p_row["name"]
            c_existing = await db.execute("""
                SELECT id, name, extra_data FROM nodes 
                WHERE map_id = ? AND (device_type = 'parent_map' OR extra_data LIKE '%"is_parent_shortcut": true%')
            """, (map_id,))
            p_nodes = await c_existing.fetchall()
            if p_nodes:
                for pn in p_nodes:
                    extra = json.loads(pn["extra_data"]) if pn["extra_data"] else {}
                    if extra.get("target_map_id") != parent_id or pn["name"] != f"📁 ⬆ {parent_name}":
                        extra["target_map_id"] = parent_id
                        extra["is_parent_shortcut"] = True
                        extra["parent_map_name"] = parent_name
                        extra["role"] = "Mapa Superior"
                        await db.execute("""
                            UPDATE nodes SET name = ?, site_name = ?, extra_data = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (f"📁 ⬆ {parent_name}", parent_name, json.dumps(extra), pn["id"]))
            else:
                pnode_id = f"node-{uuid.uuid4().hex[:8]}"
                p_extra = json.dumps({
                    "target_map_id": parent_id,
                    "is_parent_shortcut": True,
                    "parent_map_name": parent_name,
                    "role": "Mapa Superior"
                })
                await db.execute("""
                    INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                    VALUES (?, ?, ?, ?, 'parent_map', ?, 80.0, 80.0, 'ok', ?)
                """, (pnode_id, map_id, f"📁 ⬆ {parent_name}", "", parent_name, p_extra))
        else:
            await db.execute("UPDATE maps SET parent_map_id = NULL WHERE id = ?", (map_id,))
            c_del = await db.execute("""
                SELECT id FROM nodes 
                WHERE map_id = ? AND (device_type = 'parent_map' OR extra_data LIKE '%"is_parent_shortcut": true%')
            """, (map_id,))
            for d in await c_del.fetchall():
                await db.execute("DELETE FROM links WHERE source_node_id = ? OR target_node_id = ?", (d["id"], d["id"]))
                await db.execute("DELETE FROM nodes WHERE id = ?", (d["id"],))
    else:
        c_del = await db.execute("""
            SELECT id FROM nodes 
            WHERE map_id = ? AND (device_type = 'parent_map' OR extra_data LIKE '%"is_parent_shortcut": true%')
        """, (map_id,))
        for d in await c_del.fetchall():
            await db.execute("DELETE FROM links WHERE source_node_id = ? OR target_node_id = ?", (d["id"], d["id"]))
            await db.execute("DELETE FROM nodes WHERE id = ?", (d["id"],))

    # --- 2. Sincronizar Nodos de Submapas Hijos ---
    c_children = await db.execute("SELECT id, name, description FROM maps WHERE parent_map_id = ?", (map_id,))
    child_maps = await c_children.fetchall()
    child_map_dict = {cm["id"]: cm for cm in child_maps}

    c_cur_sub = await db.execute("SELECT id, name, x, y, extra_data FROM nodes WHERE map_id = ? AND device_type = 'submap'", (map_id,))
    existing_sub_nodes = await c_cur_sub.fetchall()

    covered_child_ids = set()
    for sn in existing_sub_nodes:
        extra = json.loads(sn["extra_data"]) if sn["extra_data"] else {}
        t_id = extra.get("target_map_id")

        if not t_id:
            clean_name = sn["name"].replace("📁", "").strip().lower()
            for cm_id, cm in child_map_dict.items():
                if cm["name"].strip().lower() == clean_name:
                    t_id = cm_id
                    extra["target_map_id"] = cm_id
                    break

        if t_id and t_id in child_map_dict:
            covered_child_ids.add(t_id)
            cm = child_map_dict[t_id]
            expected_name = f"📁 {cm['name']}"
            if sn["name"] != expected_name and sn["name"].replace("📁", "").strip() != cm["name"].strip():
                expected_name = f"📁 {cm['name']}"

            needs_update = False
            if extra.get("target_map_id") != t_id:
                extra["target_map_id"] = t_id
                needs_update = True
            if not extra.get("role"):
                extra["role"] = "Submapa"
                needs_update = True
            if sn["name"] != expected_name:
                needs_update = True

            if needs_update:
                await db.execute("""
                    UPDATE nodes SET name = ?, site_name = ?, extra_data = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (expected_name, cm["description"] or cm["name"], json.dumps(extra), sn["id"]))
        else:
            # Submapa desvinculado o inexistente -> eliminar nodo y sus enlaces
            await db.execute("DELETE FROM links WHERE source_node_id = ? OR target_node_id = ?", (sn["id"], sn["id"]))
            await db.execute("DELETE FROM nodes WHERE id = ?", (sn["id"],))

    uncovered_children = [cm for cm in child_maps if cm["id"] not in covered_child_ids]
    if uncovered_children:
        cur_count = len(covered_child_ids)
        for idx, cm in enumerate(uncovered_children):
            slot_idx = cur_count + idx
            col = slot_idx % 4
            row = slot_idx // 4
            x = 80.0 + (col * 220.0)
            y = 170.0 + (row * 100.0)

            snode_id = f"node-{uuid.uuid4().hex[:8]}"
            extra = json.dumps({
                "target_map_id": cm["id"],
                "role": "Submapa",
                "site_name": cm["name"]
            })
            await db.execute("""
                INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                VALUES (?, ?, ?, '', 'submap', ?, ?, ?, 'ok', ?)
            """, (snode_id, map_id, f"📁 {cm['name']}", cm["description"] or cm["name"], x, y, extra))

    await db.commit()

@router.get("/{map_id}", response_model=MapDetailOut)
async def get_map_detail(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Obtiene el mapa con todos sus nodos y enlaces, asegurando automáticamente la presencia de nodos de navegación."""
    async with get_db_connection() as db:
        # Asegurar y sincronizar nodos de navegación para este mapa
        await ensure_map_navigation_nodes(db, map_id)

        # Obtener mapa
        cursor = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
        m = await cursor.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        # Nodos
        c_nodes = await db.execute("SELECT * FROM nodes WHERE map_id = ?", (map_id,))
        nodes_rows = await c_nodes.fetchall()
        nodes = []
        for r in nodes_rows:
            extra = json.loads(r["extra_data"]) if r["extra_data"] else None
            nodes.append(NodeOut(
                id=r["id"],
                map_id=r["map_id"],
                device_id=r["device_id"],
                name=r["name"],
                ip=r["ip"],
                device_type=r["device_type"],
                site_name=r["site_name"],
                x=r["x"],
                y=r["y"],
                status=r["status"],
                extra_data=extra,
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"])
            ))

        # Enlaces
        c_links = await db.execute("SELECT * FROM links WHERE map_id = ?", (map_id,))
        links_rows = await c_links.fetchall()
        links = []
        for r in links_rows:
            extra = json.loads(r["extra_data"]) if r["extra_data"] else None
            links.append(LinkOut(
                id=r["id"],
                map_id=r["map_id"],
                source_node_id=r["source_node_id"],
                target_node_id=r["target_node_id"],
                source_interface=r["source_interface"],
                target_interface=r["target_interface"],
                status=r["status"],
                rtt_ms=r["rtt_ms"],
                loss_percent=r["loss_percent"],
                extra_data=extra,
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"])
            ))

        return MapDetailOut(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            parent_map_id=m["parent_map_id"],
            grid_size=m["grid_size"],
            created_at=str(m["created_at"]),
            updated_at=str(m["updated_at"]),
            nodes_count=len(nodes),
            links_count=len(links),
            nodes=nodes,
            links=links
        )

@router.post("", response_model=MapOut, status_code=status.HTTP_201_CREATED)
async def create_map(map_data: MapCreate, user: Dict[str, Any] = Depends(get_current_user)):
    """Crea un nuevo mapa de topología y sincroniza la navegación jerárquica."""
    new_id = map_data.id or f"map-{uuid.uuid4().hex[:8]}"
    parent_id = map_data.parent_map_id.strip() if map_data.parent_map_id and map_data.parent_map_id.strip() else None
    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO maps (id, name, description, parent_map_id, grid_size)
            VALUES (?, ?, ?, ?, ?)
        """, (new_id, map_data.name, map_data.description, parent_id, map_data.grid_size or 20))
        await db.commit()

        # Sincronizar automáticamente navegación en el nuevo mapa y en el padre
        await ensure_map_navigation_nodes(db, new_id)
        if parent_id:
            await ensure_map_navigation_nodes(db, parent_id)

        cursor = await db.execute("""
            SELECT m.*, (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id) as nodes_count
            FROM maps m WHERE m.id = ?
        """, (new_id,))
        m = await cursor.fetchone()
        return MapOut(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            parent_map_id=m["parent_map_id"],
            grid_size=m["grid_size"],
            created_at=str(m["created_at"]),
            updated_at=str(m["updated_at"]),
            nodes_count=m["nodes_count"] or 0,
            links_count=0
        )

def arrange_site_nodes(devices: List[Dict[str, Any]], start_x: float = 80.0, start_y: float = 80.0) -> List[Dict[str, Any]]:
    """Organiza automáticamente los equipos de un sitio en una cuadrícula jerárquica por roles."""
    tiers: Dict[int, List[Dict[str, Any]]] = {1: [], 2: [], 3: [], 4: []}
    for dev in devices:
        r = (dev.get("role") or "").lower()
        if any(k in r for k in ["core", "router", "gateway", "borde", "bgp", "sitemonitor", "site monitor"]):
            tier = 1
        elif any(k in r for k in ["switch", "olt", "distribucion", "torre"]):
            tier = 2
        elif any(k in r for k in ["ap", "access point", "sector", "radio", "ptp", "backhaul", "antena"]):
            tier = 3
        else:
            tier = 4
        tiers[tier].append(dev)

    arranged = []
    current_y = start_y
    x_step = 220.0  # Ancho de nodo responsivo + margen
    y_step = 95.0   # Alto de nodo + margen
    max_cols = 5

    for tier_num in [1, 2, 3, 4]:
        tier_devices = tiers[tier_num]
        if not tier_devices:
            continue
        for idx, d in enumerate(tier_devices):
            col = idx % max_cols
            row_in_tier = idx // max_cols
            x = start_x + (col * x_step)
            y = current_y + (row_in_tier * y_step)
            arranged.append({
                "device": d,
                "x": float(x),
                "y": float(y)
            })
        num_rows = ((len(tier_devices) - 1) // max_cols) + 1
        current_y += (num_rows * y_step) + 40.0

    return arranged

@router.post("/from-site", status_code=status.HTTP_201_CREATED)
async def create_map_from_site(req: CreateMapFromSiteRequest, user: Dict[str, Any] = Depends(get_current_user)):
    """Crea un mapa o submapa a partir de un Sitio de NetBox y opcionalmente puebla todos sus equipos."""
    site_name = req.site_name.strip()
    devices = await inventory_service.get_devices_by_site(site_name)

    new_map_id = f"map-{uuid.uuid4().hex[:8]}"
    desc = f"Sitio NetBox: {site_name} ({len(devices)} equipos)"

    async with get_db_connection() as db:
        # 1. Crear el registro del mapa
        await db.execute("""
            INSERT INTO maps (id, name, description, parent_map_id, grid_size)
            VALUES (?, ?, ?, ?, ?)
        """, (new_map_id, site_name, desc, req.parent_map_id, 20))

        # 2. Si se solicitó nodo de submapa en el padre
        submap_node = None
        if req.insert_submap_node and req.parent_map_id:
            subnode_id = f"node-{uuid.uuid4().hex[:8]}"
            sub_x = req.x if req.x is not None else 100.0
            sub_y = req.y if req.y is not None else 100.0
            sub_extra = json.dumps({
                "target_map_id": new_map_id,
                "site_name": site_name,
                "device_count": len(devices) if req.auto_populate else 0
            })
            await db.execute("""
                INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subnode_id, req.parent_map_id, site_name, "", "submap",
                site_name, sub_x, sub_y, "ok", sub_extra
            ))
            cursor_sub = await db.execute("SELECT * FROM nodes WHERE id = ?", (subnode_id,))
            sub_r = await cursor_sub.fetchone()
            submap_node = NodeOut(
                id=sub_r["id"],
                map_id=sub_r["map_id"],
                name=sub_r["name"],
                ip=sub_r["ip"],
                device_type=sub_r["device_type"],
                site_name=sub_r["site_name"],
                x=sub_r["x"],
                y=sub_r["y"],
                status=sub_r["status"],
                extra_data=json.loads(sub_r["extra_data"]) if sub_r["extra_data"] else None
            )

        # 3. Si tiene mapa padre, insertar nodo de navegación de retorno hacia el mapa padre
        if req.parent_map_id:
            c_parent = await db.execute("SELECT name FROM maps WHERE id = ?", (req.parent_map_id,))
            p_row = await c_parent.fetchone()
            parent_name = p_row["name"] if p_row else "Topología Principal"
            parent_node_id = f"node-{uuid.uuid4().hex[:8]}"
            parent_extra = json.dumps({
                "target_map_id": req.parent_map_id,
                "is_parent_shortcut": True,
                "parent_map_name": parent_name,
                "role": "Mapa Superior"
            })
            await db.execute("""
                INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parent_node_id, new_map_id, f"📁 ⬆ {parent_name}", "", "parent_map",
                parent_name, 80.0, 80.0, "ok", parent_extra
            ))

        # 4. Si auto_populate es True, insertar los equipos organizados jerárquicamente
        if req.auto_populate and devices:
            devices_start_y = 170.0 if req.parent_map_id else 80.0
            arranged = arrange_site_nodes(devices, start_x=80.0, start_y=devices_start_y)
            for item in arranged:
                d = item["device"]
                nid = f"node-{uuid.uuid4().hex[:8]}"
                d_extra = json.dumps({
                    "manufacturer": d.get("manufacturer") or "Genérico",
                    "model": d.get("model") or "",
                    "serial": d.get("serial") or "",
                    "role": d.get("role") or d.get("device_type") or "Dispositivo",
                    "role_color": d.get("role_color") or "",
                    "status": d.get("status") or "active"
                })
                await db.execute("""
                    INSERT INTO nodes (id, map_id, device_id, name, ip, device_type, site_name, x, y, status, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    nid, new_map_id, d.get("id"), d.get("name"), d.get("ip") or "",
                    d.get("role") or "generic", site_name, item["x"], item["y"], "ok", d_extra
                ))

        await db.commit()

        cursor = await db.execute("SELECT * FROM maps WHERE id = ?", (new_map_id,))
        m = await cursor.fetchone()

        return {
            "status": "success",
            "map": MapOut(
                id=m["id"],
                name=m["name"],
                description=m["description"],
                parent_map_id=m["parent_map_id"],
                grid_size=m["grid_size"],
                created_at=str(m["created_at"]),
                updated_at=str(m["updated_at"]),
                nodes_count=len(devices) if req.auto_populate else 0,
                links_count=0
            ),
            "submap_node": submap_node,
            "devices_count": len(devices)
        }

@router.post("/bulk-from-sites")
async def bulk_create_maps_from_sites(
    req: BulkCreateMapsFromSitesRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Crea mapas en lote a partir de todos los sitios existentes en NetBox."""
    parent_map_id = req.parent_map_id.strip() if req.parent_map_id else None

    # Asegurar que el inventario esté cargado
    if not inventory_service._sites_cache or not inventory_service._devices_cache:
        await inventory_service.refresh_cache()

    sites_summary = await inventory_service.get_sites_summary()

    # Pre-indexar dispositivos por sitio en memoria para O(1) lookups
    devices_by_site: Dict[str, List[Dict[str, Any]]] = {}
    for d in inventory_service._devices_cache:
        s_name = (d.get("site") or "").strip().lower()
        if s_name:
            devices_by_site.setdefault(s_name, []).append(d)

    # Filtrar según opciones
    if req.only_with_devices:
        sites_to_process = [s for s in sites_summary if (s.get("device_count") or 0) > 0]
    else:
        sites_to_process = sites_summary

    if req.max_sites and req.max_sites > 0:
        sites_to_process = sites_to_process[:req.max_sites]

    async with get_db_connection() as db:
        # Verificar mapa padre si se especificó
        parent_name_for_bulk = "Topología Principal"
        if parent_map_id:
            c_parent = await db.execute("SELECT id, name FROM maps WHERE id = ?", (parent_map_id,))
            parent_row = await c_parent.fetchone()
            if not parent_row:
                if parent_map_id == "default-map":
                    await db.execute("""
                        INSERT OR IGNORE INTO maps (id, name, description)
                        VALUES ('default-map', 'Topología Principal', 'Mapa raíz principal')
                    """)
                else:
                    parent_map_id = None
            else:
                parent_name_for_bulk = parent_row["name"]

        # Cargar mapas existentes para evitar duplicados
        c_maps = await db.execute("SELECT id, name FROM maps")
        existing_maps_rows = await c_maps.fetchall()
        existing_map_names = {r["name"].strip().lower(): r["id"] for r in existing_maps_rows}

        # Cargar nodos existentes en el mapa padre si se insertarán accesos de submapa
        existing_submap_names = set()
        submap_start_x = 80.0
        submap_start_y = 80.0

        if req.insert_submap_nodes and parent_map_id:
            c_pnodes = await db.execute("SELECT id, name, device_type, x, y FROM nodes WHERE map_id = ?", (parent_map_id,))
            pnodes = await c_pnodes.fetchall()
            for pn in pnodes:
                if pn["device_type"] == "submap":
                    existing_submap_names.add(pn["name"].strip().lower())
            if pnodes:
                max_p_y = max(pn["y"] for pn in pnodes)
                submap_start_y = max(80.0, max_p_y + 140.0)

        cols = 6
        x_step = 290.0
        y_step = 85.0
        submap_idx = len(existing_submap_names)

        created_maps_count = 0
        skipped_maps_count = 0
        total_devices_populated = 0
        submap_nodes_created = 0
        skipped_sites = []
        created_maps = []

        for site_info in sites_to_process:
            site_name = (site_info.get("name") or "").strip()
            if not site_name:
                continue

            site_lower = site_name.lower()
            if req.skip_existing and site_lower in existing_map_names:
                skipped_maps_count += 1
                skipped_sites.append(site_name)
                continue

            # Obtener dispositivos del sitio
            site_devices = devices_by_site.get(site_lower, [])
            new_map_id = f"map-{uuid.uuid4().hex[:8]}"
            desc = f"Sitio NetBox: {site_name} ({len(site_devices)} equipos)"

            # 1. Crear mapa
            await db.execute("""
                INSERT INTO maps (id, name, description, parent_map_id, grid_size)
                VALUES (?, ?, ?, ?, ?)
            """, (new_map_id, site_name, desc, parent_map_id, 20))

            existing_map_names[site_lower] = new_map_id
            created_maps_count += 1
            created_maps.append({"id": new_map_id, "name": site_name, "devices_count": len(site_devices)})

            # 2. Si tiene mapa padre, insertar acceso directo hacia el mapa padre dentro del submapa
            if parent_map_id:
                parent_node_id = f"node-{uuid.uuid4().hex[:8]}"
                parent_extra = json.dumps({
                    "target_map_id": parent_map_id,
                    "is_parent_shortcut": True,
                    "parent_map_name": parent_name_for_bulk,
                    "role": "Mapa Superior"
                })
                await db.execute("""
                    INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    parent_node_id, new_map_id, f"📁 ⬆ {parent_name_for_bulk}", "", "parent_map",
                    parent_name_for_bulk, 80.0, 80.0, "ok", parent_extra
                ))

            # 3. Poblar equipos del sitio con distribución jerárquica
            if req.auto_populate_devices and site_devices:
                dev_start_y = 170.0 if parent_map_id else 80.0
                arranged = arrange_site_nodes(site_devices, start_x=80.0, start_y=dev_start_y)
                for item in arranged:
                    d = item["device"]
                    nid = f"node-{uuid.uuid4().hex[:8]}"
                    d_extra = json.dumps({
                        "manufacturer": d.get("manufacturer") or "Genérico",
                        "model": d.get("model") or "",
                        "serial": d.get("serial") or "",
                        "role": d.get("role") or d.get("device_type") or "Dispositivo",
                        "role_color": d.get("role_color") or "",
                        "status": d.get("status") or "active"
                    })
                    await db.execute("""
                        INSERT INTO nodes (id, map_id, device_id, name, ip, device_type, site_name, x, y, status, extra_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        nid, new_map_id, d.get("id"), d.get("name"), d.get("ip") or "",
                        d.get("role") or "generic", site_name, item["x"], item["y"], "ok", d_extra
                    ))
                total_devices_populated += len(site_devices)

            # 4. Insertar acceso directo de submapa en el mapa padre
            if req.insert_submap_nodes and parent_map_id:
                if not (req.skip_existing and site_lower in existing_submap_names):
                    col = submap_idx % cols
                    row = submap_idx // cols
                    sub_x = submap_start_x + (col * x_step)
                    sub_y = submap_start_y + (row * y_step)
                    submap_idx += 1

                    subnode_id = f"node-{uuid.uuid4().hex[:8]}"
                    sub_extra = json.dumps({
                        "target_map_id": new_map_id,
                        "site_name": site_name,
                        "device_count": len(site_devices)
                    })
                    await db.execute("""
                        INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        subnode_id, parent_map_id, site_name, "", "submap",
                        site_name, sub_x, sub_y, "ok", sub_extra
                    ))
                    existing_submap_names.add(site_lower)
                    submap_nodes_created += 1

        # Confirmar todos los cambios atómicamente
        await db.commit()

        return {
            "status": "success",
            "message": f"Se procesaron {len(sites_to_process)} sitios: {created_maps_count} mapas creados, {total_devices_populated} dispositivos organizados, {submap_nodes_created} accesos directos agregados.",
            "total_evaluated": len(sites_to_process),
            "created_count": created_maps_count,
            "skipped_count": skipped_maps_count,
            "devices_populated": total_devices_populated,
            "submap_nodes_created": submap_nodes_created,
            "skipped_sites": skipped_sites[:30],
            "created_sample": created_maps[:10]
        }

@router.post("/{map_id}/populate-from-site")
async def populate_map_from_site(map_id: str, req: PopulateMapFromSiteRequest, user: Dict[str, Any] = Depends(get_current_user)):
    """Puebla un mapa existente con los equipos de su sitio de NetBox correspondiente sin duplicar."""
    # Forzar refresco fresco desde NetBox
    await inventory_service.refresh_cache(force=True)

    async with get_db_connection() as db:
        cursor_map = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
        m = await cursor_map.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        # Asegurar que si el mapa tiene parent_map_id tenga su nodo de navegación hacia el padre
        if m["parent_map_id"]:
            c_pnode = await db.execute("""
                SELECT id FROM nodes 
                WHERE map_id = ? AND (device_type = 'parent_map' OR extra_data LIKE '%"is_parent_shortcut": true%')
            """, (map_id,))
            if not await c_pnode.fetchone():
                c_parent = await db.execute("SELECT name FROM maps WHERE id = ?", (m["parent_map_id"],))
                p_row = await c_parent.fetchone()
                parent_name = p_row["name"] if p_row else "Topología Principal"
                p_node_id = f"node-{uuid.uuid4().hex[:8]}"
                p_extra = json.dumps({
                    "target_map_id": m["parent_map_id"],
                    "is_parent_shortcut": True,
                    "parent_map_name": parent_name,
                    "role": "Mapa Superior"
                })
                await db.execute("""
                    INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    p_node_id, map_id, f"📁 ⬆ {parent_name}", "", "parent_map",
                    parent_name, 80.0, 80.0, "ok", p_extra
                ))

        site_name = (req.site_name or m["name"]).strip()
        devices = await inventory_service.get_devices_by_site(site_name)
        if not devices:
            all_sites = await inventory_service.get_sites()
            for s in all_sites:
                if s["name"].lower() == site_name.lower() or s["name"].lower() in site_name.lower() or site_name.lower() in s["name"].lower():
                    site_name = s["name"]
                    devices = await inventory_service.get_devices_by_site(site_name)
                    break

        if not devices:
            raise HTTPException(status_code=404, detail=f"No se encontraron dispositivos en NetBox para el sitio '{site_name}'")

        cursor_existing = await db.execute("SELECT device_id, name, y FROM nodes WHERE map_id = ?", (map_id,))
        existing = await cursor_existing.fetchall()
        existing_device_ids = {r["device_id"] for r in existing if r["device_id"]}
        existing_names = {r["name"].lower() for r in existing if r["name"]}

        to_insert = [d for d in devices if d.get("id") not in existing_device_ids and d.get("name", "").lower() not in existing_names]

        if not to_insert:
            await db.commit()
            return {
                "status": "info",
                "message": f"Todos los {len(devices)} equipos del sitio '{site_name}' ya están presentes en este mapa.",
                "added_count": 0,
                "total_devices": len(devices),
                "site_name": site_name
            }

        start_y = 170.0 if m["parent_map_id"] else 80.0
        if existing:
            max_y = max((r["y"] for r in existing if r["y"] is not None), default=80.0)
            start_y = max(start_y, max_y + 120.0)

        arranged = arrange_site_nodes(to_insert, start_x=80.0, start_y=start_y)
        for item in arranged:
            d = item["device"]
            nid = f"node-{uuid.uuid4().hex[:8]}"
            d_extra = json.dumps({
                "manufacturer": d.get("manufacturer") or "Genérico",
                "model": d.get("model") or "",
                "serial": d.get("serial") or "",
                "role": d.get("role") or d.get("device_type") or "Dispositivo",
                "role_color": d.get("role_color") or "",
                "status": d.get("status") or "active"
            })
            await db.execute("""
                INSERT INTO nodes (id, map_id, device_id, name, ip, device_type, site_name, x, y, status, extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                nid, map_id, d.get("id"), d.get("name"), d.get("ip") or "",
                d.get("role") or "generic", site_name, item["x"], item["y"], "ok", d_extra
            ))

        await db.commit()

        return {
            "status": "success",
            "message": f"✔ ¡Sincronización completada! Se agregaron {len(to_insert)} nuevos equipos del sitio '{site_name}' al mapa (Total: {len(existing) + len(to_insert)}).",
            "added_count": len(to_insert),
            "total_devices": len(devices),
            "site_name": site_name
        }

@router.put("/{map_id}", response_model=MapOut)
async def update_map(map_id: str, map_data: MapUpdate, user: Dict[str, Any] = Depends(get_current_user)):
    """Actualiza propiedades de un mapa (nombre, descripción, cuadrícula, mapa padre) y sincroniza jerarquía."""
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
        m = await cursor.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        old_parent_id = m["parent_map_id"]
        new_name = map_data.name if map_data.name is not None else m["name"]
        new_desc = map_data.description if map_data.description is not None else m["description"]
        new_grid = map_data.grid_size if map_data.grid_size is not None else m["grid_size"]

        new_parent_id = old_parent_id
        if map_data.parent_map_id is not None:
            raw_p = map_data.parent_map_id.strip() if map_data.parent_map_id else ""
            new_parent_id = raw_p if raw_p and raw_p != map_id else None

        await db.execute("""
            UPDATE maps
            SET name = ?, description = ?, grid_size = ?, parent_map_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_name, new_desc, new_grid, new_parent_id, map_id))
        await db.commit()

        # Sincronizar automáticamente los nodos de navegación en los mapas afectados
        await ensure_map_navigation_nodes(db, map_id)
        if old_parent_id and old_parent_id != new_parent_id:
            await ensure_map_navigation_nodes(db, old_parent_id)
        if new_parent_id:
            await ensure_map_navigation_nodes(db, new_parent_id)

        c_updated = await db.execute("""
            SELECT m.*, (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id) as nodes_count,
                   (SELECT COUNT(*) FROM links l WHERE l.map_id = m.id) as links_count
            FROM maps m WHERE m.id = ?
        """, (map_id,))
        u = await c_updated.fetchone()
        return MapOut(
            id=u["id"],
            name=u["name"],
            description=u["description"],
            parent_map_id=u["parent_map_id"],
            grid_size=u["grid_size"],
            created_at=str(u["created_at"]),
            updated_at=str(u["updated_at"]),
            nodes_count=u["nodes_count"] or 0,
            links_count=u["links_count"] or 0
        )

@router.delete("/{map_id}")
async def delete_map(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Elimina un mapa específico sin destruir sus submapas dependientes.
    Los submapas hijos se desacoplan (parent_map_id pasa a NULL o nivel independiente),
    preservando todos sus dispositivos, nodos, enlaces y configuración intactos.
    """
    if map_id == "default-map":
        raise HTTPException(status_code=400, detail="No se puede eliminar el mapa principal del sistema")

    async with get_db_connection() as db:
        cursor = await db.execute("SELECT id, name, parent_map_id FROM maps WHERE id = ?", (map_id,))
        m = await cursor.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        map_name = m["name"]
        old_parent_id = m["parent_map_id"]

        # 1. Desacoplar submapas dependientes (hijos directos) preservando todas sus instancias intactas
        c_children = await db.execute("SELECT id, name FROM maps WHERE parent_map_id = ?", (map_id,))
        children = await c_children.fetchall()
        for child in children:
            cid = child["id"]
            # Desvincular parent_map_id para que quede como mapa independiente
            await db.execute("UPDATE maps SET parent_map_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (cid,))
            # Limpiar en el submapa hijo el nodo de retorno hacia este mapa eliminado
            c_pnodes = await db.execute("""
                SELECT id FROM nodes 
                WHERE map_id = ? AND (device_type = 'parent_map' OR extra_data LIKE '%"is_parent_shortcut": true%')
            """, (cid,))
            for pn in await c_pnodes.fetchall():
                await db.execute("DELETE FROM links WHERE source_node_id = ? OR target_node_id = ?", (pn["id"], pn["id"]))
                await db.execute("DELETE FROM nodes WHERE id = ?", (pn["id"],))

        # 2. Limpiar en otros mapas los nodos 'submap' que apuntaban a este mapa eliminado
        c_subnodes = await db.execute("SELECT id FROM nodes WHERE device_type = 'submap' AND extra_data LIKE ?", (f'%"{map_id}"%',))
        for sn in await c_subnodes.fetchall():
            await db.execute("DELETE FROM links WHERE source_node_id = ? OR target_node_id = ?", (sn["id"], sn["id"]))
            await db.execute("DELETE FROM nodes WHERE id = ?", (sn["id"],))

        # 3. Eliminar enlaces y nodos pertenecientes ÚNICAMENTE a este mapa
        await db.execute("DELETE FROM links WHERE map_id = ?", (map_id,))
        await db.execute("DELETE FROM nodes WHERE map_id = ?", (map_id,))

        # 4. Eliminar el registro del mapa
        await db.execute("DELETE FROM maps WHERE id = ?", (map_id,))
        await db.commit()

        # Si el mapa eliminado tenía un padre, sincronizar los nodos de navegación del padre
        if old_parent_id:
            await ensure_map_navigation_nodes(db, old_parent_id)

        return {
            "status": "success",
            "message": f"Se eliminó el mapa '{map_name}'. Se preservaron {len(children)} submapa(s) hijo(s) de forma independiente.",
            "deleted_id": map_id,
            "preserved_child_ids": [c["id"] for c in children]
        }

@router.get("/{map_id}/breadcrumb")
async def get_map_breadcrumb(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Retorna la jerarquía de mapas padres desde la raíz hasta el mapa actual."""
    breadcrumbs = []
    curr_id = map_id
    visited = set()

    async with get_db_connection() as db:
        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            cursor = await db.execute("SELECT id, name, parent_map_id FROM maps WHERE id = ?", (curr_id,))
            row = await cursor.fetchone()
            if not row:
                break
            breadcrumbs.insert(0, {
                "id": row["id"],
                "name": row["name"],
                "parent_map_id": row["parent_map_id"]
            })
            curr_id = row["parent_map_id"]

    return breadcrumbs

@router.post("/{map_id}/ensure-parent-node")
async def ensure_parent_node(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Sincroniza y asegura los nodos de navegación (padre y submapas) para este mapa."""
    async with get_db_connection() as db:
        await ensure_map_navigation_nodes(db, map_id)
        return {
            "status": "success",
            "message": "✔ Nodos de navegación sincronizados correctamente para el mapa.",
            "map_id": map_id
        }

@router.post("/sync-all-navigation-nodes")
@router.post("/retrofit-parent-nodes")
async def sync_all_navigation_nodes(user: Dict[str, Any] = Depends(get_current_user)):
    """Sincroniza todos los nodos de navegación (submapas y padres) en todos los mapas del sistema."""
    async with get_db_connection() as db:
        c_maps = await db.execute("SELECT id FROM maps")
        all_maps = await c_maps.fetchall()
        for m in all_maps:
            await ensure_map_navigation_nodes(db, m["id"])

        return {
            "status": "success",
            "message": f"Se sincronizaron los nodos de navegación en {len(all_maps)} mapas.",
            "total_maps": len(all_maps)
        }

# --- Endpoints de Nodos ---

@router.post("/nodes", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_node(node_data: NodeCreate, user: Dict[str, Any] = Depends(get_current_user)):
    """Agrega un nodo al lienzo."""
    node_id = f"node-{uuid.uuid4().hex[:8]}"
    extra_str = json.dumps(node_data.extra_data) if node_data.extra_data else None

    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO nodes (id, map_id, device_id, name, ip, device_type, site_name, x, y, status, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            node_id, node_data.map_id, node_data.device_id, node_data.name,
            node_data.ip, node_data.device_type, node_data.site_name,
            node_data.x, node_data.y, node_data.status, extra_str
        ))
        await db.commit()

        cursor = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        r = await cursor.fetchone()
        return NodeOut(
            id=r["id"],
            map_id=r["map_id"],
            device_id=r["device_id"],
            name=r["name"],
            ip=r["ip"],
            device_type=r["device_type"],
            site_name=r["site_name"],
            x=r["x"],
            y=r["y"],
            status=r["status"],
            extra_data=json.loads(r["extra_data"]) if r["extra_data"] else None,
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"])
        )

@router.put("/nodes/{node_id}", response_model=NodeOut)
async def update_node(node_id: str, update_data: NodeUpdate, user: Dict[str, Any] = Depends(get_current_user)):
    """Actualiza propiedades de un nodo (posición x, y, etc.)."""
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        r = await cursor.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Nodo no encontrado")

        new_x = update_data.x if update_data.x is not None else r["x"]
        new_y = update_data.y if update_data.y is not None else r["y"]
        new_name = update_data.name if update_data.name is not None else r["name"]
        new_ip = update_data.ip if update_data.ip is not None else r["ip"]
        new_device_type = update_data.device_type if update_data.device_type is not None else r["device_type"]
        new_status = update_data.status if update_data.status is not None else r["status"]
        new_extra = json.dumps(update_data.extra_data) if update_data.extra_data is not None else r["extra_data"]

        await db.execute("""
            UPDATE nodes
            SET x = ?, y = ?, name = ?, ip = ?, device_type = ?, status = ?, extra_data = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_x, new_y, new_name, new_ip, new_device_type, new_status, new_extra, node_id))
        await db.commit()

        c_updated = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        u = await c_updated.fetchone()
        return NodeOut(
            id=u["id"],
            map_id=u["map_id"],
            device_id=u["device_id"],
            name=u["name"],
            ip=u["ip"],
            device_type=u["device_type"],
            site_name=u["site_name"],
            x=u["x"],
            y=u["y"],
            status=u["status"],
            extra_data=json.loads(u["extra_data"]) if u["extra_data"] else None,
            created_at=str(u["created_at"]),
            updated_at=str(u["updated_at"])
        )

@router.delete("/nodes/{node_id}")
async def delete_node(node_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina un nodo del lienzo."""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        await db.commit()
        return {"status": "success", "message": f"Nodo {node_id} eliminado"}

@router.post("/nodes/bulk-delete")
async def bulk_delete_nodes(req: BulkDeleteNodesRequest, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina múltiples nodos del lienzo en una sola transacción atómica."""
    if not req.node_ids:
        return {"status": "success", "deleted_count": 0, "deleted_ids": []}
    
    async with get_db_connection() as db:
        placeholders = ",".join("?" for _ in req.node_ids)
        await db.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", req.node_ids)
        await db.commit()
        return {"status": "success", "deleted_count": len(req.node_ids), "deleted_ids": req.node_ids}


# --- Endpoints de Enlaces ---

@router.post("/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
async def create_link(link_data: LinkCreate, user: Dict[str, Any] = Depends(get_current_user)):
    """Crea una arista/enlace entre dos nodos."""
    link_id = f"link-{uuid.uuid4().hex[:8]}"
    extra_str = json.dumps(link_data.extra_data) if link_data.extra_data else None

    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO links (id, map_id, source_node_id, target_node_id, source_interface, target_interface, status, rtt_ms, loss_percent, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            link_id, link_data.map_id, link_data.source_node_id, link_data.target_node_id,
            link_data.source_interface, link_data.target_interface, link_data.status,
            link_data.rtt_ms, link_data.loss_percent, extra_str
        ))
        await db.commit()

        cursor = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
        r = await cursor.fetchone()
        return LinkOut(
            id=r["id"],
            map_id=r["map_id"],
            source_node_id=r["source_node_id"],
            target_node_id=r["target_node_id"],
            source_interface=r["source_interface"],
            target_interface=r["target_interface"],
            status=r["status"],
            rtt_ms=r["rtt_ms"],
            loss_percent=r["loss_percent"],
            extra_data=json.loads(r["extra_data"]) if r["extra_data"] else None,
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"])
        )

@router.put("/links/{link_id}", response_model=LinkOut)
async def update_link(link_id: str, link_data: LinkUpdate, user: Dict[str, Any] = Depends(get_current_user)):
    """Actualiza propiedades de un enlace (interfaces, dirección de servicio, extra_data)."""
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
        r = await cursor.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Enlace no encontrado")

        new_src_iface = link_data.source_interface if link_data.source_interface is not None else r["source_interface"]
        new_tgt_iface = link_data.target_interface if link_data.target_interface is not None else r["target_interface"]
        new_status = link_data.status if link_data.status is not None else r["status"]
        new_rtt = link_data.rtt_ms if link_data.rtt_ms is not None else r["rtt_ms"]
        new_loss = link_data.loss_percent if link_data.loss_percent is not None else r["loss_percent"]

        cur_extra = json.loads(r["extra_data"]) if r["extra_data"] else {}
        if link_data.extra_data is not None:
            cur_extra.update(link_data.extra_data)

        await db.execute("""
            UPDATE links
            SET source_interface = ?, target_interface = ?, status = ?, rtt_ms = ?, loss_percent = ?,
                extra_data = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_src_iface, new_tgt_iface, new_status, new_rtt, new_loss, json.dumps(cur_extra), link_id))
        await db.commit()

        c_u = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
        u = await c_u.fetchone()
        return LinkOut(
            id=u["id"],
            map_id=u["map_id"],
            source_node_id=u["source_node_id"],
            target_node_id=u["target_node_id"],
            source_interface=u["source_interface"],
            target_interface=u["target_interface"],
            status=u["status"],
            rtt_ms=u["rtt_ms"],
            loss_percent=u["loss_percent"],
            extra_data=json.loads(u["extra_data"]) if u["extra_data"] else None,
            created_at=str(u["created_at"]),
            updated_at=str(u["updated_at"])
        )

@router.delete("/links/{link_id}")
async def delete_link(link_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina un enlace entre nodos."""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM links WHERE id = ?", (link_id,))
        await db.commit()
        return {"status": "success", "message": f"Enlace {link_id} eliminado"}


# --- Sincronización Masiva de Nodos con NetBox ---

@router.post("/sync-all-netbox-nodes")
async def sync_all_maps_nodes_from_netbox(user: Dict[str, Any] = Depends(get_current_user)):
    """
    Sincroniza masivamente las propiedades (nombre, ip, rol, color de rol, modelo, fabricante, serial, status)
    de todos los nodos de todos los mapas contra la información más reciente de NetBox.
    """
    await inventory_service.refresh_cache(force=True)

    dev_by_id = {d["id"]: d for d in inventory_service._devices_cache}
    dev_by_name = {d["name"].strip().lower(): d for d in inventory_service._devices_cache}

    updated_count = 0
    async with get_db_connection() as db:
        c_nodes = await db.execute("SELECT id, map_id, device_id, name, ip, device_type, site_name, extra_data FROM nodes")
        nodes_rows = await c_nodes.fetchall()

        for nr in nodes_rows:
            nid = nr["id"]
            did = nr["device_id"]
            name = nr["name"]

            matched_dev = None
            if did and did in dev_by_id:
                matched_dev = dev_by_id[did]
            elif name and name.strip().lower() in dev_by_name:
                matched_dev = dev_by_name[name.strip().lower()]

            if matched_dev:
                try:
                    edata = json.loads(nr["extra_data"]) if nr["extra_data"] else {}
                except Exception:
                    edata = {}

                edata["manufacturer"] = matched_dev.get("manufacturer") or edata.get("manufacturer", "Genérico")
                edata["model"] = matched_dev.get("model") or edata.get("model", "")
                edata["serial"] = matched_dev.get("serial") or edata.get("serial", "")
                edata["role"] = matched_dev.get("role") or matched_dev.get("device_type") or edata.get("role", "Dispositivo")
                edata["role_color"] = matched_dev.get("role_color") or edata.get("role_color", "")
                edata["status"] = matched_dev.get("status") or edata.get("status", "active")

                new_device_type = matched_dev.get("role") or nr["device_type"]
                new_name = matched_dev.get("name") or name
                new_ip = matched_dev.get("ip") or nr["ip"]
                new_did = matched_dev.get("id") or did
                new_site = matched_dev.get("site") or nr["site_name"]

                await db.execute("""
                    UPDATE nodes
                    SET device_id = ?, name = ?, ip = ?, device_type = ?, site_name = ?, extra_data = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (new_did, new_name, new_ip, new_device_type, new_site, json.dumps(edata), nid))
                updated_count += 1

        await db.commit()

    return {
        "status": "success",
        "total_nodes_updated": updated_count,
        "message": f"Se sincronizaron {updated_count} nodos con los datos oficiales de NetBox."
    }


@router.post("/{map_id}/sync-netbox-nodes")
async def sync_map_nodes_from_netbox(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Sincroniza las propiedades de los nodos de un mapa específico contra la información más reciente de NetBox.
    """
    await inventory_service.refresh_cache(force=True)

    dev_by_id = {d["id"]: d for d in inventory_service._devices_cache}
    dev_by_name = {d["name"].strip().lower(): d for d in inventory_service._devices_cache}

    updated_count = 0
    async with get_db_connection() as db:
        c_nodes = await db.execute("SELECT id, map_id, device_id, name, ip, device_type, site_name, extra_data FROM nodes WHERE map_id = ?", (map_id,))
        nodes_rows = await c_nodes.fetchall()

        for nr in nodes_rows:
            nid = nr["id"]
            did = nr["device_id"]
            name = nr["name"]

            matched_dev = None
            if did and did in dev_by_id:
                matched_dev = dev_by_id[did]
            elif name and name.strip().lower() in dev_by_name:
                matched_dev = dev_by_name[name.strip().lower()]

            if matched_dev:
                try:
                    edata = json.loads(nr["extra_data"]) if nr["extra_data"] else {}
                except Exception:
                    edata = {}

                edata["manufacturer"] = matched_dev.get("manufacturer") or edata.get("manufacturer", "Genérico")
                edata["model"] = matched_dev.get("model") or edata.get("model", "")
                edata["serial"] = matched_dev.get("serial") or edata.get("serial", "")
                edata["role"] = matched_dev.get("role") or matched_dev.get("device_type") or edata.get("role", "Dispositivo")
                edata["role_color"] = matched_dev.get("role_color") or edata.get("role_color", "")
                edata["status"] = matched_dev.get("status") or edata.get("status", "active")

                new_device_type = matched_dev.get("role") or nr["device_type"]
                new_name = matched_dev.get("name") or name
                new_ip = matched_dev.get("ip") or nr["ip"]
                new_did = matched_dev.get("id") or did
                new_site = matched_dev.get("site") or nr["site_name"]

                await db.execute("""
                    UPDATE nodes
                    SET device_id = ?, name = ?, ip = ?, device_type = ?, site_name = ?, extra_data = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (new_did, new_name, new_ip, new_device_type, new_site, json.dumps(edata), nid))
                updated_count += 1

        await db.commit()

    return {
        "status": "success",
        "map_id": map_id,
        "total_nodes_updated": updated_count,
        "message": f"Se sincronizaron {updated_count} nodos del mapa con NetBox."
    }

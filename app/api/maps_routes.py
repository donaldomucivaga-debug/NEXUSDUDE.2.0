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

@router.get("/{map_id}", response_model=MapDetailOut)
async def get_map_detail(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Obtiene el mapa con todos sus nodos y enlaces."""
    async with get_db_connection() as db:
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
    """Crea un nuevo mapa de topología."""
    new_id = map_data.id or f"map-{uuid.uuid4().hex[:8]}"
    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO maps (id, name, description, parent_map_id, grid_size)
            VALUES (?, ?, ?, ?, ?)
        """, (new_id, map_data.name, map_data.description, map_data.parent_map_id, map_data.grid_size))
        await db.commit()

        cursor = await db.execute("SELECT * FROM maps WHERE id = ?", (new_id,))
        m = await cursor.fetchone()
        return MapOut(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            parent_map_id=m["parent_map_id"],
            grid_size=m["grid_size"],
            created_at=str(m["created_at"]),
            updated_at=str(m["updated_at"]),
            nodes_count=0,
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
                "device_count": len(devices)
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

        # 3. Si auto_populate es True, insertar los equipos organizados jerárquicamente
        if req.auto_populate and devices:
            arranged = arrange_site_nodes(devices, start_x=80.0, start_y=80.0)
            for item in arranged:
                d = item["device"]
                nid = f"node-{uuid.uuid4().hex[:8]}"
                d_extra = json.dumps({
                    "manufacturer": d.get("manufacturer") or "Genérico",
                    "model": d.get("model") or "",
                    "serial": d.get("serial") or "",
                    "role": d.get("role") or d.get("device_type") or "Dispositivo",
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

            # 2. Poblar equipos del sitio con distribución jerárquica
            if req.auto_populate_devices and site_devices:
                arranged = arrange_site_nodes(site_devices, start_x=80.0, start_y=80.0)
                for item in arranged:
                    d = item["device"]
                    nid = f"node-{uuid.uuid4().hex[:8]}"
                    d_extra = json.dumps({
                        "manufacturer": d.get("manufacturer") or "Genérico",
                        "model": d.get("model") or "",
                        "serial": d.get("serial") or "",
                        "role": d.get("role") or d.get("device_type") or "Dispositivo",
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

            # 3. Insertar acceso directo de submapa en el mapa padre
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
    async with get_db_connection() as db:
        cursor_map = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
        m = await cursor_map.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        site_name = (req.site_name or m["name"]).strip()
        devices = await inventory_service.get_devices_by_site(site_name)
        if not devices:
            all_sites = await inventory_service.get_sites()
            for s in all_sites:
                if s["name"].lower() in site_name.lower() or site_name.lower() in s["name"].lower():
                    site_name = s["name"]
                    devices = await inventory_service.get_devices_by_site(site_name)
                    break

        if not devices:
            raise HTTPException(status_code=404, detail=f"No se encontraron dispositivos en NetBox para el sitio '{site_name}'")

        cursor_existing = await db.execute("SELECT device_id, name FROM nodes WHERE map_id = ?", (map_id,))
        existing = await cursor_existing.fetchall()
        existing_device_ids = {r["device_id"] for r in existing if r["device_id"]}
        existing_names = {r["name"].lower() for r in existing if r["name"]}

        to_insert = [d for d in devices if d.get("id") not in existing_device_ids and d.get("name", "").lower() not in existing_names]

        if not to_insert:
            return {
                "status": "info",
                "message": f"Todos los {len(devices)} equipos del sitio '{site_name}' ya están en este mapa",
                "added_count": 0,
                "site_name": site_name
            }

        arranged = arrange_site_nodes(to_insert, start_x=80.0, start_y=80.0)
        for item in arranged:
            d = item["device"]
            nid = f"node-{uuid.uuid4().hex[:8]}"
            d_extra = json.dumps({
                "manufacturer": d.get("manufacturer") or "Genérico",
                "model": d.get("model") or "",
                "serial": d.get("serial") or "",
                "role": d.get("role") or d.get("device_type") or "Dispositivo",
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
            "message": f"Se agregaron {len(to_insert)} equipos del sitio '{site_name}' al mapa",
            "added_count": len(to_insert),
            "site_name": site_name
        }

@router.put("/{map_id}", response_model=MapOut)
async def update_map(map_id: str, map_data: MapUpdate, user: Dict[str, Any] = Depends(get_current_user)):
    """Actualiza propiedades de un mapa (nombre, descripción, cuadrícula)."""
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
        m = await cursor.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        new_name = map_data.name if map_data.name is not None else m["name"]
        new_desc = map_data.description if map_data.description is not None else m["description"]
        new_grid = map_data.grid_size if map_data.grid_size is not None else m["grid_size"]

        await db.execute("""
            UPDATE maps
            SET name = ?, description = ?, grid_size = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_name, new_desc, new_grid, map_id))
        await db.commit()

        c_updated = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
        u = await c_updated.fetchone()
        return MapOut(
            id=u["id"],
            name=u["name"],
            description=u["description"],
            parent_map_id=u["parent_map_id"],
            grid_size=u["grid_size"],
            created_at=str(u["created_at"]),
            updated_at=str(u["updated_at"])
        )

@router.delete("/{map_id}")
async def delete_map(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina un mapa, sus submapas hijos recursivamente y todos sus nodos y enlaces asociados."""
    if map_id == "default-map":
        raise HTTPException(status_code=400, detail="No se puede eliminar el mapa principal del sistema")

    async with get_db_connection() as db:
        cursor = await db.execute("SELECT id FROM maps WHERE id = ?", (map_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Mapa no encontrado")

        # Recopilar todos los IDs de mapas descendientes de forma recursiva
        all_map_ids = [map_id]
        queue = [map_id]
        while queue:
            parent = queue.pop(0)
            c = await db.execute("SELECT id FROM maps WHERE parent_map_id = ?", (parent,))
            children = await c.fetchall()
            for child in children:
                cid = child["id"]
                all_map_ids.append(cid)
                queue.append(cid)

        placeholders = ",".join(["?"] * len(all_map_ids))
        await db.execute(f"DELETE FROM links WHERE map_id IN ({placeholders})", all_map_ids)
        await db.execute(f"DELETE FROM nodes WHERE map_id IN ({placeholders})", all_map_ids)

        for m_id in all_map_ids:
            await db.execute("DELETE FROM nodes WHERE device_type = 'submap' AND extra_data LIKE ?", (f'%"{m_id}"%',))

        await db.execute(f"DELETE FROM maps WHERE id IN ({placeholders})", all_map_ids)
        await db.commit()

        return {
            "status": "success",
            "message": f"Se eliminaron {len(all_map_ids)} mapa(s) correctamente",
            "deleted_ids": all_map_ids
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

@router.delete("/links/{link_id}")
async def delete_link(link_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina un enlace entre nodos."""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM links WHERE id = ?", (link_id,))
        await db.commit()
        return {"status": "success", "message": f"Enlace {link_id} eliminado"}

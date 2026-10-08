import json
import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.config import settings
from app.database import get_db_connection
from app.models import (
    MapOut, MapDetailOut, MapCreate, MapUpdate,
    NodeOut, NodeCreate, NodeUpdate,
    LinkOut, LinkCreate, LinkUpdate,
    CreateMapFromSiteRequest, PopulateMapFromSiteRequest,
    BulkCreateMapsFromSitesRequest, BulkDeleteNodesRequest,
    MapReorderRequest, MapOrderItem,
    HierarchyItem, HierarchyCreate, HierarchyUpdate, MergeDuplicatesRequest
)
from app.services.inventory_service import inventory_service
from app.services.zabbix_service import zabbix_service

router = APIRouter(prefix="/maps", tags=["Maps & Topology"])

@router.get("", response_model=List[MapOut])
async def list_maps(
    sort: str = "custom",
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Lista todos los mapas disponibles ordenados por su posición asignada o alfabéticamente (asc/desc)."""
    order_clause = "COALESCE(m.position, 0) ASC, m.created_at ASC"
    if sort == "asc":
        order_clause = "m.name COLLATE NOCASE ASC"
    elif sort == "desc":
        order_clause = "m.name COLLATE NOCASE DESC"

    async with get_db_connection() as db:
        cursor = await db.execute(f"""
            SELECT m.*,
                   (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id) as nodes_count,
                   (SELECT COUNT(*) FROM links l WHERE l.map_id = m.id) as links_count,
                   (SELECT COUNT(*) FROM map_hierarchy h WHERE h.child_map_id = m.id) as access_count
            FROM maps m
            ORDER BY {order_clause}
        """)
        rows = await cursor.fetchall()
        return [
            MapOut(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                parent_map_id=r["parent_map_id"],
                grid_size=r["grid_size"],
                position=r["position"] if "position" in r.keys() and r["position"] is not None else 0,
                netbox_site_id=r["netbox_site_id"] if "netbox_site_id" in r.keys() else None,
                site_name=r["site_name"] if "site_name" in r.keys() else None,
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
                nodes_count=r["nodes_count"],
                links_count=r["links_count"],
                access_count=max(1, r["access_count"]) if "access_count" in r.keys() and r["access_count"] is not None else 1
            )
            for r in rows
        ]

@router.get("/hierarchy", response_model=List[HierarchyItem])
async def get_map_hierarchy(user: Dict[str, Any] = Depends(get_current_user)):
    """Retorna todos los accesos en el árbol de jerarquía (N-a-N)."""
    async with get_db_connection() as db:
        cursor = await db.execute("""
            SELECT h.id, h.parent_map_id, h.child_map_id, h.alias, h.position, h.is_primary,
                   m.name as map_name, m.site_name, m.netbox_site_id,
                   (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id) as nodes_count,
                   (SELECT COUNT(*) FROM links l WHERE l.map_id = m.id) as links_count,
                   (SELECT COUNT(*) FROM map_hierarchy h2 WHERE h2.child_map_id = m.id) as access_count
            FROM map_hierarchy h
            JOIN maps m ON m.id = h.child_map_id
            ORDER BY COALESCE(h.position, 0) ASC, h.created_at ASC
        """)
        rows = await cursor.fetchall()
        return [
            HierarchyItem(
                id=r["id"],
                parent_map_id=r["parent_map_id"],
                child_map_id=r["child_map_id"],
                map_name=r["map_name"],
                alias=r["alias"],
                effective_name=r["alias"] if r["alias"] and r["alias"].strip() else r["map_name"],
                position=r["position"] if r["position"] is not None else 0,
                is_primary=bool(r["is_primary"]),
                site_name=r["site_name"],
                netbox_site_id=r["netbox_site_id"],
                nodes_count=r["nodes_count"] or 0,
                links_count=r["links_count"] or 0,
                access_count=r["access_count"] or 1
            )
            for r in rows
        ]

@router.post("/hierarchy")
async def create_hierarchy_access(
    payload: HierarchyCreate,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Crea un nuevo acceso (enlace/atajo) de un mapa dentro de una carpeta padre."""
    p_id = payload.parent_map_id if payload.parent_map_id and payload.parent_map_id != 'root' else None
    c_id = payload.child_map_id

    # Validar anti-ciclo: un mapa no puede ser hijo de sí mismo
    if p_id == c_id:
        raise HTTPException(status_code=400, detail="Un mapa no puede ser agregado dentro de sí mismo.")

    async with get_db_connection() as db:
        # Verificar existencia
        c_child = await db.execute("SELECT id, name, site_name FROM maps WHERE id = ?", (c_id,))
        child_row = await c_child.fetchone()
        if not child_row:
            raise HTTPException(status_code=404, detail="El mapa a vincular no existe.")

        # Verificar si ya existe este acceso exacto en el mismo padre
        c_exist = await db.execute("""
            SELECT id FROM map_hierarchy WHERE child_map_id = ? AND (parent_map_id = ? OR (parent_map_id IS NULL AND ? IS NULL))
        """, (c_id, p_id, p_id))
        if await c_exist.fetchone():
            raise HTTPException(status_code=400, detail="Este mapa ya tiene un acceso en esta misma carpeta.")

        # Obtener máxima posición en el padre
        c_pos = await db.execute("""
            SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM map_hierarchy
            WHERE (parent_map_id = ? OR (parent_map_id IS NULL AND ? IS NULL))
        """, (p_id, p_id))
        r_pos = await c_pos.fetchone()
        next_pos = payload.position if payload.position is not None else (r_pos["next_pos"] if r_pos else 0)

        new_h_id = f"hier-{uuid.uuid4().hex[:8]}"
        await db.execute("""
            INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, alias, position, is_primary)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (new_h_id, p_id, c_id, payload.alias, next_pos))

        # Si hay padre y se solicitó insertar submap node en su lienzo
        if p_id and payload.insert_submap_node:
            subnode_id = f"node-{uuid.uuid4().hex[:8]}"
            extra = json.dumps({
                "target_map_id": c_id,
                "role": "Submapa",
                "site_name": child_row["site_name"] or child_row["name"]
            })
            display_name = payload.alias or child_row["name"]
            await db.execute("""
                INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                VALUES (?, ?, ?, '', 'submap', ?, 120.0, 120.0, 'ok', ?)
            """, (subnode_id, p_id, f"📁 {display_name}", child_row["name"], extra))

        await db.commit()
        await ensure_map_navigation_nodes(db, c_id)
        if p_id:
            await ensure_map_navigation_nodes(db, p_id)

    return {"success": True, "id": new_h_id, "message": "Acceso creado correctamente en la jerarquía."}

@router.delete("/hierarchy/{hierarchy_id}")
async def remove_hierarchy_access(
    hierarchy_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Elimina un acceso específico del árbol sin eliminar el mapa físico fuente."""
    async with get_db_connection() as db:
        c_h = await db.execute("SELECT * FROM map_hierarchy WHERE id = ?", (hierarchy_id,))
        h_row = await c_h.fetchone()
        if not h_row:
            raise HTTPException(status_code=404, detail="Acceso de jerarquía no encontrado.")

        c_id = h_row["child_map_id"]
        p_id = h_row["parent_map_id"]

        # Contar cuántos accesos le quedan a este mapa
        c_cnt = await db.execute("SELECT COUNT(*) as count FROM map_hierarchy WHERE child_map_id = ?", (c_id,))
        cnt_row = await c_cnt.fetchone()
        remaining = (cnt_row["count"] or 0) - 1

        await db.execute("DELETE FROM map_hierarchy WHERE id = ?", (hierarchy_id,))

        # Si era el último acceso, conservar el mapa fuente moviéndolo a nivel raíz
        if remaining <= 0:
            new_root_h_id = f"hier-{uuid.uuid4().hex[:8]}"
            await db.execute("""
                INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, position, is_primary)
                VALUES (?, NULL, ?, 0, 1)
            """, (new_root_h_id, c_id))
            await db.execute("UPDATE maps SET parent_map_id = NULL WHERE id = ?", (c_id,))
        elif h_row["is_primary"]:
            # Si era el primario, designar el siguiente como primario
            await db.execute("""
                UPDATE map_hierarchy SET is_primary = 1
                WHERE id = (SELECT id FROM map_hierarchy WHERE child_map_id = ? LIMIT 1)
            """, (c_id,))

        await db.commit()
        if p_id:
            await ensure_map_navigation_nodes(db, p_id)
        await ensure_map_navigation_nodes(db, c_id)

    return {"success": True, "message": "Acceso retirado de la jerarquía."}

@router.post("/merge-duplicates")
async def merge_duplicate_netbox_maps(
    payload: MergeDuplicatesRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Detecta y fusiona mapas duplicados de NetBox:
    - Conserva como mapa maestro la copia con más enlaces y submapas.
    - Traspasa equipos y aristas faltantes al maestro.
    - Convierte la ubicación de la copia descartada en un acceso adicional en map_hierarchy.
    - Redirige todos los accesos directos de submapa hacia el maestro.
    """
    dry_run = payload.dry_run
    async with get_db_connection() as db:
        # Buscar sitios duplicados
        c_dups = await db.execute("""
            SELECT LOWER(site_name) as lower_site, COUNT(*) as c
            FROM maps WHERE site_name IS NOT NULL AND site_name != ''
            GROUP BY LOWER(site_name) HAVING c > 1
        """)
        dup_sites = [r["lower_site"] for r in await c_dups.fetchall()]

        merge_report = []

        for site in dup_sites:
            c_maps = await db.execute("""
                SELECT m.id, m.name, m.site_name, m.netbox_site_id, m.parent_map_id, m.created_at,
                       (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id AND n.device_type NOT IN ('submap', 'parent_map')) as dev_nodes,
                       (SELECT COUNT(*) FROM links l WHERE l.map_id = m.id) as link_count
                FROM maps m WHERE LOWER(m.site_name) = ?
                ORDER BY link_count DESC, dev_nodes DESC, m.created_at ASC
            """, (site,))
            candidates = [dict(r) for r in await c_maps.fetchall()]
            if len(candidates) < 2:
                continue

            master = candidates[0]
            to_merge = candidates[1:]

            site_detail = {
                "site_name": master["site_name"],
                "master_id": master["id"],
                "master_name": master["name"],
                "merged_maps": []
            }

            for disc in to_merge:
                disc_id = disc["id"]
                site_detail["merged_maps"].append({
                    "discarded_id": disc_id,
                    "discarded_name": disc["name"],
                    "parent_map_id": disc["parent_map_id"]
                })

                if not dry_run:
                    # 1. Traspasar equipos de disc que no existan en master (por device_id o IP)
                    c_m_nodes = await db.execute("SELECT device_id, ip FROM nodes WHERE map_id = ?", (master["id"],))
                    m_dev_ids = {r["device_id"] for r in await c_m_nodes.fetchall() if r["device_id"]}
                    m_ips = {r["ip"] for r in await c_m_nodes.fetchall() if r["ip"]}

                    c_d_nodes = await db.execute("""
                        SELECT * FROM nodes WHERE map_id = ? AND device_type NOT IN ('submap', 'parent_map')
                    """, (disc_id,))
                    for dn in await c_d_nodes.fetchall():
                        if (dn["device_id"] and dn["device_id"] in m_dev_ids) or (dn["ip"] and dn["ip"] in m_ips):
                            continue
                        new_nid = f"node-{uuid.uuid4().hex[:8]}"
                        await db.execute("""
                            INSERT INTO nodes (id, map_id, device_id, name, ip, device_type, site_name, x, y, status, extra_data)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (new_nid, master["id"], dn["device_id"], dn["name"], dn["ip"], dn["device_type"], dn["site_name"], dn["x"], dn["y"], dn["status"], dn["extra_data"]))

                    # 2. Convertir la ubicación del mapa descartado en un acceso a master en map_hierarchy
                    if disc["parent_map_id"] and disc["parent_map_id"] != master["parent_map_id"]:
                        c_chk = await db.execute("""
                            SELECT id FROM map_hierarchy WHERE child_map_id = ? AND parent_map_id = ?
                        """, (master["id"], disc["parent_map_id"]))
                        if not await c_chk.fetchone():
                            h_id = f"hier-{uuid.uuid4().hex[:8]}"
                            await db.execute("""
                                INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, position, is_primary)
                                VALUES (?, ?, ?, 0, 0)
                            """, (h_id, disc["parent_map_id"], master["id"]))

                    # 3. Redirigir nodos de submapas que apuntaban al mapa descartado hacia el master
                    c_refs = await db.execute("""
                        SELECT id, extra_data FROM nodes WHERE device_type = 'submap' AND extra_data LIKE ?
                    """, (f'%"{disc_id}"%',))
                    for ref in await c_refs.fetchall():
                        try:
                            ex = json.loads(ref["extra_data"])
                            if ex.get("target_map_id") == disc_id:
                                ex["target_map_id"] = master["id"]
                                await db.execute("UPDATE nodes SET extra_data = ? WHERE id = ?", (json.dumps(ex), ref["id"]))
                        except Exception:
                            pass

                    # 4. Eliminar el mapa descartado
                    await db.execute("DELETE FROM map_hierarchy WHERE child_map_id = ?", (disc_id,))
                    await db.execute("DELETE FROM links WHERE map_id = ?", (disc_id,))
                    await db.execute("DELETE FROM nodes WHERE map_id = ?", (disc_id,))
                    await db.execute("DELETE FROM maps WHERE id = ?", (disc_id,))

            merge_report.append(site_detail)

        if not dry_run:
            await db.commit()

        return {
            "success": True,
            "dry_run": dry_run,
            "merged_count": len(merge_report),
            "report": merge_report
        }

@router.post("/reorder")
async def reorder_maps(
    payload: MapReorderRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """Actualiza la posición (orden en la lista) y opcionalmente el parent_map_id de los mapas/submapas."""
    async with get_db_connection() as db:
        for item in payload.items:
            if item.parent_map_id is not None:
                p_id = item.parent_map_id if item.parent_map_id and item.parent_map_id != 'root' else None
                await db.execute(
                    "UPDATE maps SET position = ?, parent_map_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (item.position, p_id, item.id)
                )
                # Actualizar también acceso primario en map_hierarchy
                await db.execute("""
                    UPDATE map_hierarchy SET position = ?, parent_map_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE child_map_id = ? AND is_primary = 1
                """, (item.position, p_id, item.id))
            else:
                await db.execute(
                    "UPDATE maps SET position = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (item.position, item.id)
                )
                await db.execute("""
                    UPDATE map_hierarchy SET position = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE child_map_id = ? AND is_primary = 1
                """, (item.position, item.id))
        await db.commit()
    return {"success": True, "message": f"{len(payload.items)} mapas reordenados correctamente."}

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
        c_maps = await db.execute("SELECT id, name, site_name, netbox_site_id FROM maps")
        existing_maps_rows = await c_maps.fetchall()
        existing_by_site_id = {r["netbox_site_id"]: r["id"] for r in existing_maps_rows if "netbox_site_id" in r.keys() and r["netbox_site_id"]}
        existing_by_site_name = {r["site_name"].strip().lower(): r["id"] for r in existing_maps_rows if "site_name" in r.keys() and r["site_name"]}
        existing_map_names = {r["name"].strip().lower(): r["id"] for r in existing_maps_rows}

    status_list = []
    mapped_count = 0
    unmapped_count = 0

    for s in sites_summary:
        s_id = s.get("id")
        s_name = (s.get("name") or "").strip()
        s_lower = s_name.lower()
        map_id = existing_by_site_id.get(s_id) or existing_by_site_name.get(s_lower) or existing_map_names.get(s_lower)
        has_map = bool(map_id)
        if has_map:
            mapped_count += 1
        else:
            unmapped_count += 1

        status_list.append({
            "site_id": s_id,
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
    # Considera tanto mapas con parent_map_id como accesos explícitos en map_hierarchy
    c_children = await db.execute("""
        SELECT DISTINCT m.id,
               COALESCE(h.alias, m.name) as name,
               m.description
        FROM maps m
        LEFT JOIN map_hierarchy h ON h.child_map_id = m.id AND h.parent_map_id = ?
        WHERE m.parent_map_id = ? OR h.parent_map_id = ?
    """, (map_id, map_id, map_id))
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
            keys = r.keys()
            links.append(LinkOut(
                id=r["id"],
                map_id=r["map_id"],
                source_node_id=r["source_node_id"],
                target_node_id=r["target_node_id"],
                source_interface=r["source_interface"],
                target_interface=r["target_interface"],
                source_interface_id=r["source_interface_id"] if "source_interface_id" in keys else None,
                target_interface_id=r["target_interface_id"] if "target_interface_id" in keys else None,
                netbox_cable_id=r["netbox_cable_id"] if "netbox_cable_id" in keys else None,
                cable_type=r["cable_type"] if "cable_type" in keys else "cat6",
                cable_status=r["cable_status"] if "cable_status" in keys else "connected",
                zabbix_src_interface=r["zabbix_src_interface"] if "zabbix_src_interface" in keys else None,
                zabbix_tgt_interface=r["zabbix_tgt_interface"] if "zabbix_tgt_interface" in keys else None,
                status=r["status"],
                rtt_ms=r["rtt_ms"],
                loss_percent=r["loss_percent"],
                extra_data=extra,
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"])
            ))

        keys = m.keys()
        return MapDetailOut(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            parent_map_id=m["parent_map_id"],
            grid_size=m["grid_size"],
            position=m["position"] if "position" in keys and m["position"] is not None else 0,
            netbox_site_id=m["netbox_site_id"] if "netbox_site_id" in keys else None,
            site_name=m["site_name"] if "site_name" in keys else None,
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

    # Relación con Sitio NetBox (Ninguna por defecto)
    site_name = map_data.site_name.strip() if map_data.site_name and map_data.site_name.strip() else None
    netbox_site_id = map_data.netbox_site_id

    if site_name and not netbox_site_id:
        sites = await inventory_service.get_sites()
        for s in sites:
            if s["name"].lower() == site_name.lower():
                netbox_site_id = s["id"]
                site_name = s["name"]
                break

    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO maps (id, name, description, parent_map_id, grid_size, netbox_site_id, site_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (new_id, map_data.name, map_data.description, parent_id, map_data.grid_size or 20, netbox_site_id, site_name))

        # Registrar acceso primario en map_hierarchy
        h_id = f"hier-{uuid.uuid4().hex[:8]}"
        await db.execute("""
            INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, position, is_primary)
            VALUES (?, ?, ?, 0, 1)
        """, (h_id, parent_id, new_id))
        await db.commit()

        # Si se solicitó insertar submap node en el padre
        if map_data.insert_submap_node and parent_id:
            subnode_id = f"node-{uuid.uuid4().hex[:8]}"
            sub_extra = json.dumps({
                "target_map_id": new_id,
                "role": "Submapa",
                "site_name": site_name or map_data.name
            })
            await db.execute("""
                INSERT INTO nodes (id, map_id, name, ip, device_type, site_name, x, y, status, extra_data)
                VALUES (?, ?, ?, '', 'submap', ?, 100.0, 100.0, 'ok', ?)
            """, (subnode_id, parent_id, f"📁 {map_data.name}", map_data.description or map_data.name, sub_extra))
            await db.commit()

        # Si se solicitó auto_populate y se especificó un sitio
        if map_data.auto_populate and site_name:
            devices = await inventory_service.get_devices_by_site(site_name)
            if devices:
                devices_start_y = 170.0 if parent_id else 80.0
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
                        nid, new_id, d.get("id"), d.get("name"), d.get("ip") or "",
                        d.get("role") or "generic", site_name, item["x"], item["y"], "ok", d_extra
                    ))
                await db.commit()

        # Sincronizar automáticamente navegación en el nuevo mapa y en el padre
        await ensure_map_navigation_nodes(db, new_id)
        if parent_id:
            await ensure_map_navigation_nodes(db, parent_id)

        cursor = await db.execute("""
            SELECT m.*, (SELECT COUNT(*) FROM nodes n WHERE n.map_id = m.id) as nodes_count,
                   (SELECT COUNT(*) FROM links l WHERE l.map_id = m.id) as links_count
            FROM maps m WHERE m.id = ?
        """, (new_id,))
        m = await cursor.fetchone()
        keys = m.keys()
        return MapOut(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            parent_map_id=m["parent_map_id"],
            grid_size=m["grid_size"],
            position=m["position"] if "position" in keys and m["position"] is not None else 0,
            netbox_site_id=m["netbox_site_id"] if "netbox_site_id" in keys else None,
            site_name=m["site_name"] if "site_name" in keys else None,
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
    """Actualiza propiedades de un mapa (nombre, descripción, cuadrícula, mapa padre, sitio NetBox) y sincroniza jerarquía."""
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

        # Actualización de relación con Sitio NetBox
        new_site_id = m["netbox_site_id"] if "netbox_site_id" in m.keys() else None
        new_site_name = m["site_name"] if "site_name" in m.keys() else None

        fields_set = getattr(map_data, "model_fields_set", getattr(map_data, "__fields_set__", set()))
        if "site_name" in fields_set:
            raw_s = map_data.site_name.strip() if map_data.site_name else None
            new_site_name = raw_s if raw_s else None
            if not new_site_name:
                new_site_id = None
        if "netbox_site_id" in fields_set:
            new_site_id = map_data.netbox_site_id
            if new_site_id is None and "site_name" not in fields_set:
                new_site_name = None

        if new_site_name and not new_site_id:
            sites = await inventory_service.get_sites()
            for s in sites:
                if s["name"].lower() == new_site_name.lower():
                    new_site_id = s["id"]
                    new_site_name = s["name"]
                    break

        await db.execute("""
            UPDATE maps
            SET name = ?, description = ?, grid_size = ?, parent_map_id = ?,
                netbox_site_id = ?, site_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_name, new_desc, new_grid, new_parent_id, new_site_id, new_site_name, map_id))
        await db.commit()

        # Si se solicitó auto_populate y se especificó un sitio
        if map_data.auto_populate and new_site_name:
            devices = await inventory_service.get_devices_by_site(new_site_name)
            if devices:
                c_ex = await db.execute("SELECT device_id, name FROM nodes WHERE map_id = ?", (map_id,))
                ex_nodes = await c_ex.fetchall()
                ex_dev_ids = {r["device_id"] for r in ex_nodes if r["device_id"]}
                ex_names = {r["name"].lower() for r in ex_nodes if r["name"]}
                to_insert = [d for d in devices if d.get("id") not in ex_dev_ids and d.get("name", "").lower() not in ex_names]
                if to_insert:
                    start_y = 170.0 if new_parent_id else 80.0
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
                            d.get("role") or "generic", new_site_name, item["x"], item["y"], "ok", d_extra
                        ))
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
        keys = u.keys()
        return MapOut(
            id=u["id"],
            name=u["name"],
            description=u["description"],
            parent_map_id=u["parent_map_id"],
            grid_size=u["grid_size"],
            position=u["position"] if "position" in keys and u["position"] is not None else 0,
            netbox_site_id=u["netbox_site_id"] if "netbox_site_id" in keys else None,
            site_name=u["site_name"] if "site_name" in keys else None,
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


# --- Endpoints de Enlaces con Sincronización Bidireccional NetBox ---

@router.post("/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
async def create_link(link_data: LinkCreate, user: Dict[str, Any] = Depends(get_current_user)):
    """Crea un enlace entre dos nodos y sincroniza el cable físico correspondiente en NetBox si ambos nodos son dispositivos."""
    if not getattr(settings, "ALLOW_EDGE_EDITING", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La creación y edición de aristas está deshabilitada globalmente por el nivel de Soporte en NetBox."
        )
    link_id = f"link-{uuid.uuid4().hex[:8]}"
    extra_data = link_data.extra_data or {}

    netbox_cable_id = link_data.netbox_cable_id
    src_if_id = link_data.source_interface_id
    tgt_if_id = link_data.target_interface_id
    cable_type = link_data.cable_type or "cat6"
    cable_status = link_data.cable_status or "connected"

    async with get_db_connection() as db:
        # 1. Obtener los nodos para verificar si corresponden a dispositivos de NetBox
        c_nodes = await db.execute("SELECT id, device_id, name FROM nodes WHERE id IN (?, ?)", (link_data.source_node_id, link_data.target_node_id))
        nodes_rows = await c_nodes.fetchall()
        node_map = {n["id"]: n for n in nodes_rows}

        src_node = node_map.get(link_data.source_node_id)
        tgt_node = node_map.get(link_data.target_node_id)

        # 2. Si ambos nodos tienen device_id en NetBox y se especificaron interfaces, crear el cable en NetBox
        if src_node and tgt_node and src_node["device_id"] and tgt_node["device_id"] and link_data.source_interface and link_data.target_interface and not netbox_cable_id:
            try:
                cable_res = await inventory_service.create_netbox_cable(
                    dev1_id=src_node["device_id"],
                    if1_name=link_data.source_interface,
                    dev2_id=tgt_node["device_id"],
                    if2_name=link_data.target_interface,
                    cable_type=cable_type,
                    description=f"Enlace creado desde NexusDude (Mapa: {link_data.map_id})"
                )
                netbox_cable_id = cable_res.get("cable_id")
                src_if_id = cable_res.get("if1_id")
                tgt_if_id = cable_res.get("if2_id")
                cable_status = cable_res.get("cable_status", cable_status)
                cable_type = cable_res.get("cable_type", cable_type)
            except Exception as e:
                # Si NetBox falla (ej. puerto ya cableado), registramos el warning pero permitimos guardar el enlace lógico con aviso
                extra_data["netbox_sync_warning"] = str(e)
        elif not (link_data.source_interface and link_data.target_interface):
            # Notificación de importancia de documentación de puertos físicos
            extra_data["port_doc_notice"] = "Es importante documentar el puerto físico exacto en NetBox para un inventario completo."

        extra_str = json.dumps(extra_data) if extra_data else None

        await db.execute("""
            INSERT INTO links (
                id, map_id, source_node_id, target_node_id,
                source_interface, target_interface,
                source_interface_id, target_interface_id,
                netbox_cable_id, cable_type, cable_status,
                zabbix_src_interface, zabbix_tgt_interface,
                status, rtt_ms, loss_percent, extra_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            link_id, link_data.map_id, link_data.source_node_id, link_data.target_node_id,
            link_data.source_interface, link_data.target_interface,
            src_if_id, tgt_if_id,
            netbox_cable_id, cable_type, cable_status,
            link_data.zabbix_src_interface, link_data.zabbix_tgt_interface,
            link_data.status or "ok", link_data.rtt_ms or 0.0, link_data.loss_percent or 0.0, extra_str
        ))
        await db.commit()

        cursor = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
        r = await cursor.fetchone()
        keys = r.keys()
        return LinkOut(
            id=r["id"],
            map_id=r["map_id"],
            source_node_id=r["source_node_id"],
            target_node_id=r["target_node_id"],
            source_interface=r["source_interface"],
            target_interface=r["target_interface"],
            source_interface_id=r["source_interface_id"] if "source_interface_id" in keys else None,
            target_interface_id=r["target_interface_id"] if "target_interface_id" in keys else None,
            netbox_cable_id=r["netbox_cable_id"] if "netbox_cable_id" in keys else None,
            cable_type=r["cable_type"] if "cable_type" in keys else "cat6",
            cable_status=r["cable_status"] if "cable_status" in keys else "connected",
            zabbix_src_interface=r["zabbix_src_interface"] if "zabbix_src_interface" in keys else None,
            zabbix_tgt_interface=r["zabbix_tgt_interface"] if "zabbix_tgt_interface" in keys else None,
            status=r["status"],
            rtt_ms=r["rtt_ms"],
            loss_percent=r["loss_percent"],
            extra_data=json.loads(r["extra_data"]) if r["extra_data"] else None,
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"])
        )

@router.put("/links/{link_id}", response_model=LinkOut)
async def update_link(link_id: str, link_data: LinkUpdate, user: Dict[str, Any] = Depends(get_current_user)):
    """Actualiza propiedades de un enlace (interfaces, telemetría Zabbix, dirección de servicio, extra_data)."""
    if not getattr(settings, "ALLOW_EDGE_EDITING", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La creación y edición de aristas está deshabilitada globalmente por el nivel de Soporte en NetBox."
        )
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
        r = await cursor.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Enlace no encontrado")

        keys = r.keys()
        new_src_iface = link_data.source_interface if link_data.source_interface is not None else r["source_interface"]
        new_tgt_iface = link_data.target_interface if link_data.target_interface is not None else r["target_interface"]
        new_src_if_id = link_data.source_interface_id if link_data.source_interface_id is not None else (r["source_interface_id"] if "source_interface_id" in keys else None)
        new_tgt_if_id = link_data.target_interface_id if link_data.target_interface_id is not None else (r["target_interface_id"] if "target_interface_id" in keys else None)
        new_cable_id = link_data.netbox_cable_id if link_data.netbox_cable_id is not None else (r["netbox_cable_id"] if "netbox_cable_id" in keys else None)
        new_cable_type = link_data.cable_type if link_data.cable_type is not None else (r["cable_type"] if "cable_type" in keys else "cat6")
        new_cable_status = link_data.cable_status if link_data.cable_status is not None else (r["cable_status"] if "cable_status" in keys else "connected")
        new_zbx_src = link_data.zabbix_src_interface if link_data.zabbix_src_interface is not None else (r["zabbix_src_interface"] if "zabbix_src_interface" in keys else None)
        new_zbx_tgt = link_data.zabbix_tgt_interface if link_data.zabbix_tgt_interface is not None else (r["zabbix_tgt_interface"] if "zabbix_tgt_interface" in keys else None)
        new_status = link_data.status if link_data.status is not None else r["status"]
        new_rtt = link_data.rtt_ms if link_data.rtt_ms is not None else r["rtt_ms"]
        new_loss = link_data.loss_percent if link_data.loss_percent is not None else r["loss_percent"]

        cur_extra = json.loads(r["extra_data"]) if r["extra_data"] else {}
        if link_data.extra_data is not None:
            cur_extra.update(link_data.extra_data)

        await db.execute("""
            UPDATE links
            SET source_interface = ?, target_interface = ?,
                source_interface_id = ?, target_interface_id = ?,
                netbox_cable_id = ?, cable_type = ?, cable_status = ?,
                zabbix_src_interface = ?, zabbix_tgt_interface = ?,
                status = ?, rtt_ms = ?, loss_percent = ?,
                extra_data = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            new_src_iface, new_tgt_iface,
            new_src_if_id, new_tgt_if_id,
            new_cable_id, new_cable_type, new_cable_status,
            new_zbx_src, new_zbx_tgt,
            new_status, new_rtt, new_loss,
            json.dumps(cur_extra), link_id
        ))
        await db.commit()

        c_u = await db.execute("SELECT * FROM links WHERE id = ?", (link_id,))
        u = await c_u.fetchone()
        u_keys = u.keys()
        return LinkOut(
            id=u["id"],
            map_id=u["map_id"],
            source_node_id=u["source_node_id"],
            target_node_id=u["target_node_id"],
            source_interface=u["source_interface"],
            target_interface=u["target_interface"],
            source_interface_id=u["source_interface_id"] if "source_interface_id" in u_keys else None,
            target_interface_id=u["target_interface_id"] if "target_interface_id" in u_keys else None,
            netbox_cable_id=u["netbox_cable_id"] if "netbox_cable_id" in u_keys else None,
            cable_type=u["cable_type"] if "cable_type" in u_keys else "cat6",
            cable_status=u["cable_status"] if "cable_status" in u_keys else "connected",
            zabbix_src_interface=u["zabbix_src_interface"] if "zabbix_src_interface" in u_keys else None,
            zabbix_tgt_interface=u["zabbix_tgt_interface"] if "zabbix_tgt_interface" in u_keys else None,
            status=u["status"],
            rtt_ms=u["rtt_ms"],
            loss_percent=u["loss_percent"],
            extra_data=json.loads(u["extra_data"]) if u["extra_data"] else None,
            created_at=str(u["created_at"]),
            updated_at=str(u["updated_at"])
        )

@router.delete("/links/{link_id}")
async def delete_link(link_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina un enlace entre nodos y desconecta el cable correspondiente en NetBox si existía."""
    if not getattr(settings, "ALLOW_EDGE_EDITING", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La creación y edición de aristas está deshabilitada globalmente por el nivel de Soporte en NetBox."
        )
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT netbox_cable_id FROM links WHERE id = ?", (link_id,))
        r = await cursor.fetchone()
        if r and "netbox_cable_id" in r.keys() and r["netbox_cable_id"]:
            cable_id = r["netbox_cable_id"]
            await inventory_service.delete_netbox_cable(cable_id)

        await db.execute("DELETE FROM links WHERE id = ?", (link_id,))
        await db.commit()
        return {"status": "success", "message": f"Enlace {link_id} eliminado de NexusDude y NetBox"}

@router.post("/{map_id}/sync-netbox-cables")
async def sync_map_netbox_cables(map_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """
    Escanea todos los nodos de este mapa que tengan device_id de NetBox y sincroniza
    automáticamente los cables físicos y conexiones de pares existentes en NetBox hacia enlaces de NexusDude.
    """
    async with get_db_connection() as db:
        c_nodes = await db.execute("SELECT id, device_id, name FROM nodes WHERE map_id = ? AND device_id IS NOT NULL", (map_id,))
        nodes = await c_nodes.fetchall()
        if not nodes:
            return {"status": "success", "synced_cables_count": 0, "message": "No hay dispositivos de NetBox en este mapa"}

        dev_to_node = {n["device_id"]: n["id"] for n in nodes}
        device_ids = list(dev_to_node.keys())

        # Enlaces existentes en este mapa
        c_links = await db.execute("SELECT netbox_cable_id, source_node_id, target_node_id, source_interface, target_interface FROM links WHERE map_id = ?", (map_id,))
        existing_links = await c_links.fetchall()
        existing_cable_ids = {l["netbox_cable_id"] for l in existing_links if "netbox_cable_id" in l.keys() and l["netbox_cable_id"]}
        existing_pairs = {f"{min(l['source_node_id'], l['target_node_id'])}_{max(l['source_node_id'], l['target_node_id'])}" for l in existing_links}

        synced_count = 0
        for dev_id in device_ids:
            ifaces = await inventory_service.get_device_interfaces(dev_id)
            for iface in ifaces:
                cable_id = iface.get("cable_id")
                peer_dev_id = iface.get("connected_device_id")

                if cable_id and cable_id not in existing_cable_ids:
                    # Verificar si la otra punta está en este mismo mapa
                    # Consultar el cable en NetBox para obtener endpoints
                    headers = await inventory_service.get_headers()
                    try:
                        import httpx
                        from app.config import settings
                        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
                            res_cable = await client.get(f"{settings.NETBOX_URL}/api/dcim/cables/{cable_id}/", headers=headers)
                            if res_cable.status_code == 200:
                                cdata = res_cable.json()
                                a_terms = cdata.get("a_terminations", [])
                                b_terms = cdata.get("b_terminations", [])
                                if a_terms and b_terms:
                                    dev_a_id = a_terms[0].get("object", {}).get("device", {}).get("id")
                                    if_a_name = a_terms[0].get("object", {}).get("name")
                                    if_a_id = a_terms[0].get("object_id")
                                    dev_b_id = b_terms[0].get("object", {}).get("device", {}).get("id")
                                    if_b_name = b_terms[0].get("object", {}).get("name")
                                    if_b_id = b_terms[0].get("object_id")

                                    if dev_a_id in dev_to_node and dev_b_id in dev_to_node:
                                        node_a_id = dev_to_node[dev_a_id]
                                        node_b_id = dev_to_node[dev_b_id]
                                        pair_key = f"{min(node_a_id, node_b_id)}_{max(node_a_id, node_b_id)}"
                                        new_link_id = f"link-{uuid.uuid4().hex[:8]}"
                                        await db.execute("""
                                            INSERT INTO links (
                                                id, map_id, source_node_id, target_node_id,
                                                source_interface, target_interface,
                                                source_interface_id, target_interface_id,
                                                netbox_cable_id, cable_type, cable_status, status
                                            )
                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                        """, (
                                            new_link_id, map_id, node_a_id, node_b_id,
                                            if_a_name, if_b_name,
                                            if_a_id, if_b_id,
                                            cable_id, cdata.get("type", {}).get("value") if isinstance(cdata.get("type"), dict) else "cat6",
                                            cdata.get("status", {}).get("value") if isinstance(cdata.get("status"), dict) else "connected",
                                            "ok"
                                        ))
                                        existing_cable_ids.add(cable_id)
                                        existing_pairs.add(pair_key)
                                        synced_count += 1
                    except Exception as e:
                        pass
                elif peer_dev_id and peer_dev_id in dev_to_node:
                    # Enlace unidireccional / par directo conectado en NetBox
                    node_a_id = dev_to_node[dev_id]
                    node_b_id = dev_to_node[peer_dev_id]
                    pair_key = f"{min(node_a_id, node_b_id)}_{max(node_a_id, node_b_id)}"
                    if pair_key not in existing_pairs:
                        new_link_id = f"link-{uuid.uuid4().hex[:8]}"
                        extra_data = {"port_doc_notice": "Conexión de punto final detectada desde NetBox."}
                        await db.execute("""
                            INSERT INTO links (
                                id, map_id, source_node_id, target_node_id,
                                source_interface, target_interface,
                                source_interface_id, target_interface_id,
                                netbox_cable_id, cable_type, cable_status, status, extra_data
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            new_link_id, map_id, node_a_id, node_b_id,
                            iface.get("name") or "", iface.get("connected_interface") or "",
                            iface.get("id"), iface.get("connected_interface_id"),
                            None, iface.get("cable_type") or "cat6",
                            "connected", "ok", json.dumps(extra_data)
                        ))
                        existing_pairs.add(pair_key)
                        synced_count += 1

        await db.commit()
        return {"status": "success", "synced_cables_count": synced_count, "message": f"Se sincronizaron {synced_count} cables y conexiones desde NetBox"}


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

            # Saltar nodos de navegación y notas (no son dispositivos reales de NetBox)
            if nr["device_type"] in ("submap", "parent_map", "note"):
                continue

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

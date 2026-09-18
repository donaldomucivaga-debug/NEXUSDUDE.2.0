import json
import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth import get_current_user
from app.database import get_db_connection
from app.models import RoleOut, RoleCreate, RolePermissions, UserRoleUpdate

router = APIRouter(prefix="/rbac", tags=["Role-Based Access Control"])

@router.get("/roles", response_model=List[RoleOut])
async def list_roles(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista todos los roles y sus permisos configurados."""
    async with get_db_connection() as db:
        c = await db.execute("SELECT * FROM roles ORDER BY created_at ASC")
        rows = await c.fetchall()
        result = []
        for r in rows:
            perms_dict = json.loads(r["permissions"]) if r["permissions"] else {}
            result.append(RoleOut(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                is_system=bool(r["is_system"]),
                permissions=RolePermissions(**perms_dict),
                created_at=str(r["created_at"])
            ))
        return result

@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(role_data: RoleCreate, user: Dict[str, Any] = Depends(get_current_user)):
    """Crea un nuevo rol personalizado con permisos a medida."""
    # Verificar si el usuario actual puede gestionar usuarios/roles
    if not user.get("permissions", {}).get("can_manage_users", True):
        raise HTTPException(status_code=403, detail="No tienes permisos para crear roles.")

    role_id = f"role-{uuid.uuid4().hex[:8]}"
    perms_json = json.dumps(role_data.permissions.dict())

    async with get_db_connection() as db:
        c_exist = await db.execute("SELECT id FROM roles WHERE LOWER(name) = ?", (role_data.name.strip().lower(),))
        if await c_exist.fetchone():
            raise HTTPException(status_code=400, detail=f"Ya existe un rol con el nombre '{role_data.name}'")

        await db.execute("""
            INSERT INTO roles (id, name, description, is_system, permissions)
            VALUES (?, ?, ?, 0, ?)
        """, (role_id, role_data.name.strip(), role_data.description, perms_json))
        await db.commit()

        c_new = await db.execute("SELECT * FROM roles WHERE id = ?", (role_id,))
        r = await c_new.fetchone()
        return RoleOut(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            is_system=False,
            permissions=RolePermissions(**json.loads(r["permissions"])),
            created_at=str(r["created_at"])
        )

@router.put("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: str, role_data: RoleCreate, user: Dict[str, Any] = Depends(get_current_user)):
    """Actualiza permisos o descripción de un rol."""
    if not user.get("permissions", {}).get("can_manage_users", True):
        raise HTTPException(status_code=403, detail="No tienes permisos para modificar roles.")

    perms_json = json.dumps(role_data.permissions.dict())
    async with get_db_connection() as db:
        c = await db.execute("SELECT * FROM roles WHERE id = ?", (role_id,))
        r = await c.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Rol no encontrado.")

        await db.execute("""
            UPDATE roles
            SET name = ?, description = ?, permissions = ?
            WHERE id = ?
        """, (role_data.name.strip(), role_data.description, perms_json, role_id))
        await db.commit()

        c_up = await db.execute("SELECT * FROM roles WHERE id = ?", (role_id,))
        u = await c_up.fetchone()
        return RoleOut(
            id=u["id"],
            name=u["name"],
            description=u["description"],
            is_system=bool(u["is_system"]),
            permissions=RolePermissions(**json.loads(u["permissions"])),
            created_at=str(u["created_at"])
        )

@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    """Elimina un rol personalizado."""
    if not user.get("permissions", {}).get("can_manage_users", True):
        raise HTTPException(status_code=403, detail="No tienes permisos para eliminar roles.")

    async with get_db_connection() as db:
        c = await db.execute("SELECT * FROM roles WHERE id = ?", (role_id,))
        r = await c.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Rol no encontrado.")
        if r["is_system"]:
            raise HTTPException(status_code=400, detail="No se pueden eliminar los roles del sistema.")

        await db.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        await db.commit()
        return {"status": "success", "message": f"Rol '{r['name']}' eliminado correctamente."}

@router.get("/users")
async def list_user_assignments(user: Dict[str, Any] = Depends(get_current_user)):
    """Lista las asignaciones de roles a usuarios."""
    async with get_db_connection() as db:
        c = await db.execute("""
            SELECT ur.username, ur.role_id, r.name as role_name, ur.custom_permissions, r.permissions as role_permissions, ur.updated_at
            FROM user_roles ur
            LEFT JOIN roles r ON ur.role_id = r.id
            ORDER BY ur.updated_at DESC
        """)
        rows = await c.fetchall()
        result = []
        for r in rows:
            raw_perms = r["custom_permissions"] or r["role_permissions"]
            perms = json.loads(raw_perms) if raw_perms else {}
            result.append({
                "username": r["username"],
                "role_id": r["role_id"],
                "role_name": r["role_name"],
                "permissions": perms,
                "updated_at": str(r["updated_at"])
            })
        return result

@router.post("/users/{username}/role")
async def assign_user_role(username: str, data: UserRoleUpdate, user: Dict[str, Any] = Depends(get_current_user)):
    """Asigna un rol y permisos opcionales a un usuario especificado."""
    if not user.get("permissions", {}).get("can_manage_users", True):
        raise HTTPException(status_code=403, detail="No tienes permisos para asignar roles a usuarios.")

    async with get_db_connection() as db:
        c_role = await db.execute("SELECT id FROM roles WHERE id = ?", (data.role,))
        if not await c_role.fetchone():
            raise HTTPException(status_code=404, detail=f"Rol '{data.role}' no existe.")

        custom_json = json.dumps(data.custom_permissions.dict()) if data.custom_permissions else None
        await db.execute("""
            INSERT INTO user_roles (username, role_id, custom_permissions, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                role_id = excluded.role_id,
                custom_permissions = excluded.custom_permissions,
                updated_at = CURRENT_TIMESTAMP
        """, (username.strip(), data.role, custom_json))
        await db.commit()

        return {"status": "success", "message": f"Rol '{data.role}' asignado a usuario '{username}' correctamente."}

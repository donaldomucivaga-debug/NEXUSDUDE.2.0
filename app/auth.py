import jwt
from typing import Optional, Dict, Any
from fastapi import Header, Query, Cookie, HTTPException, status, Depends
import logging
from app.config import settings

logger = logging.getLogger("nexusdude.auth")

def extract_token(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    auth_token: Optional[str] = Cookie(None)
) -> Optional[str]:
    """Extrae el token JWT de encabezado Authorization, query param o cookie."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ")[1].strip()
    if token:
        return token.strip()
    if auth_token:
        return auth_token.strip()
    return None

def verify_nexus_jwt(token: str) -> Dict[str, Any]:
    """Valida la firma y expiración del JWT generado por Nexus Orchestrator."""
    try:
        payload = jwt.decode(
            token,
            settings.NEXUS_JWT_SECRET,
            algorithms=["HS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado. Por favor inicie sesión nuevamente en Nexus Orchestrator.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Error decodificando JWT: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación inválido o no autorizado.",
            headers={"WWW-Authenticate": "Bearer"}
        )

async def get_current_user(
    token: Optional[str] = Depends(extract_token)
) -> Dict[str, Any]:
    """Dependencia FastAPI para proteger endpoints con verificación de roles RBAC."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó token de autenticación (SSO Nexus requerido).",
            headers={"WWW-Authenticate": "Bearer"}
        )
    payload = verify_nexus_jwt(token)
    username = payload.get("user") or payload.get("username")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no contiene información válida de usuario."
        )

    role_name = payload.get("role", "admin")
    permissions = {
        "can_view_inventory": True,
        "can_edit_topology": True,
        "can_manage_submaps": True,
        "can_view_zabbix": True,
        "can_access_servers": True,
        "can_manage_users": True
    }

    try:
        from app.database import get_db_connection
        import json
        async with get_db_connection() as db:
            # Buscar asignación de rol personalizada en DB
            c_user = await db.execute("""
                SELECT ur.role_id, ur.custom_permissions, r.permissions as role_permissions
                FROM user_roles ur
                LEFT JOIN roles r ON ur.role_id = r.id
                WHERE ur.username = ?
            """, (username,))
            user_row = await c_user.fetchone()

            if user_row:
                role_name = user_row["role_id"]
                raw_perms = user_row["custom_permissions"] or user_row["role_permissions"]
                if raw_perms:
                    permissions = json.loads(raw_perms)
            else:
                # Si no está en user_roles, buscar permisos del rol indicado en el JWT
                c_role = await db.execute("SELECT permissions FROM roles WHERE id = ?", (role_name,))
                r_row = await c_role.fetchone()
                if r_row and r_row["permissions"]:
                    permissions = json.loads(r_row["permissions"])
    except Exception as e:
        logger.warning(f"Error consultando permisos de usuario '{username}' en DB: {e}")

    return {
        "username": username,
        "role": role_name,
        "permissions": permissions,
        "exp": payload.get("exp")
    }

async def get_optional_user(
    token: Optional[str] = Depends(extract_token)
) -> Optional[Dict[str, Any]]:
    """Dependencia FastAPI para endpoints que permiten modo anónimo o autenticado."""
    if not token:
        return None
    try:
        return await get_current_user(token)
    except HTTPException:
        return None

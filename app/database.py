import os
import aiosqlite
import logging
from contextlib import asynccontextmanager
from app.config import settings

logger = logging.getLogger("nexusdude.db")

@asynccontextmanager
async def get_db_connection():
    os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA foreign_keys=ON;")
        yield db

async def init_db():
    """Inicializa el esquema SQLite para NexusDude."""
    os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
    async with get_db_connection() as db:
        # Tabla de Mapas (Lienzos)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS maps (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                parent_map_id TEXT,
                grid_size INTEGER DEFAULT 20,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabla de Nodos (Dispositivos / Elementos en el lienzo)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                map_id TEXT NOT NULL,
                device_id INTEGER,
                name TEXT NOT NULL,
                ip TEXT,
                device_type TEXT DEFAULT 'generic',
                site_name TEXT,
                x REAL NOT NULL DEFAULT 100.0,
                y REAL NOT NULL DEFAULT 100.0,
                status TEXT DEFAULT 'unknown',
                extra_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (map_id) REFERENCES maps(id) ON DELETE CASCADE
            );
        """)

        # Tabla de Enlaces / Aristas entre nodos con sincronización NetBox
        await db.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id TEXT PRIMARY KEY,
                map_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                source_interface TEXT,
                target_interface TEXT,
                source_interface_id INTEGER,
                target_interface_id INTEGER,
                netbox_cable_id INTEGER,
                cable_type TEXT DEFAULT 'cat6',
                cable_status TEXT DEFAULT 'connected',
                status TEXT DEFAULT 'ok',
                rtt_ms REAL DEFAULT 0.0,
                loss_percent REAL DEFAULT 0.0,
                extra_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (map_id) REFERENCES maps(id) ON DELETE CASCADE,
                FOREIGN KEY (source_node_id) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target_node_id) REFERENCES nodes(id) ON DELETE CASCADE
            );
        """)

        # Auto-migración para columnas de NetBox si la tabla ya existía
        try:
            await db.execute("ALTER TABLE links ADD COLUMN source_interface_id INTEGER;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE links ADD COLUMN target_interface_id INTEGER;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE links ADD COLUMN netbox_cable_id INTEGER;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE links ADD COLUMN cable_type TEXT DEFAULT 'cat6';")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE links ADD COLUMN cable_status TEXT DEFAULT 'connected';")
        except Exception:
            pass

        # Tabla de Roles (Niveles de Usuarios y Permisos)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                is_system BOOLEAN DEFAULT 0,
                permissions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabla de Asignación de Roles de Usuario
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                username TEXT PRIMARY KEY,
                role_id TEXT NOT NULL,
                custom_permissions TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE RESTRICT
            );
        """)

        # Insertar Roles por Defecto del Sistema (Admin, Operator, Viewer, ServerAdmin)
        c_roles = await db.execute("SELECT COUNT(*) as count FROM roles")
        r_roles = await c_roles.fetchone()
        if r_roles and r_roles["count"] == 0:
            import json
            default_roles = [
                (
                    "admin",
                    "Administrador Total",
                    "Acceso completo al sistema, servidores, topología y gestión de usuarios",
                    1,
                    json.dumps({
                        "can_view_inventory": True,
                        "can_edit_topology": True,
                        "can_manage_submaps": True,
                        "can_view_zabbix": True,
                        "can_access_servers": True,
                        "can_manage_users": True
                    })
                ),
                (
                    "server_admin",
                    "Administrador de Servidores",
                    "Gestión del lienzo topológico y control completo del panel de Servidores",
                    1,
                    json.dumps({
                        "can_view_inventory": True,
                        "can_edit_topology": True,
                        "can_manage_submaps": True,
                        "can_view_zabbix": True,
                        "can_access_servers": True,
                        "can_manage_users": False
                    })
                ),
                (
                    "operator",
                    "Operador de Red",
                    "Creación de mapas, submapas y enlaces (Sin acceso a servidores ni usuarios)",
                    1,
                    json.dumps({
                        "can_view_inventory": True,
                        "can_edit_topology": True,
                        "can_manage_submaps": True,
                        "can_view_zabbix": True,
                        "can_access_servers": False,
                        "can_manage_users": False
                    })
                ),
                (
                    "viewer",
                    "Visualizador",
                    "Modo de solo lectura para monitoreo de estados de red",
                    1,
                    json.dumps({
                        "can_view_inventory": True,
                        "can_edit_topology": False,
                        "can_manage_submaps": False,
                        "can_view_zabbix": True,
                        "can_access_servers": False,
                        "can_manage_users": False
                    })
                )
            ]
            for r_id, r_name, r_desc, is_sys, r_perms in default_roles:
                await db.execute("""
                    INSERT INTO roles (id, name, description, is_system, permissions)
                    VALUES (?, ?, ?, ?, ?)
                """, (r_id, r_name, r_desc, is_sys, r_perms))
            logger.info("Roles por defecto (admin, server_admin, operator, viewer) inicializados.")

        # Mapa por defecto si la base de datos está vacía
        cursor = await db.execute("SELECT COUNT(*) as count FROM maps")
        row = await cursor.fetchone()
        if row and row["count"] == 0:
            await db.execute("""
                INSERT INTO maps (id, name, description)
                VALUES ('default-map', 'Topología Principal', 'Mapa raíz generado automáticamente por NexusDude')
            """)
            logger.info("Mapa por defecto 'default-map' inicializado.")

        await db.commit()
    logger.info("Base de datos SQLite de NexusDude inicializada exitosamente.")

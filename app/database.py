import os
import aiosqlite
import logging
from typing import Optional, Dict, Any
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
                position INTEGER DEFAULT 0,
                netbox_site_id INTEGER,
                site_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            await db.execute("ALTER TABLE maps ADD COLUMN position INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE maps ADD COLUMN netbox_site_id INTEGER;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE maps ADD COLUMN site_name TEXT;")
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_maps_position ON maps(position);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_maps_netbox_site ON maps(netbox_site_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_maps_site_name ON maps(site_name);")

        # Tabla de Jerarquía y Accesos Múltiples a Mapas (N-a-N: un mapa fuente puede tener múltiples accesos/rutas)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS map_hierarchy (
                id TEXT PRIMARY KEY,
                parent_map_id TEXT,
                child_map_id TEXT NOT NULL,
                alias TEXT,
                position INTEGER DEFAULT 0,
                is_primary BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (child_map_id) REFERENCES maps(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_map_id) REFERENCES maps(id) ON DELETE CASCADE
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_parent ON map_hierarchy(parent_map_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_child ON map_hierarchy(child_map_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_pos ON map_hierarchy(parent_map_id, position);")

        # Población inicial de map_hierarchy si está vacía, tomando como base parent_map_id existente en maps
        c_hier = await db.execute("SELECT COUNT(*) as count FROM map_hierarchy")
        r_hier = await c_hier.fetchone()
        if r_hier and r_hier["count"] == 0:
            import uuid
            c_all_maps = await db.execute("SELECT id, parent_map_id, position FROM maps")
            for m_row in await c_all_maps.fetchall():
                h_id = f"hier-{uuid.uuid4().hex[:8]}"
                p_id = m_row["parent_map_id"] if m_row["parent_map_id"] and m_row["parent_map_id"].strip() else None
                pos = m_row["position"] if m_row["position"] is not None else 0
                await db.execute("""
                    INSERT INTO map_hierarchy (id, parent_map_id, child_map_id, position, is_primary)
                    VALUES (?, ?, ?, ?, 1)
                """, (h_id, p_id, m_row["id"], pos))

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
                zabbix_src_interface TEXT,
                zabbix_tgt_interface TEXT,
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

        # Auto-migración para columnas de NetBox y Zabbix si la tabla ya existía
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
        try:
            await db.execute("ALTER TABLE links ADD COLUMN zabbix_src_interface TEXT;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE links ADD COLUMN zabbix_tgt_interface TEXT;")
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

        # Tabla de Configuración General del Sistema (i-WISP API Key, OLTs, etc.)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabla de Caché de Clientes y ONUs de i-WISP
        await db.execute("""
            CREATE TABLE IF NOT EXISTS iwisp_clients_cache (
                id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                client_name TEXT NOT NULL,
                onu_serial TEXT NOT NULL,
                onu_mac TEXT,
                onu_model TEXT,
                onu_brand TEXT,
                service_id TEXT,
                service_type TEXT,
                plan_id TEXT,
                plan_name TEXT,
                plan_cost TEXT,
                client_status TEXT,
                address TEXT,
                locality TEXT,
                zone TEXT,
                latitude REAL,
                longitude REAL,
                consumed_tb REAL DEFAULT 0.0,
                raw_data TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            await db.execute("ALTER TABLE iwisp_clients_cache ADD COLUMN consumed_tb REAL DEFAULT 0.0;")
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_iwisp_onu_serial ON iwisp_clients_cache(onu_serial);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_iwisp_client_id ON iwisp_clients_cache(client_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_iwisp_service_id ON iwisp_clients_cache(service_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_iwisp_client_name ON iwisp_clients_cache(client_name);")

        # Tabla de Directorio Global de Clientes i-WISP (para resolución ultra rápida y sin truncación)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS iwisp_directory (
                client_id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                rfc TEXT DEFAULT '',
                ine TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_iwisp_dir_norm ON iwisp_directory(normalized_name);")

        # Tabla de Credenciales y Comunidades SNMP personalizadas por OLT
        await db.execute("""
            CREATE TABLE IF NOT EXISTS olt_snmp_credentials (
                olt_ip TEXT PRIMARY KEY,
                community TEXT NOT NULL,
                vendor TEXT DEFAULT 'Huawei',
                notes TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Precargar credenciales verificadas en producción
        await db.execute("INSERT OR IGNORE INTO olt_snmp_credentials (olt_ip, community, vendor, notes) VALUES ('10.20.0.2', 'Muci!6508_rd', 'Huawei', 'EA5800-X2 Producción');")
        await db.execute("INSERT OR IGNORE INTO olt_snmp_credentials (olt_ip, community, vendor, notes) VALUES ('10.19.0.14', 'Mucivaga6508_rd', 'Huawei', 'EA5800-X15 Producción');")
        await db.execute("INSERT OR IGNORE INTO olt_snmp_credentials (olt_ip, community, vendor, notes) VALUES ('10.14.31.27', 'admin6508', 'Huawei', 'gpon-olt Producción');")

        await db.commit()
    logger.info("Base de datos SQLite de NexusDude inicializada exitosamente.")
    await load_system_config_into_settings()

async def get_system_config(key: str, default: Optional[str] = None) -> Optional[str]:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else default

async def set_system_config(key: str, value: str, description: Optional[str] = None):
    async with get_db_connection() as db:
        await db.execute("""
            INSERT INTO system_config (key, value, description, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                description = COALESCE(excluded.description, system_config.description),
                updated_at = CURRENT_TIMESTAMP
        """, (key, value, description))
        await db.commit()

async def get_all_system_config() -> dict:
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT key, value FROM system_config")
        rows = await cursor.fetchall()
        return {r["key"]: r["value"] for r in rows}

async def load_system_config_into_settings():
    """Carga variables guardadas en SQLite hacia settings en memoria."""
    try:
        cfg = await get_all_system_config()
        if "netbox_url" in cfg and cfg["netbox_url"]:
            settings.NETBOX_URL = cfg["netbox_url"]
        if "netbox_external_url" in cfg and cfg["netbox_external_url"]:
            settings.NETBOX_EXTERNAL_URL = cfg["netbox_external_url"]
        if "netbox_token" in cfg and cfg["netbox_token"]:
            settings.NETBOX_TOKEN = cfg["netbox_token"]
        if "zabbix_url" in cfg and cfg["zabbix_url"]:
            settings.ZABBIX_URL = cfg["zabbix_url"]
            try:
                from app.services.zabbix_service import zabbix_service
                zabbix_service.base_url = settings.ZABBIX_URL.rstrip('/')
                zabbix_service.api_url = f"{zabbix_service.base_url}/api_jsonrpc.php"
            except Exception:
                pass
        if "zabbix_token" in cfg and cfg["zabbix_token"]:
            settings.ZABBIX_TOKEN = cfg["zabbix_token"]
            try:
                from app.services.zabbix_service import zabbix_service
                zabbix_service.auth_token = settings.ZABBIX_TOKEN
            except Exception:
                pass
        logger.info("Configuraciones dinámicas del sistema cargadas desde SQLite a memoria.")
    except Exception as e:
        logger.warning(f"No se pudieron cargar configuraciones dinámicas desde SQLite: {e}")

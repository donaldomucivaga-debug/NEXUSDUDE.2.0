import logging
import re
import json
import time
import asyncio
from typing import Dict, Any, Optional, List
import httpx

from app.database import get_db_connection

logger = logging.getLogger("nexusdude.iwisp")

DEFAULT_IWISP_API_URL = "https://cloud.iwisp.mx:4434/api"

def normalize_onu_serial(serial_val: Any) -> str:
    """
    Normaliza el número de serie de una ONT.
    Maneja:
    - Cadenas Hex de Huawei: '48 57 54 43 12 34 56 78' o '4857544312345678' -> 'HWTC12345678'
    - Cadenas ASCII: 'HWTC12345678', 'VSOL12345678', 'ZTEG...'
    - Limpieza de comillas, espacios y guiones.
    """
    if not serial_val:
        return ""
    
    s = str(serial_val).strip().replace('"', '').replace("'", "")
    
    # Si viene con espacios o dos puntos (ej. formato Hex SNMP)
    s_compact = re.sub(r'[\s\:\-]', '', s)
    
    # Detección de prefijo HWTC en Hex (48 57 54 43)
    if len(s_compact) >= 8 and s_compact[:8].upper() == "48575443":
        try:
            prefix = "HWTC"
            remainder = s_compact[8:]
            return (prefix + remainder).upper()
        except Exception:
            pass

    # Detección general de cadenas hex que representen fabricantes conocidos (HWTC, VSOL, ZTEG, ALCL)
    known_vendor_hex = {
        "48575443": "HWTC",
        "56534F4C": "VSOL",
        "5A544547": "ZTEG",
        "414C434C": "ALCL",
    }
    prefix_8 = s_compact[:8].upper()
    if prefix_8 in known_vendor_hex:
        return (known_vendor_hex[prefix_8] + s_compact[8:]).upper()

    # Si es puramente hexadecimal y longitud >= 12, verificar si los primeros bytes son letras legibles
    if len(s_compact) >= 12 and all(c in "0123456789abcdefABCDEF" for c in s_compact):
        try:
            head_bytes = bytes.fromhex(s_compact[:8])
            head_ascii = head_bytes.decode("ascii", errors="ignore")
            if len(head_ascii) == 4 and head_ascii.isalnum():
                return (head_ascii + s_compact[8:]).upper()
        except Exception:
            pass

    return s_compact.upper()


class IWispService:
    def __init__(self):
        self._cached_api_key: Optional[str] = None
        self._cached_api_url: Optional[str] = None

    async def get_config(self) -> Dict[str, Any]:
        """Obtiene la configuración actual de i-WISP desde SQLite."""
        api_key = ""
        api_url = DEFAULT_IWISP_API_URL
        last_sync = None

        async with get_db_connection() as db:
            c = await db.execute("SELECT key, value FROM system_config WHERE key IN ('iwisp_api_key', 'iwisp_api_url', 'iwisp_last_sync')")
            rows = await c.fetchall()
            for r in rows:
                if r["key"] == "iwisp_api_key":
                    api_key = r["value"]
                elif r["key"] == "iwisp_api_url":
                    api_url = r["value"] or DEFAULT_IWISP_API_URL
                elif r["key"] == "iwisp_last_sync":
                    last_sync = r["value"]

        # Máscara para la API Key si existe (ej: "K5d8••••••••686")
        masked_key = ""
        if api_key:
            if len(api_key) > 8:
                masked_key = f"{api_key[:4]}••••••••{api_key[-4:]}"
            else:
                masked_key = "••••••••"

        status_info = await self.get_cache_status()

        return {
            "is_configured": bool(api_key),
            "has_api_key": bool(api_key),
            "api_key_masked": masked_key,
            "masked_api_key": masked_key,
            "api_url": api_url,
            "last_sync": last_sync,
            "cache_status": status_info
        }

    async def save_config(self, api_key: Optional[str] = None, api_url: Optional[str] = None) -> Dict[str, Any]:
        """Guarda o actualiza la configuración de i-WISP en SQLite."""
        async with get_db_connection() as db:
            if api_key is not None and api_key.strip():
                clean_key = api_key.strip()
                await db.execute("""
                    INSERT INTO system_config (key, value, description, updated_at)
                    VALUES ('iwisp_api_key', ?, 'API Key para consultas i-WISP Manager', CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """, (clean_key,))
                self._cached_api_key = clean_key

            if api_url is not None and api_url.strip():
                clean_url = api_url.strip().rstrip('/')
                await db.execute("""
                    INSERT INTO system_config (key, value, description, updated_at)
                    VALUES ('iwisp_api_url', ?, 'URL base de la API de i-WISP', CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """, (clean_url,))
                self._cached_api_url = clean_url

            await db.commit()

        return await self.get_config()

    async def get_raw_api_key(self) -> str:
        """Obtiene la API key pura sin enmascarar."""
        if self._cached_api_key:
            return self._cached_api_key
        async with get_db_connection() as db:
            c = await db.execute("SELECT value FROM system_config WHERE key = 'iwisp_api_key'")
            r = await c.fetchone()
            if r and r["value"]:
                self._cached_api_key = r["value"].strip()
                return self._cached_api_key
        return ""

    async def get_raw_api_url(self) -> str:
        """Obtiene la URL base configurada."""
        if self._cached_api_url:
            return self._cached_api_url
        async with get_db_connection() as db:
            c = await db.execute("SELECT value FROM system_config WHERE key = 'iwisp_api_url'")
            r = await c.fetchone()
            if r and r["value"]:
                self._cached_api_url = r["value"].strip().rstrip('/')
                return self._cached_api_url
        return DEFAULT_IWISP_API_URL

    async def test_connection(self, test_api_key: Optional[str] = None, test_api_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Prueba la conectividad y validación de la API Key contra i-WISP.
        Utiliza el endpoint ligero /getLocalities.
        """
        api_key = test_api_key or await self.get_raw_api_key()
        api_url = (test_api_url or await self.get_raw_api_url()).rstrip('/')

        if not api_key:
            return {
                "success": False,
                "message": "No se ha proporcionado una API Key para probar la conexión."
            }

        endpoint = f"{api_url}/getLocalities"
        try:
            # i-WISP espera un request body JSON con api_key y tipo
            payload = {
                "api_key": api_key,
                "tipo": "F"
            }
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                res = await client.request("GET", endpoint, json=payload)

                if res.status_code == 200:
                    data = res.json()
                    count = len(data) if isinstance(data, list) else 0
                    return {
                        "success": True,
                        "status_code": 200,
                        "message": f"Conexión exitosa con i-WISP Manager ({count} localidades descubiertas).",
                        "localities_sample": data[:3] if isinstance(data, list) else []
                    }
                elif res.status_code == 403:
                    return {
                        "success": False,
                        "status_code": 403,
                        "message": "Error 403: API Key no autorizada o inválida en i-WISP Manager."
                    }
                else:
                    return {
                        "success": False,
                        "status_code": res.status_code,
                        "message": f"Respuesta inesperada de i-WISP (Código {res.status_code}): {res.text[:200]}"
                    }
        except httpx.ConnectError as ce:
            return {
                "success": False,
                "message": f"Error de conexión con el host i-WISP ({api_url}): {str(ce)}"
            }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": f"Tiempo de espera agotado al conectar con {api_url}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error probando conexión con i-WISP: {str(e)}"
            }

    async def get_cache_status(self) -> Dict[str, Any]:
        """Devuelve el total de clientes y ONUs en la caché local."""
        async with get_db_connection() as db:
            c_total = await db.execute("SELECT COUNT(*) as count FROM iwisp_clients_cache")
            r_total = await c_total.fetchone()
            total_onus = r_total["count"] if r_total else 0

            c_clients = await db.execute("SELECT COUNT(DISTINCT client_id) as count FROM iwisp_clients_cache")
            r_clients = await c_clients.fetchone()
            total_clients = r_clients["count"] if r_clients else 0

            c_last = await db.execute("SELECT MAX(updated_at) as last_update FROM iwisp_clients_cache")
            r_last = await c_last.fetchone()
            last_update = r_last["last_update"] if r_last else None

        return {
            "total_clients": total_clients,
            "total_onus": total_onus,
            "last_update": last_update
        }

    async def get_client_info(
        self,
        onu_serial: Optional[str] = None,
        service_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Busca un cliente en la caché local por múltiples criterios complementarios:
        1. Serial ONT normalizado (exacto o sufijo de 8 caracteres).
        2. ID de servicio de i-WISP (service_id).
        3. ID de cliente de i-WISP (client_id).
        4. Nombre del cliente (client_name exacto o parcial).
        Si no se encuentra y se dispone de un ID numérico, intenta consulta en caliente contra i-WISP.
        """
        clean_serial = normalize_onu_serial(onu_serial) if onu_serial else ""
        clean_srv_id = str(service_id).strip() if service_id else ""
        clean_cli_id = str(client_id).strip() if client_id else ""
        clean_name = str(client_name).strip() if client_name else ""

        row = None
        async with get_db_connection() as db:
            # 1. Búsqueda por número de serie
            if clean_serial:
                c = await db.execute("SELECT * FROM iwisp_clients_cache WHERE onu_serial = ?", (clean_serial,))
                row = await c.fetchone()
                if not row and len(clean_serial) >= 8:
                    c = await db.execute("SELECT * FROM iwisp_clients_cache WHERE onu_serial LIKE ?", (f"%{clean_serial[-8:]}%",))
                    row = await c.fetchone()

            # 2. Búsqueda por service_id
            if not row and clean_srv_id:
                c = await db.execute("SELECT * FROM iwisp_clients_cache WHERE service_id = ?", (clean_srv_id,))
                row = await c.fetchone()

            # 3. Búsqueda por client_id
            if not row and clean_cli_id:
                c = await db.execute("SELECT * FROM iwisp_clients_cache WHERE client_id = ?", (clean_cli_id,))
                row = await c.fetchone()

            # 4. Búsqueda por nombre de cliente
            if not row and clean_name and len(clean_name) >= 4:
                # Intento 1: LIKE con el nombre completo
                c = await db.execute("SELECT * FROM iwisp_clients_cache WHERE client_name LIKE ?", (f"%{clean_name}%",))
                row = await c.fetchone()

                # Intento 2: Palabras clave si el orden apellidos/nombres difiere
                if not row:
                    words = [w for w in re.split(r'\s+', clean_name) if len(w) >= 4]
                    if len(words) >= 2:
                        query = "SELECT * FROM iwisp_clients_cache WHERE " + " AND ".join(["client_name LIKE ?"] * len(words))
                        params = [f"%{w}%" for w in words]
                        c = await db.execute(query, tuple(params))
                        row = await c.fetchone()

        # 5. Consulta en caliente a la API de i-WISP si no hubo coincidencia en caché local
        if not row and (clean_srv_id.isdigit() or clean_cli_id.isdigit()):
            candidate_ids = []
            if clean_cli_id.isdigit():
                candidate_ids.append(int(clean_cli_id))
            if clean_srv_id.isdigit() and int(clean_srv_id) not in candidate_ids:
                candidate_ids.append(int(clean_srv_id))

            for cid in candidate_ids:
                try:
                    api_key = await self.get_raw_api_key()
                    api_url = await self.get_raw_api_url()
                    if api_key and api_url:
                        async with httpx.AsyncClient(timeout=4.0, verify=False) as http_c:
                            res = await http_c.request(
                                "GET",
                                f"{api_url}/getClient",
                                json={"api_key": api_key, "idcliente": cid}
                            )
                            if res.status_code == 200:
                                c_detail = res.json()
                                if isinstance(c_detail, dict) and "servicios" in c_detail:
                                    for srv in c_detail.get("servicios", []):
                                        await self.upsert_client_service(c_detail, srv)

                                    async with get_db_connection() as db:
                                        c = await db.execute(
                                            "SELECT * FROM iwisp_clients_cache WHERE client_id = ? OR service_id = ? OR onu_serial = ?",
                                            (str(c_detail.get("id")), clean_srv_id, clean_serial)
                                        )
                                        row = await c.fetchone()
                                        if row:
                                            break
                except Exception as ex:
                    logger.debug(f"Fallo en consulta en caliente a i-WISP para ID {cid}: {ex}")

        if row:
            d = dict(row)
            return {
                "client_id": d["client_id"],
                "client_name": d["client_name"],
                "onu_serial": d["onu_serial"],
                "onu_mac": d.get("onu_mac"),
                "onu_model": d.get("onu_model"),
                "onu_brand": d.get("onu_brand"),
                "service_id": d.get("service_id"),
                "plan_id": d.get("plan_id"),
                "plan_name": d.get("plan_name") or "Plan Estándar",
                "plan_cost": d.get("plan_cost") or "",
                "status": d.get("client_status") or "Activo",
                "locality": d.get("locality"),
                "zone": d.get("zone"),
                "address": d.get("address"),
                "latitude": d.get("latitude"),
                "longitude": d.get("longitude"),
                "consumed_tb": d.get("consumed_tb")
            }
        return None

    async def get_client_by_serial(self, onu_serial: str) -> Optional[Dict[str, Any]]:
        """Busca un cliente en la caché local por número de serie de ONT."""
        return await self.get_client_info(onu_serial=onu_serial)

    async def upsert_client_service(self, client_data: Dict[str, Any], service: Dict[str, Any]):
        """Inserta o actualiza un registro de servicio/ONT en la caché local."""
        onu_serial = normalize_onu_serial(service.get("onu_numero_serie") or service.get("cpe_numero_serie"))
        if not onu_serial:
            return

        client_id = str(client_data.get("id", "")).strip()
        client_name = str(client_data.get("nombre", "")).strip()
        if not client_id or not client_name:
            return

        cache_id = f"{client_id}_{onu_serial}"
        plan_id = str(service.get("plan") or "")
        plan_name = str(service.get("nombre") or "").strip()
        plan_cost = str(service.get("costo") or "").strip()
        service_id = str(service.get("id") or "")
        service_type = str(service.get("tipo") or "F")

        consumed_tb = 0.0
        try:
            raw_c = service.get("consumo_tb") or service.get("consumed_tb")
            if raw_c:
                consumed_tb = float(raw_c)
        except Exception:
            pass

        lat = None
        lon = None
        try:
            if service.get("latitud"):
                lat = float(service["latitud"])
            if service.get("longitud"):
                lon = float(service["longitud"])
        except Exception:
            pass

        async with get_db_connection() as db:
            await db.execute("""
                INSERT INTO iwisp_clients_cache (
                    id, client_id, client_name, onu_serial, onu_mac, onu_model, onu_brand,
                    service_id, service_type, plan_id, plan_name, plan_cost, client_status,
                    address, locality, zone, latitude, longitude, consumed_tb, raw_data, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    client_name = excluded.client_name,
                    onu_mac = excluded.onu_mac,
                    onu_model = excluded.onu_model,
                    onu_brand = excluded.onu_brand,
                    plan_name = excluded.plan_name,
                    plan_cost = excluded.plan_cost,
                    client_status = excluded.client_status,
                    address = excluded.address,
                    locality = excluded.locality,
                    zone = excluded.zone,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    consumed_tb = CASE WHEN excluded.consumed_tb > 0 THEN excluded.consumed_tb ELSE iwisp_clients_cache.consumed_tb END,
                    raw_data = excluded.raw_data,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                cache_id, client_id, client_name, onu_serial,
                service.get("onu_mac") or service.get("cpe_mac") or "",
                service.get("onu_modelo") or service.get("cpe_modelo") or "",
                service.get("onu_marca") or service.get("cpe_marca") or "",
                service_id, service_type, plan_id, plan_name, plan_cost,
                client_data.get("estatus") or "activo",
                service.get("direccion") or client_data.get("direccion") or "",
                service.get("localidad") or client_data.get("localidad") or "",
                client_data.get("zona") or "",
                lat, lon, consumed_tb, json.dumps({"client": client_data, "service": service})
            ))
            await db.commit()

    async def sync_clients_from_iwisp(self, batch_size: int = 50) -> Dict[str, Any]:
        """
        Sincroniza el inventario completo de clientes y sus ONTs desde la API de i-WISP Manager.
        Particiona la consulta por ventanas de fecha para evitar la truncación del límite de 2000 registros de i-WISP.
        """
        api_key = await self.get_raw_api_key()
        api_url = await self.get_raw_api_url()

        if not api_key:
            raise ValueError("No se ha configurado la API Key de i-WISP Manager. Por favor ingrésela en Configuración.")

        start_time = time.time()
        logger.info(f"Iniciando sincronización completa de clientes desde i-WISP ({api_url})...")

        # Ventanas de fecha particionadas para no exceder los 2000 registros por llamada
        date_ranges = [
            ("2017-01-01", "2019-12-31"),
            ("2020-01-01", "2021-12-31"),
            ("2022-01-01", "2022-12-31"),
            ("2023-01-01", "2023-12-31"),
            ("2024-01-01", "2024-06-30"),
            ("2024-07-01", "2024-09-30"),
            ("2024-10-01", "2024-12-31"),
            ("2025-01-01", "2025-06-30"),
            ("2025-07-01", "2025-12-31"),
            ("2026-01-01", "2026-12-31"),
        ]

        clients_dict: Dict[str, Dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            for f_from, f_to in date_ranges:
                try:
                    res = await client.request(
                        "GET",
                        f"{api_url}/getClientDateReg",
                        json={
                            "api_key": api_key,
                            "fecha_desde": f_from,
                            "fecha_hasta": f_to
                        }
                    )
                    if res.status_code == 200:
                        raw = res.json()
                        if isinstance(raw, list):
                            for c in raw:
                                if c.get("id"):
                                    clients_dict[str(c["id"])] = c
                except Exception as e:
                    logger.warning(f"Error consultando intervalo {f_from} a {f_to} en i-WISP: {e}")

        total_registered = len(clients_dict)
        clients_list = list(clients_dict.values())
        logger.info(f"i-WISP reportó {total_registered} clientes únicos totales. Sincronizando detalles y servicios...")

        synced_count = 0
        onus_count = 0
        errors_count = 0

        semaphore = asyncio.Semaphore(20)

        async def fetch_client_detail(client_item: Dict[str, Any]):
            nonlocal synced_count, onus_count, errors_count
            c_id = client_item.get("id")
            if not c_id:
                return

            async with semaphore:
                try:
                    async with httpx.AsyncClient(timeout=12.0, verify=False) as http_c:
                        res = await http_c.request(
                            "GET",
                            f"{api_url}/getClient",
                            json={"api_key": api_key, "idcliente": int(c_id)}
                        )
                        if res.status_code == 200:
                            c_detail = res.json()
                            if isinstance(c_detail, dict) and "servicios" in c_detail:
                                for srv in c_detail.get("servicios", []):
                                    serial = srv.get("onu_numero_serie") or srv.get("cpe_numero_serie")
                                    if serial:
                                        await self.upsert_client_service(c_detail, srv)
                                        onus_count += 1
                                synced_count += 1
                except Exception as ex:
                    errors_count += 1
                    logger.debug(f"Error obteniendo detalle para cliente {c_id}: {ex}")

        tasks = [fetch_client_detail(c) for c in clients_list]
        await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = round(time.time() - start_time, 2)

        # Actualizar timestamp en system_config
        async with get_db_connection() as db:
            await db.execute("""
                INSERT INTO system_config (key, value, description, updated_at)
                VALUES ('iwisp_last_sync', datetime('now', 'localtime'), 'Fecha y hora de última sincronización i-WISP', CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """)
            await db.commit()

        logger.info(f"Sincronización i-WISP finalizada en {elapsed}s: {synced_count} clientes procesados, {onus_count} ONUs en caché, {errors_count} errores.")

        return {
            "status": "success",
            "total_clients_in_iwisp": total_registered,
            "clients_synced": synced_count,
            "onus_cached": onus_count,
            "errors": errors_count,
            "elapsed_seconds": elapsed,
            "message": f"Sincronización completada: {onus_count} ONUs vinculadas a clientes en {elapsed}s."
        }


iwisp_service = IWispService()

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# --- Autenticación & Niveles de Permisos (RBAC) ---
class RolePermissions(BaseModel):
    can_view_inventory: bool = True
    can_edit_topology: bool = True
    can_manage_submaps: bool = True
    can_view_zabbix: bool = True
    can_access_servers: bool = False  # Pestaña / Sección Servidores
    can_manage_users: bool = False

class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: RolePermissions

class RoleOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    is_system: bool = False
    permissions: RolePermissions
    created_at: Optional[str] = None

class UserRoleUpdate(BaseModel):
    role: str
    custom_permissions: Optional[RolePermissions] = None

class UserResponse(BaseModel):
    username: str
    role: str
    permissions: Optional[RolePermissions] = None
    exp: Optional[int] = None
    authenticated: bool = True

# --- Healthcheck ---
class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: datetime
    database: str
    nexus_orchestrator_url: str

# --- Nodos (Equipos en el mapa) ---
class NodeBase(BaseModel):
    name: str
    ip: Optional[str] = None
    device_id: Optional[int] = None
    device_type: Optional[str] = "generic"
    site_name: Optional[str] = None
    x: float = 100.0
    y: float = 100.0
    status: Optional[str] = "unknown"
    extra_data: Optional[Dict[str, Any]] = None

class NodeCreate(NodeBase):
    map_id: str

class NodeUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    device_type: Optional[str] = None
    site_name: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    status: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None

class NodeOut(NodeBase):
    id: str
    map_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# --- Interfaces de Dispositivos (Puertos NetBox) ---
class DeviceInterfaceOut(BaseModel):
    id: Optional[int] = None
    name: str
    type: Optional[str] = "1000base-t"
    enabled: bool = True
    mgmt_only: bool = False
    is_connected: bool = False
    connected_device: Optional[str] = None
    connected_interface: Optional[str] = None
    cable_id: Optional[int] = None
    cable_status: Optional[str] = None
    cable_type: Optional[str] = None

# --- Enlaces (Aristas entre nodos con sincronización NetBox & Telemetría Zabbix) ---
class LinkBase(BaseModel):
    source_node_id: str
    target_node_id: str
    source_interface: Optional[str] = None
    target_interface: Optional[str] = None
    source_interface_id: Optional[int] = None
    target_interface_id: Optional[int] = None
    netbox_cable_id: Optional[int] = None
    cable_type: Optional[str] = "cat6"
    cable_status: Optional[str] = "connected"
    zabbix_src_interface: Optional[str] = None
    zabbix_tgt_interface: Optional[str] = None
    status: Optional[str] = "ok"
    rtt_ms: Optional[float] = 0.0
    loss_percent: Optional[float] = 0.0
    extra_data: Optional[Dict[str, Any]] = None

class LinkCreate(LinkBase):
    map_id: str

class LinkUpdate(BaseModel):
    source_node_id: Optional[str] = None
    target_node_id: Optional[str] = None
    source_interface: Optional[str] = None
    target_interface: Optional[str] = None
    source_interface_id: Optional[int] = None
    target_interface_id: Optional[int] = None
    netbox_cable_id: Optional[int] = None
    cable_type: Optional[str] = None
    cable_status: Optional[str] = None
    zabbix_src_interface: Optional[str] = None
    zabbix_tgt_interface: Optional[str] = None
    status: Optional[str] = None
    rtt_ms: Optional[float] = None
    loss_percent: Optional[float] = None
    extra_data: Optional[Dict[str, Any]] = None

class LinkOut(LinkBase):
    id: str
    map_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# --- Mapas ---
class MapBase(BaseModel):
    name: str
    description: Optional[str] = None
    parent_map_id: Optional[str] = None
    grid_size: Optional[int] = 20
    position: Optional[int] = 0

class MapCreate(MapBase):
    id: Optional[str] = None

class MapUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_map_id: Optional[str] = None
    grid_size: Optional[int] = None
    position: Optional[int] = None

class MapOrderItem(BaseModel):
    id: str
    position: int
    parent_map_id: Optional[str] = None

class MapReorderRequest(BaseModel):
    items: List[MapOrderItem]

class MapOut(MapBase):
    id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    nodes_count: Optional[int] = 0
    links_count: Optional[int] = 0

class MapDetailOut(MapOut):
    nodes: List[NodeOut] = []
    links: List[LinkOut] = []

class CreateMapFromSiteRequest(BaseModel):
    site_name: str
    parent_map_id: Optional[str] = None
    insert_submap_node: bool = True
    x: Optional[float] = None
    y: Optional[float] = None
    auto_populate: bool = True

class PopulateMapFromSiteRequest(BaseModel):
    site_name: Optional[str] = None

class AutogenerateSitesRequest(BaseModel):
    parent_map_id: Optional[str] = "default-map"
    only_with_devices: bool = True
    selected_sites: Optional[List[str]] = None
    auto_populate: bool = True
    insert_submap_nodes: bool = True

class SiteMappingStatusOut(BaseModel):
    site_id: int
    site_name: str
    device_count: int
    has_map: bool
    existing_map_id: Optional[str] = None

class BulkCreateMapsFromSitesRequest(BaseModel):
    parent_map_id: Optional[str] = "default-map"
    only_with_devices: bool = True
    auto_populate_devices: bool = True
    insert_submap_nodes: bool = True
    skip_existing: bool = True
    max_sites: Optional[int] = None

class BulkDeleteNodesRequest(BaseModel):
    node_ids: List[str]

class ZabbixSyncRequest(BaseModel):
    scope: Optional[str] = "global"  # "global" o "branch"
    map_id: Optional[str] = None
    clear_first: Optional[bool] = True


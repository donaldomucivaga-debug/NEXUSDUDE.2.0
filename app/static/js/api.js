/**
 * NexusDude API Client
 * Maneja tokens de sesión y llamadas autenticadas al backend de NexusDude
 */

const API = {
  TOKEN_KEY: 'nexus_jwt_token',

  getToken() {
    return localStorage.getItem(this.TOKEN_KEY);
  },

  setToken(token) {
    if (token) {
      localStorage.setItem(this.TOKEN_KEY, token);
    }
  },

  clearToken() {
    localStorage.removeItem(this.TOKEN_KEY);
  },

  async fetch(endpoint, options = {}) {
    const token = this.getToken();
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(endpoint, {
      ...options,
      headers
    });

    if (response.status === 401) {
      console.warn("Sesión inválida o expirada en NexusDude.");
    }

    return response;
  },

  async verifyAuth() {
    const res = await this.fetch('/api/auth/verify');
    if (res.ok) {
      return await res.json();
    }
    return null;
  },

  async getHealth() {
    const res = await fetch('/api/health');
    return await res.json();
  },

  // --- Mapas & Jerarquía ---
  async getMaps() {
    const res = await this.fetch('/api/maps');
    if (res.ok) return await res.json();
    return [];
  },

  async getMapDetail(mapId) {
    const res = await this.fetch(`/api/maps/${mapId}`);
    if (res.ok) return await res.json();
    return null;
  },

  async createMap(mapData) {
    const res = await this.fetch('/api/maps', {
      method: 'POST',
      body: JSON.stringify(mapData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async updateMap(mapId, updateData) {
    const res = await this.fetch(`/api/maps/${mapId}`, {
      method: 'PUT',
      body: JSON.stringify(updateData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async reorderMaps(items) {
    const res = await this.fetch('/api/maps/reorder', {
      method: 'POST',
      body: JSON.stringify({ items })
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async deleteMap(mapId) {
    const res = await this.fetch(`/api/maps/${mapId}`, {
      method: 'DELETE'
    });
    return res.ok;
  },

  async getMapBreadcrumb(mapId) {
    const res = await this.fetch(`/api/maps/${mapId}/breadcrumb`);
    if (res.ok) return await res.json();
    return [];
  },

  async createMapFromSite(payload) {
    const res = await this.fetch('/api/maps/from-site', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async populateMapFromSite(mapId, siteName = null) {
    const res = await this.fetch(`/api/maps/${mapId}/populate-from-site`, {
      method: 'POST',
      body: JSON.stringify({ site_name: siteName })
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async ensureParentNode(mapId) {
    const res = await this.fetch(`/api/maps/${mapId}/ensure-parent-node`, {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async retrofitParentNodes() {
    const res = await this.fetch('/api/maps/retrofit-parent-nodes', {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async bulkCreateMapsFromSites(params = {}) {
    const res = await this.fetch('/api/maps/bulk-from-sites', {
      method: 'POST',
      body: JSON.stringify(params)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async getSitesStatus() {
    const res = await this.fetch('/api/maps/sites-status');
    if (res.ok) return await res.json();
    return { total_sites: 0, mapped_count: 0, unmapped_count: 0, sites: [] };
  },

  async setupNetBoxWebhook() {
    const res = await this.fetch('/api/webhooks/netbox/setup', {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async getNetBoxWebhookStatus() {
    const res = await this.fetch('/api/webhooks/netbox/status');
    if (res.ok) return await res.json();
    return { active: false, status: 'unknown' };
  },

  // --- Nodos ---
  async createNode(nodeData) {
    const res = await this.fetch('/api/maps/nodes', {
      method: 'POST',
      body: JSON.stringify(nodeData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async updateNode(nodeId, updateData) {
    const res = await this.fetch(`/api/maps/nodes/${nodeId}`, {
      method: 'PUT',
      body: JSON.stringify(updateData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async deleteNode(nodeId) {
    const res = await this.fetch(`/api/maps/nodes/${nodeId}`, {
      method: 'DELETE'
    });
    return res.ok;
  },

  async bulkDeleteNodes(nodeIds) {
    const res = await this.fetch('/api/maps/nodes/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ node_ids: nodeIds })
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async syncMapNetboxNodes(mapId) {
    const res = await this.fetch(`/api/maps/${mapId}/sync-netbox-nodes`, {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async syncAllNetboxNodes() {
    const res = await this.fetch('/api/maps/sync-all-netbox-nodes', {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },


  // --- Enlaces ---
  async createLink(linkData) {
    const res = await this.fetch('/api/maps/links', {
      method: 'POST',
      body: JSON.stringify(linkData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async updateLink(linkId, updateData) {
    const res = await this.fetch(`/api/maps/links/${linkId}`, {
      method: 'PUT',
      body: JSON.stringify(updateData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async deleteLink(linkId) {
    const res = await this.fetch(`/api/maps/links/${linkId}`, {
      method: 'DELETE'
    });
    return res.ok;
  },

  // --- Inventario NetBox / Búsqueda ---
  async searchInventory(params = {}) {
    const query = new URLSearchParams();
    if (params.query) query.set('query', params.query);
    if (params.site) query.set('site', params.site);
    if (params.role) query.set('role', params.role);
    if (params.manufacturer) query.set('manufacturer', params.manufacturer);
    if (params.limit) query.set('limit', params.limit);
    if (params.offset) query.set('offset', params.offset);

    const res = await this.fetch(`/api/inventory/devices?${query.toString()}`);
    if (res.ok) return await res.json();
    return { total: 0, results: [] };
  },

  async getDeviceById(deviceId) {
    const res = await this.fetch(`/api/inventory/devices/${deviceId}`);
    if (res.ok) return await res.json();
    return null;
  },

  async getDeviceInterfaces(deviceId) {
    const res = await this.fetch(`/api/inventory/devices/${deviceId}/interfaces`);
    if (res.ok) return await res.json();
    return [];
  },

  async syncMapNetboxCables(mapId) {
    const res = await this.fetch(`/api/maps/${mapId}/sync-netbox-cables`, {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async getInventorySites() {
    const res = await this.fetch('/api/inventory/sites');
    if (res.ok) return await res.json();
    return [];
  },

  async getSitesSummary() {
    const res = await this.fetch('/api/inventory/sites-summary');
    if (res.ok) return await res.json();
    return [];
  },

  async getInventoryRoles() {
    const res = await this.fetch('/api/inventory/roles');
    if (res.ok) return await res.json();
    return [];
  },

  async getInventoryManufacturers() {
    const res = await this.fetch('/api/inventory/manufacturers');
    if (res.ok) return await res.json();
    return [];
  },

  async refreshInventory() {
    const res = await this.fetch('/api/inventory/refresh', { method: 'POST' });
    return res.ok;
  },

  // --- Sincronización Zabbix (BSM Services) ---
  async getZabbixStatus() {
    const res = await this.fetch('/api/zabbix/status');
    if (res.ok) return await res.json();
    return { connected: false, error: 'No se pudo conectar a Zabbix' };
  },

  async getTopologyAnalysis(mapId) {
    const res = await this.fetch(`/api/zabbix/analysis/${mapId}`);
    if (res.ok) return await res.json();
    return null;
  },

  async syncZabbix(payload = { scope: 'global', clear_first: true }) {
    const res = await this.fetch('/api/zabbix/sync', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async clearZabbixServices(mapId = null, scope = 'global') {
    const query = new URLSearchParams();
    if (mapId) query.set('map_id', mapId);
    if (scope) query.set('scope', scope);
    const res = await this.fetch(`/api/zabbix/services?${query.toString()}`, {
      method: 'DELETE'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async getMapRealtimeStatus(mapId) {
    const res = await this.fetch(`/api/zabbix/realtime/${mapId}`);
    if (res.ok) return await res.json();
    return null;
  },

  async getNodeTelemetry(nodeId) {
    const res = await this.fetch(`/api/zabbix/node-telemetry/${nodeId}`);
    if (res.ok) return await res.json();
    return null;
  },

  async getNodeZabbixInterfaces(nodeId) {
    const res = await this.fetch(`/api/zabbix/nodes/${nodeId}/interfaces`);
    if (res.ok) return await res.json();
    return [];
  },

  async getLinkTelemetry(linkId) {
    const res = await this.fetch(`/api/zabbix/links/${linkId}/telemetry`);
    if (res.ok) return await res.json();
    return null;
  },

  async getMapLinksTelemetry(mapId) {
    const res = await this.fetch(`/api/zabbix/maps/${mapId}/links-telemetry`);
    if (res.ok) return await res.json();
    return { map_id: mapId, count: 0, links: {} };
  },

  // --- RBAC & Gestión de Roles / Permisos ---
  async getRoles() {
    const res = await this.fetch('/api/rbac/roles');
    if (res.ok) return await res.json();
    return [];
  },

  async createRole(roleData) {
    const res = await this.fetch('/api/rbac/roles', {
      method: 'POST',
      body: JSON.stringify(roleData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async updateRole(roleId, roleData) {
    const res = await this.fetch(`/api/rbac/roles/${roleId}`, {
      method: 'PUT',
      body: JSON.stringify(roleData)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async deleteRole(roleId) {
    const res = await this.fetch(`/api/rbac/roles/${roleId}`, {
      method: 'DELETE'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async assignUserRole(username, payload) {
    const res = await this.fetch(`/api/rbac/users/${username}/role`, {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  // --- Espectro de Radiofrecuencia (4850 - 7250 MHz) ---
  async getMapSpectrum(mapId = 'default-map') {
    const res = await this.fetch(`/api/zabbix/spectrum/${mapId}`);
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  // --- Telemetría de Brazos FTTH / Puertos GPON ---
  async getGponBranchTelemetry(nodeId) {
    const res = await this.fetch(`/api/zabbix/gpon-branch/${nodeId}`);
    if (res.ok) return await res.json();
    return null;
  },

  async getOltGponPorts(oltIpOrName) {
    const res = await this.fetch(`/api/zabbix/olt/${encodeURIComponent(oltIpOrName)}/gpon-ports`);
    if (res.ok) return await res.json();
    return [];
  },

  // --- Integraciones NetBox y Zabbix (NexusDude Configuración) ---
  async getIntegrationsConfig() {
    const res = await this.fetch('/api/config/integrations');
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async saveNetboxConfig(payload) {
    const res = await this.fetch('/api/config/netbox', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async testNetboxConnection(payload = {}) {
    const res = await this.fetch('/api/config/netbox/test', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async saveZabbixConfig(payload) {
    const res = await this.fetch('/api/config/zabbix', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async testZabbixConnection(payload = {}) {
    const res = await this.fetch('/api/config/zabbix/test', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  // --- Configuración e Integración i-WISP ---
  async getIWispConfig() {
    const res = await this.fetch('/api/config/iwisp');
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async saveIWispConfig(payload) {
    const res = await this.fetch('/api/config/iwisp', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async testIWispConnection(payload = {}) {
    const res = await this.fetch('/api/config/iwisp/test', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async syncIWispClients() {
    const res = await this.fetch('/api/config/iwisp/sync', {
      method: 'POST'
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async getIWispCacheStatus() {
    const res = await this.fetch('/api/config/iwisp/cache-status');
    if (res.ok) return await res.json();
    return { total_clients: 0, total_onus: 0, last_update: null };
  },

  // --- Diagnóstico Profundo OLT & Clientes FTTH ---
  async getOltDiagnosticSummary() {
    const res = await this.fetch('/api/zabbix/olt/diagnostic-summary');
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async getOltPortOntsDetailed(oltIpOrName, portIndex, threshold = -27.0, community = null) {
    let url = `/api/zabbix/olt/${encodeURIComponent(oltIpOrName)}/port/${encodeURIComponent(portIndex)}/onts-detailed?typical_threshold=${threshold}`;
    if (community) {
      url += `&community=${encodeURIComponent(community)}`;
    }
    const res = await this.fetch(url);
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  },

  async saveOltCommunity(oltIp, community, force = false) {
    const res = await this.fetch(`/api/zabbix/olt/${encodeURIComponent(oltIp)}/community`, {
      method: 'POST',
      body: JSON.stringify({ community, force })
    });
    if (res.ok) return await res.json();
    throw new Error(await res.text());
  }
};



# 📡 NexusDude (The Modern Network Dude for Nexus, Zabbix & NetBox)

Servidor web independiente para monitoreo visual, topologías de red interactivas y sincronización con Zabbix Services (BSM).

## 🚀 Características Principales
- **Despliegue Independiente (Standalone):** Contenedor Docker en el puerto `8085`.
- **SSO Delegado con Nexus:** Valida tokens JWT firmados por Nexus Orchestrator sin necesidad de relogueo.
- **Lienzo estilo The Dude:** HTML5 Canvas con Konva.js, zoom/pan interactivo y nodos arrastrables.
- **Persistencia Local:** SQLite de alta velocidad con modo WAL (`data/nexusdude.db`).
- **Integración con Ecosistema:** Conectado a la red Docker `monitoreo_default` para comunicación directa con Zabbix y NetBox.

## 🛠️ Comandos de Operación
- **Iniciar servicio:**
  ```bash
  docker compose up -d --build
  ```
- **Ver logs:**
  ```bash
  docker logs -f nexusdude-web
  ```
- **Verificar salud:**
  ```bash
  curl http://localhost:8085/api/health
  ```

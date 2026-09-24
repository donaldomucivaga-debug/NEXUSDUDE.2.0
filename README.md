# 📡 NexusDude (The Modern Network Dude for Nexus, Zabbix & NetBox)

Plataforma de mapeo, análisis y monitoreo visual de topologías de red en tiempo real, integrada bidireccionalmente con **NetBox (DCIM/IPAM)**, **Zabbix 7.0 (Telemetría, LLD & BSM)** y **OLTs GPON/FTTH (Huawei SmartAX/EA5800 y V-SOL)**.

---

## 🚀 Características Principales

### 1. 🗺️ Lienzo Topológico Interactivo (Estilo The Dude Moderno)
- **HTML5 Canvas de Alto Rendimiento:** Basado en **Konva.js** con soporte para miles de elementos, zoom infinito, paneo suave y cuadrícula magnética (*Snap to Grid* de 20px).
- **Jerarquía Multinivel y Submapas:** Navegación fluida entre mapas raíz, submapas locales, accesos directos (*parent shortcuts*) y bornes virtuales de interconexión con cálculo automático de coordenadas y enrutamiento ortogonal sin colisiones.
- **Iconografía y Estados Dinámicos:** Detección de tipos de dispositivos (Switches, Routers, APs Cambium ePMP, CPEs, OLTs Huawei/V-SOL, Servidores) con indicadores visuales de estado operativo en vivo (PING ICMP y SNMP).

### 2. 📝 Nodos de Notas y Anotaciones Visuales
- **Sticky Notes Personalizables:** Nodos livianos para documentación en lienzo (ej. *Entronque Huizache*, recordatorios, etiquetas de zona, notas operativas).
- **Paleta de Colores Translúcidos:** Temas visuales en amarillo, azul, verde, púrpura, naranja y gris con bordes y tipografía responsive.

### 3. 🌐 Brazos FTTH y Monitoreo Óptico GPON en Tiempo Real (`ftth_branch`)
- **Objeto Especializado de Ramal FTTH:** Representa brazos y puertos GPON vinculados a OLTs (ej. OLT Huizache `10.20.0.2`, puerto `GPON 0/1/0` / ifIndex `4194312192`).
- **Estados y Colores Inteligentes en Vivo:**
  - 🟢 **Verde (`#10b981`)**: Puerto GPON Operativo (`Link Up`) y niveles de señal dentro del rango normal ($> -27\text{ dBm}$).
  - 🟡 **Amarillo (`#f59e0b`)**: Alerta por clientes ópticos atípicos / deficientes ($\le -27\text{ dBm}$).
  - 🔴 **Rojo (`#ef4444`)**: Puerto GPON caído (`Link Down` / SNMP `ifOperStatus = 2`).
- **Telemetría Flotante y Diagnóstico de ONUs (Bajo Demanda)**:
  - **Tráfico en Vivo:** Medición de ancho de banda entrante y saliente (`Mbps In` / `Mbps Out`).
  - **Volumen Acumulado:** Medición mensual en Megabytes, Gigabytes y Terabytes (`GB` / `TB`).
  - **Diagnóstico Óptico de Clientes:**
    - Conteo y promedio en dBm de **Clientes Típicos** ($> -27\text{ dBm}$).
    - Conteo y promedio en dBm de **Clientes Atípicos** ($\le -27\text{ dBm}$).
  - **Análisis de Causa Raíz de Caídas**:
    - ⚡ **Dying-Gasp**: Corte de energía eléctrica en el domicilio del cliente (fibra intacta).
    - ✂️ **LOSi / LOBi**: Corte físico de fibra óptica o atenuación extrema.

### 4. 🔌 Integración Bidireccional de Cableado Físico con NetBox
- **Matriz Visual de Puertos Reales:** Renderizado interactivo de puertos físicos por modelo de equipo (ej. `GE1` a `GE24` + `SFP25` a `SFP28` en switches Planet SGS-6341, `eth0` en ePMP/CPEs, etc.).
- **Aprovisionamiento Automático de Interfaces:** Creación dinámica de plantillas de puertos en NetBox si el dispositivo no los tiene declarados.
- **Gestión de Cables (`dcim_cable`):** Creación y desvinculación automática de cables físicos en NetBox al conectar o eliminar aristas en el mapa.
- **Insignias Flotantes Dinámicas:** Etiquetas orientadas sobre la línea con separación visual calculada (`offsetDist`) para evitar superposición con los bordes de los nodos.

### 5. ⚡ Telemetría de Aristas y Analizador Espectral RF
- **Fuentes de Datos Zabbix Reutilizables:** Asignación de interfaces de Zabbix (`zabbix_src_interface`, `zabbix_tgt_interface`) como fuentes de telemetría sin restricción de exclusividad física.
- **Diagnóstico Óptico DDM (Gibics SFP):** Potencias ópticas de recepción y transmisión en dBm ($P_{\text{rx}}$, $P_{\text{tx}}$).
- **Métricas Inalámbricas (Cambium ePMP):** Nivel de señal RSSI (dBm), relación señal/ruido SNR (dB) y modulación MCS.
- **Analizador Espectral RF (4850 - 7250 MHz):** Regla gráfica de espectro con anchos de canal para bandas 5 GHz, UNII-4 y Wi-Fi 6E (6 GHz).

### 6. 📊 Plantillas SNMP Zabbix 7.0 (Huawei SmartAX / EA5800 & V-SOL)
- **Descubrimiento LLD de ONUs de Alta Velocidad:** Lectura de tablas ligeras `.43.1.9` (Descripciones) y `.43.1.3` (Serial Number) con `snmpbulkwalk` que evita timeouts por OMCI.
- **Alertas y Triggers Automatizados:**
  - Disparo de advertencia por ONT atenuada ($\le -27\text{ dBm}$).
  - Triggers acumulados de consumo mensual de ancho de banda por interfaz/puerto al alcanzar **1 TB**, **3 TB** y **5 TB**.

### 7. 🔐 Arquitectura y Seguridad
- **SSO Delegado con Nexus:** Autenticación fluida validando tokens JWT firmados por el *Nexus Sync Orchestrator*.
- **Persistencia Ultrarrápida:** Base de datos SQLite local optimizada con modo WAL (`data/nexusdude.db`).
- **Caché No Bloqueante:** Consultas asíncronas con `asyncio` y caché en memoria de 10s en frontend (`_gponTelemetryCache`).

---

## 🏗️ Arquitectura de Integración

```mermaid
graph LR
    subgraph UI ["Frontend (NexusDude)"]
        Canvas["Lienzo Konva.js (Nodos, Notas, Brazos FTTH)"]
        Tooltip["Tooltip Telemetría & Diagnóstico GPON"]
        PortModal["Modal Selección de Puertos & OLTs"]
        SpectrumBar["Analizador de Espectro RF"]
    end

    subgraph Backend ["NexusDude Web (FastAPI :8085)"]
        MapsAPI["Maps & Links API"]
        ZabbixService["Zabbix & SNMP Bulkwalk Service"]
        InventoryService["NetBox Inventory Service"]
        SQLite[("SQLite WAL (nexusdude.db)")]
    end

    subgraph Ecosystem ["Ecosistema Central"]
        NetBox["NetBox DCIM (Cables & Interfaces)"]
        Zabbix["Zabbix 7.0 Server (LLD, Triggers 1TB/3TB/5TB)"]
        HuaweiOLT["OLT Huawei SmartAX / EA5800 (SNMP)"]
        VsolOLT["OLT V-SOL (SNMP)"]
        Orchestrator["Nexus Sync Orchestrator (SSO)"]
    end

    Canvas <--> MapsAPI
    Tooltip <--> ZabbixService
    PortModal <--> InventoryService

    MapsAPI <--> SQLite
    InventoryService <--> NetBox
    ZabbixService <--> Zabbix
    ZabbixService <--> HuaweiOLT & VsolOLT
    MapsAPI -. SSO JWT .-> Orchestrator
```

---

## 🗺️ Plan de Implementación: Módulo de Diagnóstico y Análisis Profundo de OLTs (FTTH / GPON)

Se describe a continuación la hoja de ruta y especificación de diseño para el **Módulo de Diagnóstico Dedicado de OLTs**, una vista avanzada en segundo plano / pantalla completa que permite a los operadores e ingenieros de NOC diagnosticar a fondo el estado de los puertos GPON y clientes ONT directamente desde la OLT en tiempo real.

```mermaid
flowchart TD
    A["NOC / Operador"] -->|"Clic en ⚡ Diagnóstico OLT"| B["Vista / Dashboard Dedicado de OLTs"]
    B --> C["Selector Dinámico Multi-OLT (Zabbix/NetBox)"]
    C -->|"Auto-detecta IP & SNMP Community"| D["Panel de Puertos GPON (0/1/0 a 0/1/15)"]
    D --> E["Métricas de Puerto (Tráfico Mbps, Volumen GB/TB, ONUs Online/Offline)"]
    D -->|"Selección de Puerto Específico"| F["Tabla Maestra de Clientes / ONUs (SNMP Bulkwalk Ultra-Rápido)"]
    F --> G1["Niveles Ópticos (Rx/Tx dBm con Semáforo)"]
    F --> G2["Causa Raíz de Caída (⚡ Dying-Gasp vs ✂️ LOSi)"]
    F --> G3["Distancia (Metros) y Modelo ONT (EG8021V5, etc.)"]
    F --> G4["Agrupación Inteligente por Bote / Caja NAP / Zona"]
    F --> G5["Filtros Rápidos (Atenuados, Sin Luz, Corte Fibra, Duplicados)"]
    F --> G6["Consumo de Ancho de Banda & GB por Cliente (Opcional)"]
```

### 📋 Fases del Plan de Implementación

#### 🔹 Fase 1: Arquitectura Multi-OLT y Descubrimiento Dinámico
- **Soporte Multi-OLT:** Detección automática de todas las OLTs registradas en Zabbix y NetBox (ej. *OLT Huizache 10.20.0.2*, *OLT Central*, etc.), extrayendo dinámicamente su IP, modelo (Huawei SmartAX/EA5800, V-SOL) y comunidad SNMP de forma segura sin credenciales cableadas en código (*hardcoded*).
- **Endpoint de Resumen Global:** `GET /api/zabbix/olt/diagnostic-summary` que entrega el estado general de todas las OLTs, slots activos y conteo total de clientes.

#### 🔹 Fase 2: Grid y Matriz de Puertos GPON (Slots & Tarjetas)
- **Vista de Puertos (0/1/0 a 0/1/15):** Panel con tarjetas interactivas de cada puerto GPON mostrando:
  - Estado Operativo: `Up` (Verde) / `Down` (Rojo).
  - Tráfico en Tiempo Real: `Mbps In` y `Mbps Out`.
  - Volumen de Datos Acumulado: Megabytes, Gigabytes y Terabytes (`GB / TB`) con alertas de umbral.
  - Conteo de Clientes: `ONUs Online` / `ONUs Offline` / `Total Asignadas`.
  - Nivel Óptico Promedio del Puerto ($dBm$).

#### 🔹 Fase 3: Tabla Maestra de Diagnóstico Detallado de ONUs por Puerto
- **Endpoint de Consulta a Fondo:** `GET /api/zabbix/olt/{olt_ip}/port/{port_index}/onts-detailed` optimizado con `snmpbulkwalk -Cr32` (<0.2 segundos por puerto de 128 ONUs).
- **Columnas de Datos en Tiempo Real:**
  1. **Identificador y Contrato:** ONT ID, Descripción del Cliente (ej. `10089 - Vazquez Torres Lucero`).
  2. **Hardware:** Serial Number (Hex-STRING `HWTC...`) y Modelo (`EG8021V5`, `EG8041V5`, `EG8010H`).
  3. **Nivel Óptico de Recepción ($P_{\text{rx}}$) y Transmisión ($P_{\text{tx}}$):** Formateado en dBm con semáforo:
     - 🟢 **Óptima:** $> -24\text{ dBm}$
     - 🟡 **Aceptable / Precaución:** $-24\text{ a } -27\text{ dBm}$
     - 🔴 **Crítica / Atenuada:** $\le -27\text{ dBm}$
  4. **Distancia de Fibra:** Medición precisa en metros ($m$).
  5. **Diagnóstico de Falla / Última Causa de Caída (*Last Down Cause*):**
     - ⚡ **Dying-Gasp:** Corte de suministro eléctrico en casa del cliente (la red de fibra está íntegra).
     - ✂️ **LOSi / LOBi:** Corte físico de fibra óptica o desconexión del cable drop/manga.
     - 🔄 **Manual Reset / Deactivated:** Reinicio manual o bloqueo administrativo.
  6. **Consumo por Cliente (Opcional / Bajo Demanda):** Medición de caudal de tráfico instantáneo y volumen acumulado (GB) por ONT.

#### 🔹 Fase 4: Inteligencia de Agrupación por Bote de Conexión / Caja NAP / Zona
- **Algoritmo de Agrupación Automática:** Análisis y agrupamiento de ONUs por prefijos y etiquetas de zona detectadas en las descripciones (ej. `ZONA_2_POZAS`, `ENTRONQUE_HUIZACHE`, `MANGA_PRINCIPAL`, etc.).
- Permite a los técnicos de campo saber al instante qué botes o splitters tienen afectaciones masivas por falta de luz o cortes de fibra específicos.

#### 🔹 Fase 5: Filtros Rápidos de Diagnóstico en un Clic
- Botones de filtrado rápido sobre la tabla:
  - 🔍 **Todos**
  - 🔴 **Señal Crítica ($\le -27\text{ dBm}$)**
  - ⚡ **Sin Energía Eléctrica (Dying-Gasp)**
  - ✂️ **Cortes de Fibra (LOSi/LOBi)**
  - ⚠️ **Descripciones Duplicadas** (Detección de contratos duplicados o reutilización indebida de ONTs)
  - 🔌 **Fuera de Línea (Offline)**

---

## 🛠️ Comandos de Operación

### Iniciar o Reconstruir el Servicio
```bash
docker compose up -d --build
```

### Reiniciar el Contenedor
```bash
docker compose restart nexusdude-web
```

### Consultar Logs en Tiempo Real
```bash
docker compose logs -f nexusdude-web
```

### Verificación de Estado y Salud (Healthcheck)
```bash
curl http://localhost:8085/api/health
```

---

## 📂 Estructura del Proyecto

```text
nexusdude/
├── app/
│   ├── api/
│   │   ├── auth_routes.py        # Autenticación y validación SSO
│   │   ├── inventory_routes.py   # Endpoints de NetBox y puertos
│   │   ├── maps_routes.py        # CRUD de mapas, nodos, notas y enlaces
│   │   └── zabbix_routes.py      # Telemetría de aristas, nodos, brazos FTTH y Diagnóstico OLT
│   ├── services/
│   │   ├── inventory_service.py  # Sincronización y cables NetBox
│   │   └── zabbix_service.py     # Extracción SNMP Bulkwalk, DDM, GPON, diagnóstico profundo de ONTs
│   ├── static/
│   │   ├── index.html            # UI principal, lienzo, modales y Dashboard de Diagnóstico OLT
│   │   ├── css/style.css         # Estilos visuales The Dude Moderno y tablas de diagnóstico
│   │   └── js/
│   │       ├── api.js            # Cliente REST API autenticado
│   │       └── app.js            # Renderizado Konva, brazos FTTH, eventos y suite de diagnóstico
│   ├── database.py               # Esquema SQLite, migraciones y roles
│   ├── models.py                 # Modelos Pydantic
│   └── main.py                   # Inicialización FastAPI
├── data/
│   └── nexusdude.db              # Base de datos SQLite (persistencia)
├── Dockerfile                    # Definición de contenedor con curl, sqlite3 y snmp
├── docker-compose.yml            # Orquestación de contenedores
├── Template_Huawei_SmartAX_eKit_OLT_SNMP.yaml # Plantilla Zabbix 7.0 para OLT Huawei
└── README.md                     # Documentación técnica completa
```

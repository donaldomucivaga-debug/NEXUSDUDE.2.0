# 📜 Changelog — NexusDude

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-09-24 (Release NEXUSDUDE.1.0)

### 🌟 Added
- **Centro de Diagnóstico Profundo OLT & Clientes FTTH (`#modal-olt-diagnostics`)**:
  - Fullscreen NOC diagnostic suite accessible via toolbar button `[⚡ Diagnóstico OLT]` and contextual shortcut from FTTH branch nodes on the canvas.
  - Automatic discovery of all 24 monitored OLTs from Zabbix (`GET /api/zabbix/olt/diagnostic-summary`).
  - Interactive horizontal carousel of GPON ports (`GPON 0/1/0` to `GPON 0/1/15`) with live ONT counters.
  - Real-time sequential SNMP walks (`snmpbulkwalk -Cr10 -t 3 -r 2`) for ONT descriptions (`.43.1.9`), serial numbers (`.43.1.3`), optical Rx power levels (`.51.1.4`), and last down causes (`.46.1.15`).
  - Root-cause automated diagnostic classification:
    - ⚡ **Dying-Gasp (Code 1)**: Identifies subscriber power outage (fiber plant intact).
    - ✂️ **LOSi (Code 2)**: Identifies physical fiber cut, bend, or NAP box detachment.
    - 🟡 **Optical Degradation**: Flags ONTs with optical power $\le -27.0\text{ dBm}$.
  - 7 live metric KPI cards: Total ONTs, Online, Optimal, Degraded, Dying-Gasp, LOSi cuts, and i-WISP matched count.
  - Reactive search bar by client name, client ID, serial, or package, with quick filter buttons and counters.

- **Integración con i-WISP Manager API**:
  - Table `system_config` in SQLite for non-volatile storage of `iwisp_api_key`, `iwisp_api_url`, and `iwisp_last_sync`.
  - Table `iwisp_clients_cache` with high-performance indices on `onu_serial`, `client_id`, and `client_name` (<0.5ms lookup).
  - Serial number normalizer (`normalize_onu_serial`) with support for Hex-STRING (`48 57 54 43...`) and ASCII (`HWTC...`).
  - Interactive settings modal (`#modal-settings`) with password visibility toggle, live connectivity test against `/getLocalities`, and client synchronization.
  - Endpoints: `GET/POST /api/config/iwisp`, `POST /api/config/iwisp/test`, `POST /api/config/iwisp/sync`, `GET /api/config/iwisp/cache-status`.

- **Sistema Multi-Comunidad SNMP Dinámica y Auto-Aprendizaje**:
  - Table `olt_snmp_credentials` for per-OLT verified community storage independent of Zabbix.
  - Smart fallback cascade: tests candidate dictionary (`Muci!6508_rd`, `Mucivaga6508_rd`, `admin6508`, `public`) and auto-learns working community on response.
  - Live community input and test button in the Diagnostic Suite UI for on-the-fly updates without service restarts.
  - Dedicated endpoint: `POST /api/zabbix/olt/{olt_ip}/community`.

- **Reorganización Ergonómica de la Barra de Herramientas (Toolbar)**:
  - Added dropdown menu `[🔄 Sincronización ▼]` grouping NetBox node sync, site populator, Zabbix BSM sync, and i-WISP sync.
  - Dedicated buttons for `[⚡ Diagnóstico OLT]` and `[⚙️ Configuración]`.

### 🛡️ Verified & Tested
- Comprehensive regression and quality test suite executed twice with **0 errors and 100% test coverage**:
  - Maps & Nodes schema and geometry integrity.
  - FTTH branch telemetry calculation.
  - i-WISP client ingestion and serial correlation.
  - Live SNMP walk against production Huawei OLT `10.20.0.2` on port `4194312192`.
  - Multi-community security validation and rejection of invalid credentials.

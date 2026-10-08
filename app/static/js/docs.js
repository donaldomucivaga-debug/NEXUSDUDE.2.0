/**
 * NexusDude 2.0 - Centro de Documentación & Guía Operativa Interactiva
 * Manual completo de arquitectura, jerarquía de mapas, enlaces y sincronización Zabbix BSM.
 */

(function () {
  'use strict';

  const DOCS_SECTIONS = [
    {
      id: 'quickstart',
      title: '1. Primeros Pasos y Conexión',
      icon: 'fa-plug',
      content: `
        <h2><i class="fas fa-plug"></i> Primeros Pasos: Conexión Inicial con Plataformas</h2>
        <p>NexusDude 2.0 actúa como el núcleo unificador de la infraestructura de red, articulando de manera inteligente tres plataformas esenciales:</p>
        
        <table class="docs-table">
          <thead>
            <tr>
              <th>Plataforma</th>
              <th>Rol en NexusDude</th>
              <th>Mecanismo de Integración</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>NetBox (DCIM)</strong></td>
              <td>Fuente única de la verdad (SSOT): inventario de equipos, sitios, interfaces y cables físicos.</td>
              <td>API REST (Token) + Webhook en tiempo real (<code>/api/webhooks/netbox</code>).</td>
            </tr>
            <tr>
              <td><strong>Zabbix 7.0 (NMS)</strong></td>
              <td>Monitoreo en vivo, telemetría ICMP/SNMP y motor de Business Service Management (BSM).</td>
              <td>API JSON-RPC (<code>/api_jsonrpc.php</code>) estandarizada mediante API Token.</td>
            </tr>
            <tr>
              <td><strong>i-WISP (CRM)</strong></td>
              <td>Base de datos de clientes, contratos y correlación de seriales GPON/ONU.</td>
              <td>API REST i-WISP (API Key) cruzada en diagnóstico de puertos OLT.</td>
            </tr>
          </tbody>
        </table>

        <div class="docs-callout tip">
          <i class="fas fa-route"></i>
          <div>
            <strong>Ruta recomendada para la puesta en marcha inicial:</strong>
            <ol style="margin: 6px 0 0 16px;">
              <li>Abrir <strong>Configuración (⚙)</strong> e ingresar credenciales de NetBox y Zabbix.</li>
              <li>Pulsar los botones de validación <em>"Probar NetBox"</em> y <em>"Probar Zabbix"</em>.</li>
              <li>Ir a la barra lateral, pestaña <strong>Inventario &gt; Sitios NetBox</strong> para confirmar la sincronización.</li>
              <li>Hacer clic en <em>"Auto-generar Sitios"</em> o importar el sitio deseado como mapa.</li>
              <li>Acomodar la topología en el lienzo y trazar las conexiones respetando el flujo <em>Proveedor &rarr; Consumidor</em>.</li>
              <li>Ejecutar <strong>Sincronización Zabbix (BSM)</strong> para construir el árbol de servicios y árbol de causa raíz.</li>
            </ol>
          </div>
        </div>

        <h3>A. Configuración de NetBox</h3>
        <div class="docs-step">
          <div class="docs-step-number">1</div>
          <div class="docs-step-body">
            <div class="docs-step-title">URL Interna de NetBox</div>
            <div>URL utilizada por el contenedor de NexusDude. En despliegues locales sobre la misma red Docker se utiliza <code>http://netbox:8080</code> para latencia cero.</div>
          </div>
        </div>
        <div class="docs-step">
          <div class="docs-step-number">2</div>
          <div class="docs-step-body">
            <div class="docs-step-title">API Token de NetBox</div>
            <div>Token con permisos de lectura y escritura generado en NetBox (<em>Admin &gt; Users &gt; API Tokens</em>). Ejemplo: <code>49oUJOzZDVDrtNu32fYUU2Y...</code>.</div>
          </div>
        </div>
        <div class="docs-step">
          <div class="docs-step-number">3</div>
          <div class="docs-step-body">
            <div class="docs-step-title">URL Externa de NetBox</div>
            <div>Dirección web que abren los navegadores de los operadores (ejemplo: <code>http://10.9.8.52:8089</code>). Se utiliza para los enlaces directos a equipos y cables en el modal de propiedades.</div>
          </div>
        </div>
        <div class="docs-step">
          <div class="docs-step-number">4</div>
          <div class="docs-step-body">
            <div class="docs-step-title">Webhook de NetBox (Sincronización en Tiempo Real)</div>
            <div>Configura en NetBox un Webhook apuntando a <code>http://&lt;nexusdude_ip&gt;:8085/api/webhooks/netbox</code> con eventos de creación/actualización de <em>Sites</em> y <em>Devices</em>. Cuando des de alta un equipo en NetBox, aparecerá en el inventario de NexusDude al instante.</div>
          </div>
        </div>

        <h3>B. Configuración de Zabbix 7.0</h3>
        <div class="docs-step">
          <div class="docs-step-number">1</div>
          <div class="docs-step-body">
            <div class="docs-step-title">URL de Zabbix</div>
            <div>Dirección del frontend web de Zabbix (ejemplo: <code>https://10.9.1.7:8082</code>). NexusDude se comunica internamente mediante <code>/api_jsonrpc.php</code>.</div>
          </div>
        </div>
        <div class="docs-step">
          <div class="docs-step-number">2</div>
          <div class="docs-step-body">
            <div class="docs-step-title">API Token de Zabbix (Recomendado)</div>
            <div>Token de autenticación moderno de Zabbix 7.0 (<em>Users &gt; API tokens</em>). Brinda mayor seguridad, auditoría y no expira con cambios de contraseñas de usuarios.</div>
          </div>
        </div>
        <div class="docs-step">
          <div class="docs-step-number">3</div>
          <div class="docs-step-body">
            <div class="docs-step-title">Botón "Probar Zabbix"</div>
            <div>Valida la conexión invocando <code>apiinfo.version</code> y confirma que el token tiene permisos de lectura sobre hosts y servicios.</div>
          </div>
        </div>

        <h3>C. Integración GPON / OLTs y CRM i-WISP</h3>
        <ul>
          <li><strong>i-WISP API Key:</strong> Llave generada en el CRM para correlacionar clientes con número de contrato y planes de bajada/subida.</li>
          <li><strong>Comunidades SNMP de OLTs:</strong> Permite configurar comunidades SNMP personalizadas por IP (Huawei SmartAX EA5800, VSOL, ZTE) para leer niveles ópticos (RX/TX dBm) de cada ONU directamente desde el puerto PON.</li>
        </ul>
      `
    },
    {
      id: 'topbar',
      title: '2. Barra Superior y Controles',
      icon: 'fa-window-maximize',
      content: `
        <h2><i class="fas fa-window-maximize"></i> Barra Superior (Top Navigation Bar)</h2>
        <p>La barra superior concentra las herramientas de navegación, manipulación del lienzo y sincronizaciones automáticas.</p>

        <table class="docs-table">
          <thead>
            <tr>
              <th style="width: 190px;">Control / Botón</th>
              <th>Función y Descripción</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Logo NexusDude ND2.0</strong></td>
              <td>Regresa a la vista principal y recarga el mapa raíz predeterminado.</td>
            </tr>
            <tr>
              <td><strong>Migas de Pan (Breadcrumbs)</strong></td>
              <td>Muestra la ruta activa en la jerarquía (ej: <code>Core &gt; CERRO AZUL &gt; TORRE NORTE</code>). Puedes hacer clic en cualquier nivel para navegar a ese mapa.</td>
            </tr>
            <tr>
              <td><span class="docs-badge purple">Nuevo Submapa</span></td>
              <td>Crea un nuevo mapa dependiente del actual y añade un acceso directo (📁) en el lienzo.</td>
            </tr>
            <tr>
              <td><span class="docs-badge blue">Conectar Enlace</span></td>
              <td>Activa el modo de enlace interactivo: haz clic en el nodo de origen y luego en el nodo de destino. (Presiona <span class="docs-kbd">Esc</span> para cancelar).</td>
            </tr>
            <tr>
              <td><strong>Imantar (20px)</strong></td>
              <td>Activa o desactiva el ajuste a rejilla (Snap to Grid) para alinear nodos prolijamente a múltiplos de 20 píxeles.</td>
            </tr>
            <tr>
              <td><strong>Añadir Nota</strong></td>
              <td>Inserta una nota adhesiva o cuadro de texto en el lienzo para documentar información técnica, números de rack o advertencias.</td>
            </tr>
            <tr>
              <td><strong>+ Brazo FTTH</strong></td>
              <td>Crea un ramal óptico parametrizable (hilos de fibra, splitters 1:8 / 1:16, cálculo de atenuación teórica).</td>
            </tr>
            <tr>
              <td><strong>Menú Sincronización</strong></td>
              <td>Desplegable con acciones avanzadas:
                <ul style="margin: 4px 0 0 16px;">
                  <li><em>Actualizar con NetBox:</em> Refresca IPs, modelos y estados de los nodos del mapa actual.</li>
                  <li><em>Poblar desde Sitio NetBox:</em> Si el mapa está vinculado a un sitio, importa todos sus equipos organizados por rol.</li>
                  <li><em>Sincronizar Zabbix (BSM):</em> Abre el asistente de sincronización de servicios de negocio hacia Zabbix 7.0.</li>
                  <li><em>Insertar Acceso a Padre:</em> Garantiza la presencia del botón de subida (📁 ⬆) al mapa superior.</li>
                </ul>
              </td>
            </tr>
            <tr>
              <td><strong>Configuración (⚙)</strong></td>
              <td>Abre el panel de credenciales de NetBox, Zabbix, OLTs y CRM i-WISP.</td>
            </tr>
            <tr>
              <td><strong>Nexus Orquestador</strong></td>
              <td>Acceso directo a la plataforma de orquestación de red central (puerto 5001).</td>
            </tr>
            <tr>
              <td><strong>Insignia de Usuario / SSO</strong></td>
              <td>Muestra el usuario autenticado y su rol de acceso (Admin, Server Admin, Operator, Viewer).</td>
            </tr>
            <tr>
              <td><strong>Ayuda (<i class="fas fa-book-open"></i> / F1)</strong></td>
              <td>Abre este centro de documentación interactivo.</td>
            </tr>
          </tbody>
        </table>
      `
    },
    {
      id: 'sidebar',
      title: '3. Barra Lateral y Exploradores',
      icon: 'fa-columns',
      content: `
        <h2><i class="fas fa-columns"></i> Barra Lateral y Sus Pestañas</h2>
        <p>La barra lateral contiene los paneles de administración de inventario físico, jerarquización de la red y herramientas de radiofrecuencia.</p>

        <h3>Pestaña 1: Inventario</h3>
        <ul>
          <li><strong>Equipos NetBox:</strong> Buscador en tiempo real de todos los dispositivos de la empresa. Cuenta con filtros dinámicos por <em>Sitio</em> y por <em>Rol</em> (Router, Switch, OLT, AP, Servidor).</li>
          <li><strong>Arrastrar Equipo al Lienzo:</strong> Puedes tomar cualquier dispositivo del inventario y arrastrarlo directamente al lienzo para instanciarlo físicamente en el mapa activo.</li>
          <li><strong>Sitios NetBox (Mapas Fuente):</strong> Lista todos los sitios registrados en NetBox indicando si ya tienen un mapa fuente generado y cuántos equipos contienen. Puedes crear el mapa con un solo clic.</li>
        </ul>

        <h3>Pestaña 2: Jerarquía (Árbol de Mapas)</h3>
        <p>Muestra el árbol de relaciones de navegación de la red. Incluye:</p>
        <ul>
          <li><strong>Botón de Orden Trifásico:</strong>
            <br>• <span class="docs-badge blue">A-Z (Azul)</span>: Orden alfabético ascendente.
            <br>• <span class="docs-badge amber">Z-A (Ámbar)</span>: Orden alfabético descendente.
            <br>• <span class="docs-badge purple">Libre (Púrpura)</span>: Modo libre personalizado; guarda la posición exacta de cada elemento en la base de datos.
          </li>
          <li><strong>Drag & Drop Avanzado:</strong> Arrastra ítems para moverlos arriba o abajo de otros elementos, o colócalos en el centro de un mapa para convertirlos en submapas subordinados.</li>
          <li><strong>Auto-Scroll Automático:</strong> Al arrastrar un mapa cerca del borde superior o inferior de la lista, el panel se desplaza automáticamente.</li>
          <li><strong>Hover-Expand (650ms):</strong> Mantener el cursor arrastrado sobre una carpeta cerrada la despliega automáticamente sin soltar el elemento.</li>
        </ul>

        <h3>Pestaña 3: Lista de Mapas</h3>
        <p>Visualización en cuadrícula de tarjetas de todos los mapas del sistema con buscador de texto y conteo de nodos. Puedes arrastrar cualquier tarjeta directamente al lienzo para crear un acceso directo de submapa.</p>

        <h3>Pestaña 4: Propiedades</h3>
        <p>Muestra los detalles técnicos del nodo o enlace actualmente seleccionado en el lienzo (IP, modelo, fabricante, interfaces conectadas, telemetría y métricas de tráfico).</p>

        <h3>Pestaña 5: Espectro RF</h3>
        <p>Herramienta para operadores WISP: analiza el uso de canales y frecuencias de radio (2.4 GHz, 5 GHz, 60 GHz) para evitar interferencias entre torres vecinas.</p>
      `
    },
    {
      id: 'canvas',
      title: '4. Lienzo (Canvas) y Operación Gráfica',
      icon: 'fa-project-diagram',
      content: `
        <h2><i class="fas fa-project-diagram"></i> Operación del Lienzo Interactivo (Konva Canvas)</h2>
        <p>El lienzo es el espacio principal de diseño e ingeniería gráfica de red.</p>

        <h3>Navegación y Vista:</h3>
        <ul>
          <li><strong>Zoom:</strong> Gira la rueda del ratón hacia adelante o atrás para acercar o alejar la vista de forma continua.</li>
          <li><strong>Desplazamiento (Pan):</strong> Haz clic en el fondo del lienzo o usa el botón central del ratón y arrastra para desplazarte por la cuadrícula.</li>
          <li><strong>Selección Múltiple:</strong>
            <br>• Mantén <span class="docs-kbd">Shift</span> o <span class="docs-kbd">Ctrl</span> y haz clic en varios nodos.
            <br>• O haz <strong>clic derecho y arrastra</strong> sobre el lienzo para trazar un recuadro de selección múltiple sobre varios equipos a la vez.
          </li>
        </ul>

        <h3>Interacciones Clave:</h3>
        <table class="docs-table">
          <thead>
            <tr>
              <th>Acción</th>
              <th>Gesto / Atajo</th>
              <th>Efecto</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Abrir Submapa</strong></td>
              <td>Doble clic en nodo 📁</td>
              <td>Desciende al submapa hijo y carga su topología.</td>
            </tr>
            <tr>
              <td><strong>Subir al Padre</strong></td>
              <td>Doble clic en nodo 📁 ⬆</td>
              <td>Asciende inmediatamente al mapa superior primario.</td>
            </tr>
            <tr>
              <td><strong>Detalles del Equipo</strong></td>
              <td>Doble clic en equipo</td>
              <td>Abre el modal de propiedades, interfaces y telemetría en vivo.</td>
            </tr>
            <tr>
              <td><strong>Eliminar Elementos</strong></td>
              <td><span class="docs-kbd">Supr</span> o <span class="docs-kbd">Backspace</span></td>
              <td>Elimina los nodos o enlaces seleccionados tras confirmación.</td>
            </tr>
            <tr>
              <td><strong>Cancelar Modo</strong></td>
              <td><span class="docs-kbd">Esc</span></td>
              <td>Cancela el modo de enlace o deselecciona los elementos activos.</td>
            </tr>
            <tr>
              <td><strong>Conexión Múltiple</strong></td>
              <td><span class="docs-kbd">Enter</span></td>
              <td>Confirma la creación de enlaces desde un nodo origen hacia múltiples destinos seleccionados.</td>
            </tr>
            <tr>
              <td><strong>Arrastrar Mapa al Lienzo</strong></td>
              <td>Arrastrar desde la barra lateral</td>
              <td>Crea instantáneamente un nodo de acceso directo de submapa (📁) en las coordenadas donde sueltes el cursor.</td>
            </tr>
          </tbody>
        </table>
      `
    },
    {
      id: 'hierarchy',
      title: '5. Jerarquización y Mapas Fuente',
      icon: 'fa-sitemap',
      content: `
        <h2><i class="fas fa-sitemap"></i> Jerarquización Correcta: Modelo de Mapas Fuente y The Dude</h2>
        <p>NexusDude 2.0 adopta el modelo probado de <strong>MikroTik The Dude</strong> combinado con la consistencia física de <strong>NetBox</strong>. Es fundamental comprender la diferencia entre un <em>Mapa Fuente</em> y un <em>Acceso Jerárquico</em>:</p>

        <div class="docs-callout tip">
          <i class="fas fa-layer-group"></i>
          <div>
            <strong>1. Mapa Fuente (Entidad Real):</strong><br>
            Es el registro único del mapa en la base de datos. Contiene los equipos físicos reales, sus coordenadas X/Y y sus conexiones de cables. Cada sitio de NetBox posee <strong>un único mapa fuente</strong> que representa ese espacio físico en la red.<br><br>
            <strong>2. Acceso Jerárquico (Navegación / The Dude Shortcut):</strong><br>
            Es un enlace de navegación en el árbol lateral o un nodo (📁) en el lienzo. Un mismo mapa fuente puede estar referenciado en <strong>múltiples submapas y carpetas</strong> si la topología lo requiere (por ejemplo, una torre que brinda servicio a dos rutas redundantes de fibra).
          </div>
        </div>

        <h3>¿Por qué es crucial editar el Mapa Fuente y no duplicarlo?</h3>
        <ul>
          <li><strong>Consistencia Total:</strong> Si mueves la posición de un switch, conectas un nuevo cable de fibra o actualizas la IP de un router dentro de un mapa fuente, <strong>el cambio se refleja en todos los puntos donde esté proyectado ese mapa</strong>.</li>
          <li><strong>Prevención de Conflictos en Zabbix:</strong> Si existieran mapas duplicados con los mismos equipos, Zabbix intentaría crear servicios BSM en conflicto para el mismo host. Al usar accesos múltiples sobre el mapa fuente único, Zabbix mantiene un árbol limpio sin redundancias ficticias.</li>
          <li><strong>Nombres Únicos vs Personalizados:</strong> Los mapas sincronizados con sitios de NetBox son únicos por su nombre de sitio. En cambio, los mapas personalizados o lógicos que crees manualmente pueden tener nombres repetidos si así lo decides.</li>
        </ul>

        <h3>Operación de Arrastrar y Soltar (Desktop File Manager UX):</h3>
        <p>Al arrastrar un ítem en la lista de jerarquía, el cursor identifica con precisión la zona donde deseas ubicarlo:</p>
        <table class="docs-table">
          <thead>
            <tr>
              <th>Zona de Detección</th>
              <th>Línea Guía Visual</th>
              <th>Resultado</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Tercio Superior</strong></td>
              <td>Línea azul arriba (<code>⬆ Insertar antes</code>)</td>
              <td>Reordena el mapa colocándolo inmediatamente antes del destino al mismo nivel.</td>
            </tr>
            <tr>
              <td><strong>Tercio Inferior</strong></td>
              <td>Línea azul abajo (<code>⬇ Insertar después</code>)</td>
              <td>Reordena el mapa colocándolo inmediatamente después del destino al mismo nivel.</td>
            </tr>
            <tr>
              <td><strong>Tercio Central</strong></td>
              <td>Resaltado morado (<code>📁 Asignar como subordinado</code>)</td>
              <td>Convierte el mapa en <strong>submapa hijo</strong> del elemento seleccionado.</td>
            </tr>
            <tr>
              <td><strong>Presionando Ctrl + Arrastrar</strong></td>
              <td>Indicador de copia (<code>🔗 Nuevo acceso</code>)</td>
              <td>Crea un acceso adicional en el árbol hacia el mismo mapa fuente en lugar de moverlo.</td>
            </tr>
          </tbody>
        </table>

        <div class="docs-callout important">
          <i class="fas fa-arrows-alt-v"></i>
          <div>
            <strong>Auto-Scroll y Auto-Despliegue al Arrastrar:</strong><br>
            • Si te acercas a menos de 45 píxeles del borde superior o inferior de la lista, el contenedor se desplazará automáticamente para que alcances elementos lejanos.<br>
            • Si mantienes el cursor sobre una carpeta cerrada durante 650 milisegundos, esta se desplegará sola automáticamente.
          </div>
        </div>
      `
    },
    {
      id: 'zabbix-bsm',
      title: '6. Servicios en Zabbix (BSM)',
      icon: 'fa-satellite-dish',
      content: `
        <h2><i class="fas fa-satellite-dish"></i> Sincronización con Zabbix Services (BSM)</h2>
        <p>NexusDude 2.0 integra un motor avanzado de <strong>Business Service Management (BSM)</strong> para Zabbix 7.0 que traduce la topología gráfica en un <strong>árbol puro de Servicios Host-a-Host</strong>.</p>

        <h3>Análisis de Causa Raíz (Root Cause Analysis - RCA):</h3>
        <p>Si el router principal de una torre pierde energía eléctrica, todos los switches, antenas y clientes conectados detrás de él se apagarán al mismo tiempo. En un sistema convencional, esto detonaría decenas de alarmas independientes.</p>
        <p>Gracias al motor BSM de NexusDude:</p>
        <ul>
          <li>El flujo de conexión representa la relación: <strong>Proveedor (Padre) &rarr; Consumidor (Hijo)</strong>.</li>
          <li>En Zabbix, el servicio del equipo hijo depende jerárquicamente del padre.</li>
          <li>Al fallar el router padre, Zabbix identifica la <strong>Causa Raíz en el Padre</strong> y clasifica la caída de los hijos como dependiente, evitando tormentas de alertas y falsos positivos.</li>
        </ul>

        <h3>Reglas Críticas para una Sincronización Exitosa:</h3>
        <div class="docs-callout important">
          <i class="fas fa-clipboard-check"></i>
          <div>
            <strong>Checklist Obligatorio para el Operador:</strong>
            <ol style="margin: 6px 0 0 16px;">
              <li><strong>Coincidencia de Hosts en Zabbix:</strong> NexusDude busca el host en Zabbix comparando: (1) Dirección IP, (2) Nombre visible exacto, o (3) Nombre normalizado con guiones bajos. Si el equipo no está en Zabbix, se marcará con advertencia y no recibirá tags de problemas.</li>
              <li><strong>Solo Equipos Conectados:</strong> Los nodos flotantes sin ningún enlace físico o de servicio no se incluyen en el árbol de servicios de Zabbix.</li>
              <li><strong>Dirección del Enlace:</strong> La flecha debe apuntar desde el equipo que entrega el servicio hacia el equipo que lo consume. Puedes invertir el sentido en las propiedades del enlace (<em>target_to_source</em>) con un solo clic.</li>
              <li><strong>Enlaces Solo Visuales:</strong> Si marcas un enlace como <em>"Solo Visual / No sincronizar con Zabbix"</em>, ese enlace se omitirá del cálculo de dependencias de Zabbix.</li>
              <li><strong>Enlaces Inter-Mapa:</strong> Cuando un switch en el Mapa A alimenta a un equipo dentro del Submapa B, utiliza la herramienta de enlace inter-mapa para seleccionar el dispositivo receptor específico.</li>
            </ol>
          </div>
        </div>

        <h3>Alcances de Sincronización:</h3>
        <ul>
          <li><strong>🌿 Rama Actual:</strong> Sincroniza únicamente el mapa activo y sus submapas dependientes. Es la opción recomendada para actualizar una zona o torre sin afectar el resto del sistema.</li>
          <li><strong>🌐 Todo el Sistema (Global):</strong> Sincroniza la red completa (+260 mapas).</li>
          <li><strong>Limpiar Servicios Previos:</strong> Elimina los servicios anteriores de la rama para reconstruirlos desde cero, ideal si reestructuraste profundamente las dependencias.</li>
        </ul>
      `
    },
    {
      id: 'sync-cables',
      title: '7. Cables Físicos y NetBox Sync',
      icon: 'fa-network-wired',
      content: `
        <h2><i class="fas fa-network-wired"></i> Sincronización de Cables Físicos con NetBox</h2>
        <p>NexusDude 2.0 mantiene una correlación bidireccional con el modelo de cableado de NetBox (<code>/api/dcim/cables/</code> e interfaces).</p>

        <h3>Propiedades de Enlace (Doble Clic en cualquier Enlace):</h3>
        <ul>
          <li><strong>Flujo del Servicio:</strong> Define si <em>Nodo A &rarr; Nodo B</em> o <em>Nodo B &rarr; Nodo A</em> es el enlace de alimentación principal.</li>
          <li><strong>Interfaces de Conexión:</strong> Muestra los nombres de puertos en ambos extremos (ej: <code>ether1</code> &harr; <code>ge-0/0/0</code>).</li>
          <li><strong>Tipo de Medio Físico:</strong> Fibra monomodo (SMF), Fibra multimodo (MMF), Cobre Cat6, Enlace Inalámbrico o Enlace Virtual.</li>
          <li><strong>Telemetría en Vivo:</strong> Consulta en tiempo real las métricas de tráfico en bps y estado de la interfaz en Zabbix.</li>
        </ul>

        <h3>Auto-Generación Masiva de Sitios:</h3>
        <p>Desde la barra lateral (<em>Inventario &gt; Sitios</em>), el botón <strong>Auto-generar Sitios</strong> permite procesar masivamente todos los sitios de NetBox:</p>
        <ul>
          <li>Crea automáticamente el mapa fuente del sitio.</li>
          <li>Ubica los equipos organizados en cuadrícula según su rol (Routers arriba, Switches al centro, APs y OLTs abajo).</li>
          <li>Traza automáticamente los cables físicos entre interfaces registradas en NetBox.</li>
        </ul>
      `
    },
    {
      id: 'rbac',
      title: '8. Usuarios y Permisos (RBAC)',
      icon: 'fa-user-shield',
      content: `
        <h2><i class="fas fa-user-shield"></i> Control de Acceso Basado en Roles (RBAC)</h2>
        <p>NexusDude 2.0 cuenta con autenticación segura compartida mediante SSO Delegado con Nexus Orchestrator y cuatro niveles de roles:</p>

        <table class="docs-table">
          <thead>
            <tr>
              <th>Rol</th>
              <th>Nivel de Acceso</th>
              <th>Permisos Clave</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="docs-badge purple">admin</span></td>
              <td>Administrador Total</td>
              <td>Gestión de usuarios, cambio de credenciales globales, borrado masivo de mapas y servicios.</td>
            </tr>
            <tr>
              <td><span class="docs-badge blue">server_admin</span></td>
              <td>Administrador Técnico</td>
              <td>Sincronización completa con NetBox y Zabbix BSM, configuración de OLTs y enlaces inter-mapa.</td>
            </tr>
            <tr>
              <td><span class="docs-badge green">operator</span></td>
              <td>Operador de Red</td>
              <td>Creación y edición de mapas, acomodo de topologías, trazado de enlaces, notas y brazos FTTH.</td>
            </tr>
            <tr>
              <td><span class="docs-badge amber">viewer</span></td>
              <td>Visualizador (Solo Lectura)</td>
              <td>Navegación interactiva por mapas, inspección de telemetría y consulta de diagnósticos sin permisos de modificación.</td>
            </tr>
          </tbody>
        </table>
      `
    },
    {
      id: 'faq',
      title: '9. Preguntas Frecuentes y Atajos',
      icon: 'fa-question-circle',
      content: `
        <h2><i class="fas fa-question-circle"></i> Preguntas Frecuentes y Atajos Rápidos</h2>

        <h3>Tabla de Atajos de Teclado:</h3>
        <table class="docs-table">
          <thead>
            <tr>
              <th>Atajo</th>
              <th>Función</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="docs-kbd">F1</span></td>
              <td>Abre este Centro de Ayuda y Documentación.</td>
            </tr>
            <tr>
              <td><span class="docs-kbd">Esc</span></td>
              <td>Cierra modales activos, cancela modo de conexión o deselecciona nodos.</td>
            </tr>
            <tr>
              <td><span class="docs-kbd">Supr</span> / <span class="docs-kbd">Backspace</span></td>
              <td>Elimina los elementos seleccionados en el lienzo.</td>
            </tr>
            <tr>
              <td><span class="docs-kbd">Enter</span></td>
              <td>Confirma la creación de enlaces en selecciones múltiples.</td>
            </tr>
            <tr>
              <td><span class="docs-kbd">Shift</span> + Clic</td>
              <td>Añade o quita un nodo de la selección múltiple.</td>
            </tr>
            <tr>
              <td><span class="docs-kbd">Ctrl</span> + Arrastrar en Jerarquía</td>
              <td>Crea un nuevo acceso directo (acceso adicional) hacia el mismo mapa fuente.</td>
            </tr>
            <tr>
              <td>Clic Derecho + Arrastrar</td>
              <td>Toca y selecciona múltiples nodos dentro del área del recuadro.</td>
            </tr>
          </tbody>
        </table>

        <h3>Solución de Problemas Frecuentes:</h3>
        <div class="docs-callout tip">
          <i class="fas fa-lightbulb"></i>
          <div>
            <strong>¿Por qué un equipo aparece con advertencia en Zabbix BSM?</strong><br>
            Asegúrate de que el equipo tenga una IP registrada y que dicha IP o nombre coincida con un Host dado de alta en Zabbix 7.0.
          </div>
        </div>

        <div class="docs-callout tip">
          <i class="fas fa-lightbulb"></i>
          <div>
            <strong>¿Qué ocurre si elimino un submapa de la lista de Jerarquía?</strong><br>
            Al pulsar <em>Quitar Acceso</em>, solo se retira ese punto de navegación en la carpeta actual; el mapa fuente y sus equipos se conservan intactos. Si pulsas <em>Eliminar Mapa</em>, se borrará definitivamente el mapa fuente y todos sus accesos.
          </div>
        </div>
      `
    }
  ];

  class DocsManager {
    constructor() {
      this.overlay = null;
      const savedSection = localStorage.getItem('nexusdude_docs_section');
      this.currentSectionId = DOCS_SECTIONS.some(s => s.id === savedSection) ? savedSection : DOCS_SECTIONS[0].id;
      this.init();
    }

    init() {
      this.ensureTopBarButton();

      window.addEventListener('keydown', (e) => {
        if (e.key === 'F1') {
          e.preventDefault();
          this.open();
        } else if (e.key === 'Escape' && this.isOpen()) {
          this.close();
        }
      });
    }

    ensureTopBarButton() {
      const existingBtn = document.getElementById('btn-open-docs');
      if (existingBtn) {
        existingBtn.onclick = (e) => {
          if (e) { e.preventDefault(); e.stopPropagation(); }
          this.open();
        };
        return;
      }

      const userSection = document.querySelector('.user-section');
      if (!userSection) return;

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.id = 'btn-open-docs';
      btn.className = 'btn-docs-help';
      btn.title = 'Manual de Operaciones, Jerarquía y Guía Zabbix (F1)';
      btn.innerHTML = '<i class="fas fa-book-open"></i><span>Ayuda</span>';

      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.open();
      });

      const badge = document.getElementById('user-badge');
      if (badge) {
        userSection.insertBefore(btn, badge);
      } else {
        userSection.appendChild(btn);
      }
    }

    ensureModalDOM() {
      if (this.overlay) return;

      this.overlay = document.createElement('div');
      this.overlay.className = 'docs-modal-overlay';
      this.overlay.id = 'docs-modal-overlay';

      this.overlay.innerHTML = `
        <div class="docs-modal-container">
          <div class="docs-modal-header">
            <div class="docs-header-title">
              <i class="fas fa-book-reader"></i>
              <span>Centro de Ayuda y Documentación</span>
              <span class="docs-header-badge">NexusDude 2.0</span>
            </div>
            <button class="docs-modal-close" id="btn-close-docs" title="Cerrar (Esc)">&times;</button>
          </div>
          <div class="docs-modal-body">
            <div class="docs-nav-sidebar">
              <div class="docs-search-wrapper">
                <i class="fas fa-search docs-search-icon"></i>
                <input type="text" class="docs-search-input" id="docs-search-input" placeholder="Buscar en documentación...">
              </div>
              <div class="docs-nav-list" id="docs-nav-list">
                <!-- Ítems generados dinámicamente -->
              </div>
            </div>
            <div class="docs-content-area" id="docs-content-area">
              <!-- Secciones generadas dinámicamente -->
            </div>
          </div>
        </div>
      `;

      document.body.appendChild(this.overlay);

      this.overlay.addEventListener('click', (e) => {
        if (e.target === this.overlay) this.close();
      });

      const btnClose = this.overlay.querySelector('#btn-close-docs');
      if (btnClose) {
        btnClose.addEventListener('click', () => this.close());
      }

      const navList = this.overlay.querySelector('#docs-nav-list');
      const contentArea = this.overlay.querySelector('#docs-content-area');

      DOCS_SECTIONS.forEach((sec) => {
        const navItem = document.createElement('div');
        navItem.className = `docs-nav-item ${sec.id === this.currentSectionId ? 'active' : ''}`;
        navItem.dataset.sectionId = sec.id;
        navItem.innerHTML = `<i class="fas ${sec.icon}"></i><span>${sec.title}</span>`;
        navItem.addEventListener('click', () => this.switchSection(sec.id));
        navList.appendChild(navItem);

        const secDiv = document.createElement('div');
        secDiv.className = `docs-section ${sec.id === this.currentSectionId ? 'active' : ''}`;
        secDiv.id = `docs-sec-${sec.id}`;
        secDiv.innerHTML = sec.content;
        contentArea.appendChild(secDiv);
      });

      const searchInput = this.overlay.querySelector('#docs-search-input');
      if (searchInput) {
        searchInput.addEventListener('input', (e) => {
          const q = (e.target.value || '').trim().toLowerCase();
          const items = navList.querySelectorAll('.docs-nav-item');
          items.forEach((it) => {
            const sec = DOCS_SECTIONS.find(s => s.id === it.dataset.sectionId);
            if (!sec) return;
            const match = sec.title.toLowerCase().includes(q) || sec.content.toLowerCase().includes(q);
            it.style.display = match ? 'flex' : 'none';
          });
        });
      }
    }

    switchSection(sectionId) {
      if (!DOCS_SECTIONS.some(s => s.id === sectionId)) {
        sectionId = DOCS_SECTIONS[0]?.id || 'quickstart';
      }

      this.currentSectionId = sectionId;
      localStorage.setItem('nexusdude_docs_section', sectionId);

      if (!this.overlay) return;

      const navItems = this.overlay.querySelectorAll('.docs-nav-item');
      navItems.forEach(it => {
        it.classList.toggle('active', it.dataset.sectionId === sectionId);
      });

      const sections = this.overlay.querySelectorAll('.docs-section');
      sections.forEach(sec => {
        sec.classList.toggle('active', sec.id === `docs-sec-${sectionId}`);
      });

      const contentArea = this.overlay.querySelector('#docs-content-area');
      if (contentArea) contentArea.scrollTop = 0;
    }

    open(sectionId) {
      this.ensureModalDOM();
      if (sectionId && DOCS_SECTIONS.some(s => s.id === sectionId)) {
        this.switchSection(sectionId);
      } else {
        this.switchSection(this.currentSectionId);
      }
      this.overlay.classList.add('active');
    }

    close() {
      if (this.overlay) {
        this.overlay.classList.remove('active');
      }
    }

    isOpen() {
      return this.overlay && this.overlay.classList.contains('active');
    }
  }

  // Exponer función global abierta de inmediato
  window.openDocs = function (sectionId) {
    if (!window.docsManager) {
      window.docsManager = new DocsManager();
    }
    window.docsManager.open(sectionId);
  };

  // Inicialización automática
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      if (!window.docsManager) {
        window.docsManager = new DocsManager();
      }
    });
  } else {
    window.docsManager = new DocsManager();
  }
})();

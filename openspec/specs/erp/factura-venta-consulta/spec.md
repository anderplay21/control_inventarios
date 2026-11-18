# factura-venta-consulta Specification

## Purpose

Da acceso a las facturas de venta desde el menú Ingresos del ERP, muestra su listado y su detalle, y define qué acciones puede ejecutar la persona usuaria según el estado de cobro, los pagos y el estado ante la DIAN.

## Requirements

### Requirement: Acceso desde el menú Ingresos
El menú del ERP SHALL mostrar la sección «Ingresos» con la opción «Factura de venta» para quien tenga el permiso del módulo ERP, y al elegirla MUST cargar el listado con la acción de crear una nueva.

#### Scenario: Opción visible
- **WHEN** una persona con acceso al ERP abre el menú
- **THEN** ve «Ingresos» con la opción «Factura de venta»

#### Scenario: Vista inicial
- **WHEN** elige «Factura de venta»
- **THEN** ve el listado de facturas existentes y el botón para crear una nueva

#### Scenario: Sin permiso
- **WHEN** una persona sin acceso al módulo ERP intenta entrar a la ruta de Factura de venta
- **THEN** el sistema niega el acceso igual que en el resto del ERP

### Requirement: Listado cronológico
El listado SHALL mostrar las facturas de la más reciente a la más antigua, con número, cliente, fecha de creación, fecha de vencimiento, total y estado, y MUST permitir paginar sin traer todas las filas.

#### Scenario: Orden por defecto
- **WHEN** se abre el listado
- **THEN** la primera fila es la factura más reciente y las siguientes siguen en orden descendente

#### Scenario: Volumen grande
- **WHEN** la empresa tiene decenas de miles de facturas
- **THEN** el listado carga por páginas y el total sigue disponible sin recorrer todas las filas

### Requirement: Estado de cobro visible
El listado SHALL mostrar «Por cobrar» cuando la factura tiene saldo pendiente y «Cobrada» cuando el saldo es cero, e indicar «Vencida» cuando está por cobrar y su fecha de vencimiento ya pasó. El vencimiento MUST calcularse al consultar y no guardarse.

#### Scenario: Por cobrar
- **WHEN** una factura emitida o parcialmente pagada tiene saldo mayor que cero
- **THEN** su estado visible es «Por cobrar»

#### Scenario: Cobrada
- **WHEN** el saldo de una factura llega a cero
- **THEN** su estado visible es «Cobrada»

#### Scenario: Vencida sin tarea de fondo
- **WHEN** pasa la fecha de vencimiento de una factura con saldo
- **THEN** el listado la marca como vencida sin que se haya ejecutado ningún proceso programado

### Requirement: Acción rápida de pago
En cada fila con estado «Por cobrar», el listado SHALL mostrar la acción «Agregar pago». En facturas cobradas, anuladas o en borrador no MUST mostrarla.

#### Scenario: Fila por cobrar
- **WHEN** una fila tiene saldo pendiente
- **THEN** muestra el botón «Agregar pago»

#### Scenario: Fila cobrada o anulada
- **WHEN** una fila está cobrada o anulada
- **THEN** no muestra el botón

### Requirement: Detalle completo
Al seleccionar una factura, el sistema SHALL abrir su detalle con todos los datos del documento, sus líneas, totales, retenciones, pagos aplicados, notas asociadas, adjuntos y el historial de movimientos.

#### Scenario: Historial trazable
- **WHEN** una factura tiene un pago, una nota crédito y una retención registrados
- **THEN** el detalle muestra cada uno vinculado al documento, con su fecha y su responsable

### Requirement: Matriz de acciones por estado
El detalle SHALL habilitar o restringir cada acción según una única regla definida en el servidor, y la respuesta de detalle MUST indicar qué acciones están permitidas. Las acciones de exportación y soporte no MUST depender del estado de cobro.

#### Scenario: Factura cobrada
- **WHEN** se abre el detalle de una factura cobrada
- **THEN** «Agregar pago», «Aplicar anticipo», «Cerrar sin pago», «Editar» y «Anular» no están disponibles, y «Imprimir», «Descargar PDF», «Clonar» y «Adjuntar archivos» sí

#### Scenario: Factura anulada
- **WHEN** se abre el detalle de una factura anulada
- **THEN** «Editar» y todas las acciones contables o de cartera están deshabilitadas

#### Scenario: Acción ejecutada sin estar permitida
- **WHEN** alguien envía directamente una acción que la matriz no permite para ese estado
- **THEN** el servidor la rechaza con un mensaje que explica el motivo, aunque el botón no se haya mostrado

#### Scenario: Aplicar anticipo con saldo a favor
- **WHEN** se abre el detalle de una factura con saldo y el cliente tiene anticipos por aplicar en la moneda de la factura
- **THEN** «Aplicar anticipo» está habilitada

#### Scenario: Aplicar anticipo diferido
- **WHEN** se abre el detalle de una factura con saldo y el cliente no tiene anticipos por aplicar en la moneda de la factura
- **THEN** «Aplicar anticipo» aparece deshabilitada con el motivo: el cliente no tiene anticipos por aplicar

### Requirement: Exportación y envío
El detalle SHALL permitir imprimir, descargar el PDF y compartir el documento, y el documento emitido MUST mostrar su número, la resolución, el CUFE y las condiciones de pago.

#### Scenario: Descargar PDF
- **WHEN** se pulsa «Descargar PDF» en una factura emitida
- **THEN** se descarga un PDF con el número, el cliente, las líneas, los totales, la resolución y el CUFE

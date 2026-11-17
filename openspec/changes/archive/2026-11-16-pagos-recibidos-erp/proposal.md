# Proposal

## Why

Hoy el ERP solo registra pagos de una factura a la vez: `ERPPago` apunta a una sola factura, su número se arma como `RC-{factura}-{n}` y no hay un comprobante de recibo de caja que agrupe varias facturas, ni forma de registrar anticipos u otros ingresos. Además, el recaudo actual no mueve el saldo de la cuenta bancaria ni deja movimiento bancario. El documento «Módulo ERP: Ingresos — Submódulo Pagos Recibidos» (v1.0, 16 de noviembre de 2026) pide un espacio único para registrar, consultar y organizar todo el dinero que entra a la empresa, con trazabilidad entre cartera y tesorería.

## What Changes

- Nuevo submenú **Ingresos > Pagos recibidos** con listado, filtros combinables, detalle y botón **+ Nuevo ingreso**.
- Nuevo documento **recibo de caja** con consecutivo único por empresa. Cada recibo pertenece a un tercero registrado (`tercero_id` obligatorio), tiene una cuenta bancaria de destino, fecha, forma y medio de pago, moneda, centro de costo, notas visibles en el PDF y comentarios internos.
- Tres tipos de ingreso:
  - **Pago a factura**: reparte el dinero entre una o varias facturas por cobrar del cliente, con pagos totales o abonos.
  - **Anticipo**: dinero recibido antes de facturar. Se registra como pasivo en la 280505 «Anticipos y avances recibidos de clientes» y deja un saldo a favor del cliente.
  - **Otros ingresos**: el usuario elige directamente la cuenta contable del crédito.
- Retenciones en el recibo, elegidas de una lista desplegable (retención en la fuente, de IVA y de ICA), solo para *Anticipo* y *Otros ingresos*. En *Pago a factura* las retenciones siguen viviendo en la factura y el recibo solo las muestra.
- **Aplicar anticipo** desde la factura de venta: cruza el saldo a favor contra la cartera de la factura y traslada a la factura la referencia a las retenciones que se practicaron sobre el anticipo. Se habilita la acción que `factura-de-venta-erp` dejó diferida. También se puede **desaplicar**.
- Cada recibo crea su movimiento bancario y suma su valor al saldo contable de la cuenta. Anular el recibo lo reversa todo: asiento, movimiento y saldos de las facturas.
- Estados del recibo: **Aplicado**, **Pendiente por aplicar** (anticipo con saldo) y **Anulado**.
- El pago rápido de la factura de venta y `POST /erp/pagos` (tipo cobro) pasan a crear un recibo de una sola línea, con el consecutivo nuevo. **BREAKING** para quien espere números `RC-{factura}-{n}` en pagos nuevos; los pagos existentes conservan su número.
- PDF del recibo de caja, con notas y retenciones y sin comentarios internos.
- Los pagos de cobro que ya existen se agrupan en recibos al arrancar, para que el listado quede completo.

Fuera de alcance: devolución de anticipos al cliente, anticipos en moneda extranjera con diferencia en cambio, conciliación bancaria automática contra extractos y pagos recibidos del POS (siguen con su flujo).

## Capabilities

### New Capabilities
- `erp/pagos-recibidos-consulta`: acceso por el menú Ingresos, listado, filtros, detalle y PDF del recibo de caja.
- `erp/pagos-recibidos-registro`: creación del recibo en sus tres tipos, validaciones, retenciones, consecutivo, asiento, movimiento bancario y anulación.
- `erp/anticipos-clientes`: saldo a favor por cliente, aplicación y desaplicación de anticipos sobre facturas de venta, y traslado de retenciones.

### Modified Capabilities
<!-- Ninguna en `openspec/specs/` (está vacío). Las reglas de pago de `erp/factura-venta-recaudo` y la acción «Aplicar anticipo» de `erp/factura-venta-consulta` viven todavía en el cambio en curso `factura-de-venta-erp`. Este cambio las extiende desde sus capacidades nuevas y debe archivarse después de aquel. -->

## Impact

- **Backend:**
  - `core/erp_recibos.py` (nuevo): servicio de recibos, anticipos y anulación.
  - `api/v1/endpoints/erp_recibos.py` (nuevo): router `/erp/ingresos/pagos-recibidos`, registrado en `api/v1/router.py` bajo el módulo `erp`.
  - `core/erp_ventas.py`: `registrar_recaudo` delega en el servicio de recibos, la matriz habilita `aplicar_anticipo` y `saldo_actual` incluye las aplicaciones de anticipo.
  - `api/v1/endpoints/erp.py`: `POST /erp/pagos` de tipo cobro delega en el servicio.
  - `core/erp_motor.py`: eventos `ANTICIPO_CLIENTE`, `OTRO_INGRESO` y `APLICACION_ANTICIPO`.
  - `core/erp_semilla.py`: cuentas 28, 2805 y 280505 y las reglas de los eventos nuevos.
  - `core/erp_ventas_pdf.py`: PDF del recibo.
  - `infrastructure/models`: tablas de recibo, líneas de otros ingresos, retenciones y comentarios; `recibo_id` y `origen` en `ERPPago`.
  - `main.py`: `CREATE TABLE`/`ALTER` al arranque y el agrupamiento de los pagos anteriores.
- **Frontend:** `Sidebar.tsx` y `App.tsx` (entrada y ruta), página nueva `ERPPagosRecibidos.tsx`, `ERPFacturaVenta.tsx` (aplicar y desaplicar anticipo, enlace al recibo desde el pago) y los textos en los archivos de idioma.
- **Datos:** tablas y columnas nuevas, todas aditivas. Los pagos anteriores ganan un recibo con su número original.
- **Dependencias:** requiere `factura-de-venta-erp` implementado. El ERP sigue sin depender de WMS ni POS.

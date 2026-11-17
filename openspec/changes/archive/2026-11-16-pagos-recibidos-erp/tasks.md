# Tasks

## 1. Esquema y contabilidad base

- [x] 1.1 Agregar los modelos `ERPReciboCaja`, `ERPReciboLineaOtro`, `ERPReciboRetencion` y `ERPReciboComentario`, y las columnas `recibo_id` y `origen` en `ERPPago` (design §2); exportarlos en `infrastructure/models/__init__.py`. Verificar que `python -c "import app.infrastructure.models"` importa sin error.
- [x] 1.2 Agregar en `main.py` el `CREATE TABLE IF NOT EXISTS` y el `ADD COLUMN IF NOT EXISTS` de lo anterior. Verificar arrancando dos veces seguidas contra la base de desarrollo, sin errores y con las tablas creadas (`\d erp_recibos_caja`).
- [x] 1.3 Generalizar `erp_motor.siguiente_numero` para aceptar un prefijo explícito y agregar `numero_recibo(db, empresa_id, anio)` con el talonario `RCJ`. Verificar con una prueba que dos llamadas concurrentes dan números distintos y consecutivos, y que los comprobantes siguen con `RC`, `CE` y `CD`.
- [x] 1.4 Agregar `ANTICIPO_CLIENTE`, `OTRO_INGRESO` y `APLICACION_ANTICIPO` a `NATURALEZA_POR_EVENTO`, y en `erp_semilla` las cuentas 28, 2805 y 280505 con sus reglas (design §5). Verificar que sembrar dos veces no duplica filas y que una regla editada por la empresa no se pisa.

## 2. Servicio de recibos

- [x] 2.1 Crear `core/erp_recibos.py` con los esquemas de entrada y `crear(db, entrada, usuario)` para *Pago a factura*: tercero obligatorio, facturas de la misma moneda, montos > 0 y ≤ saldo, orden de bloqueos (design §7), `ERPPago` por factura, asiento `RECAUDO_CLIENTE`, movimiento bancario, `saldo_contable`, consecutivo y estado de las facturas. Verificar con pruebas de pago total más abono en un recibo, sobrepago rechazado sin cambios y período cerrado sin cambios.
- [x] 2.2 Extender `crear` para *Anticipo* y *Otros ingresos*: retenciones sugeridas con `erp_impuestos.calcular` y editables, neto > 0, cuentas de movimiento obligatorias en otros ingresos y asientos `ANTICIPO_CLIENTE` y `OTRO_INGRESO`. Verificar con pruebas el asiento de un otro ingreso de 1.000.000 con 4% de retención (banco 960.000, 135515 40.000, 421005 1.000.000), el rechazo de una cuenta agrupadora y un anticipo que queda `POR_APLICAR`.
- [x] 2.3 Implementar `anular(db, recibo_id, motivo, usuario)`: reverso, pagos `ANULADO`, saldos de facturas recalculados con `saldo_actual`, movimiento contrario, y rechazos por factura castigada o anulada, anticipo con aplicaciones o falta de motivo. Verificar con pruebas de cada caso del spec «Anulación del recibo».
- [x] 2.4 Implementar `aplicar_anticipo` y `desaplicar_anticipo` (design §8 y §9), con límites, el asiento `APLICACION_ANTICIPO`, el recálculo de `saldo_por_aplicar` y del estado, la advertencia por retenciones propias de la factura y `retenciones_anticipo` proporcionales. Verificar con pruebas de aplicación parcial, anticipo consumido, aplicación que supera el saldo, desaplicación y el rechazo sobre una factura castigada.
- [x] 2.5 Implementar `saldo_a_favor(db, tercero_id)` por moneda y `facturas_por_cobrar(db, tercero_id, moneda)` con el respaldo por NIT normalizado (design §12). Verificar con una prueba que una factura antigua sin `tercero_id` aparece por NIT y que una en USD no aparece en COP.

## 3. Integración con factura de venta y pagos existentes

- [x] 3.1 Hacer que `erp_ventas.registrar_recaudo` construya un recibo de una línea y delegue en `erp_recibos.crear`, y que `POST /erp/pagos` de tipo `COBRO` haga lo mismo. Verificar con las pruebas existentes del pago rápido adaptadas al número `RCJ-…`, y que el pago queda listado en Pagos recibidos.
- [x] 3.2 Sacar `aplicar_anticipo` de `_DIFERIDAS`, habilitarla en `acciones_permitidas` cuando la factura es cobrable y el cliente tiene saldo a favor en su moneda, y exponer `POST /erp/ingresos/facturas/{id}/anticipos` y `POST /erp/ingresos/facturas/{id}/anticipos/{pago_id}/desaplicar`. Verificar con pruebas de la matriz con y sin saldo a favor, y que una factura con anticipo aplicado no se puede editar.
- [x] 3.3 Incluir en el detalle de la factura el `recibo_id` y número de cada pago, el `origen` y `retenciones_anticipo`. Verificar con una prueba de la respuesta del detalle.
- [x] 3.4 Agregar al arranque el agrupamiento idempotente de los pagos `COBRO` sin recibo en recibos `historico` (design §11). Verificar arrancando dos veces sobre una base con pagos antiguos: un recibo por pago, sin consumir consecutivo y conservando el número original.

## 4. API de Pagos recibidos

- [x] 4.1 Crear `api/v1/endpoints/erp_recibos.py`: `GET` del listado con filtros combinados y paginación (identificación, cliente, fecha desde/hasta, cuenta, estado, monto mín/máx, orden fecha y número desc), `GET /{id}`, `POST`, `POST /{id}/anular`, `POST /{id}/comentarios`, `GET /facturas-por-cobrar` y `GET /saldo-a-favor`, con los permisos de design §13, registrado en `api/v1/router.py` bajo `erp`. Verificar con pruebas HTTP de filtros combinados, resultado vacío (200 con lista vacía) y un 403 sin `fin_pagos` al crear.
- [x] 4.2 Agregar `pdf_recibo` en `core/erp_ventas_pdf.py` y `GET /{id}/pdf`. Verificar con una prueba que el texto del PDF contiene las notas y no los comentarios internos.

## 5. Frontend

- [x] 5.1 Agregar «Pagos recibidos» a `ERP_NAV_ITEMS` (sección `section.ingresos`), la ruta `/erp/ingresos/pagos-recibidos` en `App.tsx` y los textos en los archivos de idioma. Verificar que `npm run build` compila y que la opción aparece bajo Ingresos.
- [x] 5.2 Crear `ERPPagosRecibidos.tsx` con el listado, los chips de estado, los filtros en la URL, «Limpiar filtros», el estado vacío y el panel de detalle con PDF, anulación y comentarios. Verificar en el navegador contra la API que los filtros combinados y el estado vacío se comportan como dice el spec.
- [x] 5.3 Agregar el formulario «+ Nuevo ingreso» con los tres tipos: tabla de facturas por cobrar con «Monto a recibir», selector de cuentas de movimiento por línea, retenciones con lista desplegable de tipo y concepto, totales bruto/retenciones/neto, notas y comentarios. Verificar en el navegador un recibo de cada tipo y que los errores del servidor se muestran junto al campo.
- [x] 5.4 En `ERPFacturaVenta.tsx`: diálogo «Aplicar anticipo» con la lista de anticipos y la advertencia de retenciones, acción de desaplicar en los pagos de origen anticipo, número de recibo como enlace y retenciones del anticipo en el detalle. Verificar en el navegador una aplicación parcial y su desaplicación.

## 6. Integración

- [x] 6.1 Extender `backend/tests/humo_erp.py` con el recorrido por HTTP: anticipo → factura → aplicar anticipo → recibo que paga el resto → anular recibo → otro ingreso con retención. Comprobar al final el saldo de cartera, el de la 280505 y el `saldo_contable` de la cuenta. Verificar que el humo termina en verde.
- [x] 6.2 Arrancar con `MODULOS_SERVIDOR=erp` y verificar que monta `/erp/ingresos/pagos-recibidos` sin cargar WMS ni POS, y que `grep -nE "(^|[^a-z])(wms|pos|reencauche)_" app/core/erp_recibos.py app/api/v1/endpoints/erp_recibos.py` no devuelve nada (el patrón exige el prefijo como palabra: `anticipos_` no cuenta).
- [x] 6.3 Hacer que los reportes cuenten los comprobantes `ANULADO` junto con su reversión (design §15) con `erp_motor.ESTADOS_EN_LIBROS`. Verificar con `test_anular_deja_los_saldos_como_estaban` y con la comprobación de cartera del humo.

## Workflow follow-up

- Archivar `factura-de-venta-erp` antes que este cambio, porque este extiende sus capacidades de recaudo y consulta.
- Anotar en la nota de despliegue el cambio de formato de número de los pagos rápidos nuevos (`RCJ-…`).

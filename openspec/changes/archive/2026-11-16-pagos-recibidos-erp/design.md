# Design

## Context

Motivación y alcance en `proposal.md`; requisitos en `specs/erp/`. Este documento fija el cómo.

Lo que ya existe y condiciona el enfoque (verificado en el código el 16 de noviembre de 2026):

- `ERPPago` (`erp_pagos`) tiene una sola factura (`factura_cliente_id`), `cuenta_bancaria_id`, `metodo_pago`, `estado` (`PROCESADO`, `ANULADO`…) y `comprobante_id`.
- `erp_ventas.registrar_recaudo` bloquea la factura con `FOR UPDATE`, valida el monto, la cuenta y el medio de pago, numera `RC-{factura}-{n}`, asienta `RECAUDO_CLIENTE` (`banco` D y `cartera` C) y actualiza el saldo. No crea `ERPMovimientoBancario` ni toca `saldo_contable`.
- `erp_ventas.saldo_actual` calcula el saldo como total − retenciones − pagos `PROCESADO` − notas crédito + notas débito. La matriz `acciones_permitidas` trata cualquier pago como bloqueo de edición. `aplicar_anticipo` está en `_DIFERIDAS`.
- `erp_motor.siguiente_numero` numera con `erp_consecutivos` (`UPDATE … RETURNING`, por empresa, prefijo y año, prefijo de máximo 8 caracteres). El prefijo `RC` ya lo usan los comprobantes contables de tipo `INGRESO`, que también producen el POS y los movimientos bancarios.
- `erp_motor` ya define el papel `anticipo_aplicado` (D) en `VENTA_FACTURA`, pero ninguna regla lo usa. La semilla no tiene la 28, la 2805 ni la 280505. Sí tiene las retenciones soportadas 135515 (fuente), 135517 (IVA) y 135518 (ICA).
- `erp_gestion` ya contabiliza un movimiento bancario contra una cuenta de contrapartida elegida por el usuario y rechaza las agrupadoras (`acepta_movimientos`). Es el mismo patrón que necesitan los otros ingresos.
- Factura de venta guarda las retenciones en la factura (`retencion_fuente`, `retencion_ica`, `retencion_iva`) y ya las contabiliza en `VENTA_FACTURA`.
- El esquema se crea al arranque en `main.py` (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`). Alembic no se usa para esto.

## Goals / Non-Goals

**Goals:**
- Que el recibo de caja sea la única puerta de entrada de dinero de clientes al ERP: el pago rápido y `POST /erp/pagos` (cobro) pasan por el mismo servicio.
- Que la factura de venta no necesite saber de recibos: sigue leyendo sus `ERPPago` para saldo, historial y matriz.
- Cero imports de WMS, POS o reencauche en el código nuevo.

**Non-Goals:**
- Conciliar automáticamente los movimientos que crea el recibo contra extractos importados.
- Cambiar cómo el POS registra su recaudo.

## Decisions

### 1. El recibo como encabezado y `ERPPago` como aplicación
Tabla nueva `erp_recibos_caja` (encabezado) y `ERPPago` gana `recibo_id` y `origen` (`RECIBO` o `ANTICIPO`). Cada factura cruzada en un recibo es un `ERPPago` con `origen = RECIBO`, y cada aplicación de anticipo es un `ERPPago` con `origen = ANTICIPO`, que apunta al recibo del anticipo, sin cuenta bancaria y con su propio `comprobante_id`.
Así `saldo_actual`, el historial y la matriz de la factura siguen funcionando sin cambios de lógica, y la "sincronización bidireccional" del SRS sale del modelo.
*Alternativa descartada:* tabla de aplicaciones separada de `ERPPago`. Obligaría a sumar dos fuentes en `saldo_actual` y en la matriz, y a migrar los pagos existentes.

### 2. Esquema
- `erp_recibos_caja`: `empresa_id`, `numero` (único por empresa), `tipo_ingreso` (`PAGO_FACTURA`, `ANTICIPO`, `OTROS`), `tercero_id` (FK, nula solo para los recibos históricos y para los pagos hechos desde una factura sin tercero: consumidor final o POS), una copia de `cliente_nombre` y `cliente_nit`, `cuenta_bancaria_id`, `fecha`, `forma_pago`, `medio_pago`, `moneda`, `centro_costo_id`, `valor_bruto`, `total_retenciones`, `valor_neto`, `saldo_por_aplicar`, `estado`, `notas_adicionales`, `comprobante_id`, `movimiento_bancario_id`, `creado_por`, `anulado_motivo`, `anulado_por`, `anulado_en` e `historico` (bool).
- `erp_recibo_lineas_otros`: `recibo_id`, `cuenta_id`, `descripcion`, `valor`.
- `erp_recibo_retenciones`: `recibo_id`, `tipo` (`RETEFUENTE`, `RETEIVA`, `RETEICA`), `concepto`, `base`, `porcentaje`, `valor`, `editada`.
- `erp_recibo_comentarios`: `recibo_id`, `usuario`, `texto`, `creado`.
- `erp_pagos`: `recibo_id` (FK, nula) y `origen` (texto, `RECIBO` por omisión).
- `estado` y `tipo_ingreso` son texto, no enums nativos de PostgreSQL. Así se evita el `ALTER TYPE` por esquema que pidió `CASTIGADA` en el cambio anterior.

### 3. Servicio y router propios
`core/erp_recibos.py` (crear, anular, aplicar y desaplicar anticipo, saldo a favor) y `api/v1/endpoints/erp_recibos.py` con prefijo `/erp/ingresos/pagos-recibidos`, registrado en `api/v1/router.py` bajo el módulo `erp`. `erp_ventas.registrar_recaudo` se reduce a construir una entrada de recibo de una línea y llamar a `erp_recibos.crear`. La acción de aplicar anticipo se expone en el router de facturas (`POST /erp/ingresos/facturas/{id}/anticipos`), que delega en el servicio.
*Alternativa descartada:* ampliar `erp_ventas.py`. Ya concentra la factura y mezclaría dos documentos.

### 4. Consecutivo con talonario propio `RCJ`
El recibo se numera con `erp_consecutivos` y el prefijo `RCJ`, con el mismo `UPDATE … RETURNING`, dentro de la transacción. El formato es `RCJ-2026-000001`. Se generaliza `siguiente_numero` para aceptar un prefijo explícito, sin duplicar la lógica.
*Alternativa descartada:* usar como número del recibo el del comprobante contable `RC-…`. Ese talonario también lo consumen el POS, los movimientos bancarios y los reversos, así que los recibos quedarían con huecos.

### 5. Un solo comprobante por recibo
| Tipo | Evento | Débitos | Créditos |
|---|---|---|---|
| Pago a factura | `RECAUDO_CLIENTE` (existe) | `banco` por el neto | `cartera` por factura, con su tercero |
| Anticipo | `ANTICIPO_CLIENTE` (nuevo) | `banco` por el neto; `retefuente`, `reteiva` y `reteica` | `anticipo` por el bruto |
| Otros ingresos | `OTRO_INGRESO` (nuevo) | `banco` por el neto; retenciones | `contrapartida` con `cuenta_id` explícita por línea |
| Aplicación | `APLICACION_ANTICIPO` (nuevo) | `anticipo` | `cartera` |

`banco` toma la cuenta contable de la cuenta bancaria si la tiene, como hoy. La semilla agrega 28 «Otros pasivos», 2805 «Anticipos y avances recibidos» y 280505 «De clientes», y las reglas de los tres eventos (retenciones a 135515, 135517 y 135518). Es idempotente y no pisa lo que la empresa haya editado.
El papel `anticipo_aplicado` de `VENTA_FACTURA` se deja sin uso. Aplicar siempre después de emitir da una sola mecánica para los dos caminos.

### 6. Movimiento bancario y saldo
En la misma transacción se crea un `ERPMovimientoBancario` `CREDITO` por el neto, con `comprobante_id` y `conciliado = False`, y se suma a `saldo_contable` (con la cuenta bloqueada con `FOR UPDATE`). La anulación crea el `DEBITO` contrario y resta. Ninguno de los dos se borra.

### 7. Concurrencia y orden de bloqueos
Crear: se bloquean las facturas en orden ascendente de id, luego la cuenta bancaria y por último se toma el consecutivo. Aplicar anticipo: primero el recibo del anticipo y luego la factura. Un orden fijo evita bloqueos mutuos entre dos recibos que cruzan las mismas facturas. Los saldos se releen con `populate_existing` después del bloqueo, como ya hace `registrar_recaudo`.

### 8. Retenciones
- En *Anticipo* y *Otros ingresos*, el porcentaje se sugiere con `erp_impuestos.calcular(impuesto, concepto, base, fecha, tercero)` y es editable (queda `editada = True`). `valor_neto = valor_bruto − total_retenciones` y debe ser mayor que cero.
- En *Pago a factura* no se aceptan retenciones en la entrada. El detalle muestra las de las facturas.
- Al aplicar un anticipo con retenciones, la respuesta de la factura incluye `retenciones_anticipo`, calculadas al vuelo como `retención × aplicado / bruto` de cada aplicación vigente. No se escriben en `retencion_*` de la factura, porque eso las volvería a contabilizar y bajaría el saldo dos veces. La advertencia de doble registro la da el endpoint de aplicación cuando la factura tiene `retencion_*` > 0 y el anticipo tiene retenciones; la confirmación se envía como `confirmar_retenciones = true`.

### 9. Estado del recibo
Se calcula al escribir:
- `ANULADO` si se anuló.
- `POR_APLICAR` si es anticipo con `saldo_por_aplicar` > 0.
- `APLICADO` en los demás casos.

`saldo_por_aplicar` = bruto − aplicaciones `PROCESADO`, y se recalcula al aplicar y al desaplicar con el recibo bloqueado. El saldo a favor del cliente se consulta como suma de `saldo_por_aplicar` de sus recibos `POR_APLICAR`, agrupada por moneda.

### 10. Anulación
`erp_motor.reversar` sobre el comprobante, `ERPPago` del recibo a `ANULADO`, saldo y estado de cada factura recalculados con `saldo_actual`, y movimiento bancario contrario.
- Se rechaza si alguna factura está `CASTIGADA` o `ANULADA`, o si es un anticipo con aplicaciones vigentes.
- Desaplicar reversa el comprobante de la aplicación y pasa el `ERPPago` a `ANULADO`.
- Ambas acciones exigen motivo y quedan en el historial de la factura.

### 11. Recibos históricos
Al arrancar, de forma idempotente, cada `ERPPago` `COBRO` sin `recibo_id` recibe un recibo `historico = True`, de tipo `PAGO_FACTURA`, con el `numero`, la fecha, la cuenta, el monto y el `comprobante_id` del pago, y un estado según el del pago. No consume consecutivo ni crea movimiento bancario. El `tercero_id` sale de la factura o, si no lo tiene, de un tercero con el mismo NIT; si no hay ninguno, queda nulo.

### 12. Facturas por cobrar del cliente
`GET /erp/ingresos/pagos-recibidos/facturas-por-cobrar?tercero_id=&moneda=` devuelve las facturas cobrables con saldo > 0 donde `tercero_id` coincide o, si es nulo, `cliente_nit` coincide con la identificación normalizada del tercero (sin puntos, guiones ni dígito de verificación). Al aplicarles un pago se completa su `tercero_id`.

### 13. Permisos
- Ver, listar y descargar PDF: `ver_cartera`.
- Crear, comentar, aplicar y desaplicar anticipo: `registrar_pagos` (`fin_pagos`).
- Anular: `anular` (`fin_anular`).

No hay permisos nuevos.

### 14. Frontend
Página `ERPPagosRecibidos.tsx` en `/erp/ingresos/pagos-recibidos`: tabla con filtros en la URL, detalle en panel lateral y formulario en diálogo de pantalla completa. Usa los mismos selectores que la factura de venta (cliente por identificación, centros de costo, catálogos `ERP/FORMA_PAGO`, `ERP/MEDIO_PAGO` y `GLOBAL/MONEDA`) y el selector de cuentas de movimiento que ya usa Tesorería. En `ERPFacturaVenta.tsx`, el diálogo «Aplicar anticipo», la acción de desaplicar en los pagos y el número de recibo como enlace.

### 15. Los anulados siguen en los libros
`erp_motor.reversar` deja el original `ANULADO` y asienta su contrario `CONTABILIZADO`. Los reportes contaban solo `CONTABILIZADO`, así que tomaban la reversión sin el original y cada anulación (recibos, facturas, notas) quedaba contada dos veces. Lo destapó la verificación de cartera del humo. Todos los saldos y libros (balance, mayor, diario, estados financieros, gestión, demo) filtran ahora por `erp_motor.ESTADOS_EN_LIBROS = (CONTABILIZADO, ANULADO)`: el original y su reversión se cancelan y el diario muestra las dos entradas. Solo el `BORRADOR` queda por fuera. Es seguro porque `reversar` es lo único que pone un comprobante en `ANULADO`.

En el mismo frente: `reversar` fechaba el contrario con la fecha UTC, que desde las 7 p. m. de Bogotá ya es el día siguiente (y el último día del mes, el período siguiente). Ahora usa `erp_motor.hoy_colombia()`.

## Risks / Trade-offs

- **Extracto importado duplica el movimiento del recibo** → el movimiento queda `conciliado = False` y con comprobante. La conciliación debe emparejarlo en vez de contabilizar el del extracto. Se documenta; la conciliación automática queda fuera de alcance.
- **Recibos históricos sin tercero** → se muestran con el nombre de la factura. No permiten aplicar anticipos (no son anticipos) y su anulación funciona igual.
- **Doble registro de retenciones si la factura ya las tenía** → advertencia explícita y confirmación. No se bloquea, porque hay casos legítimos (retenciones de conceptos distintos).
- **`saldo_contable` desalineado si ya había diferencias** → el recibo suma y resta de forma incremental y no recalcula desde los movimientos. Corregirlo no es parte de este cambio.
- **Cambio de número para los pagos rápidos nuevos** (`RC-FV-…` pasa a `RCJ-…`) → los antiguos conservan el suyo. Se anota en la nota de despliegue.

## Migration Plan

1. Desplegar las tablas y columnas nuevas al arranque (idempotente) y sembrar las cuentas y reglas por empresa.
2. Agrupar los pagos históricos en recibos (decisión 11), en el mismo arranque, por lotes y de forma idempotente.
3. Publicar la entrada del menú y la ruta junto con el cambio del pago rápido.
4. **Reversa:** quitar la ruta y el router devuelve la factura a su comportamiento anterior. Las tablas y columnas sobrantes no estorban, porque `ERPPago` sigue siendo la fuente del saldo.
5. Probar con `MODULOS_SERVIDOR=erp`: monta las rutas nuevas sin cargar WMS ni POS.

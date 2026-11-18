# Design

## Context

Motivación y alcance en `proposal.md`; requisitos en `specs/erp/`. Este documento solo fija el cómo.

Lo que ya existe y condiciona el enfoque (verificado en el código el 15 de noviembre de 2026):

- `POST /erp/cxc/facturas` (`api/v1/endpoints/erp.py`) crea la factura, sus líneas y el asiento `VENTA_FACTURA` en una transacción, pero recibe el `numero` del cliente y no usa resoluciones. `PATCH /cxc/facturas/{id}` genera un CUFE de relleno.
- `POST /erp/pagos` actualiza saldo y estado con `max(0, saldo - monto)`, sin validar sobrepago ni factura anulada.
- Los flujos del POS, el WMS 3PL y reencauche ya emiten facturas con resolución vigente (`facturacion_dian.tomar_numero`, atómico) y CUFE real (`codigo_unico`). El ERP manual es el único que no.
- El motor contable (`erp_motor`) decide cuentas por papel y evento, y `reversar` ya existe. El comprobante solo guarda el número del documento como `referencia` (texto); el tipo y el id quedan en la auditoría y en los eventos contables. Por eso la factura guarda el `comprobante_id` de su asiento (corregido durante la implementación: el diseño original suponía que el comprobante tenía `documento_tipo/documento_id`).
- `ERPTercero` tiene marcas de papel (`es_cliente`, `es_proveedor`, `es_empleado`, `es_socio`). Las facturas guardan cliente como texto (nombre, NIT, correo), sin vínculo al tercero.
- El esquema nuevo se agrega con `ALTER` al arranque en `main.py`; Alembic solo llega a la 008. El ERP importa sus modelos siempre, pero sus rutas solo se montan si el módulo `erp` está cargado en el servidor.
- `erp.py` ya es muy grande. El caché de respuestas (`cache_respuestas.py`) invalida por empresa en toda escritura exitosa, así que los endpoints nuevos no necesitan nada propio.

## Goals / Non-Goals

**Goals:**
- Un único servicio de emisión de factura de venta del ERP, con la misma mecánica de numeración y CUFE que el POS y el WMS.
- Todas las reglas de estado en un solo lugar del servidor; el frontend solo las muestra.
- Cambios aditivos al esquema, sin tocar facturas existentes.
- El módulo `erp` sigue funcionando solo: sin imports de `wms_*`, `pos_*` ni `reencauche_*` en el código nuevo.

**Non-Goals:**
- Inventario propio, listas de precios, anticipos de cliente, transmisión real a la DIAN, recurrentes y recordatorios.
- Reescribir los flujos de POS, WMS y reencauche para que usen el servicio nuevo.
- Unificar las cinco listas de centros de costo.

## Decisions

### 1. Servicio y router nuevos, no más código en `erp.py`
Un módulo `core/erp_ventas.py` (reglas, matriz de acciones, emisión, recaudo, ajustes) y un router `erp_ventas.py` con prefijo `/erp/ingresos/facturas`, montado en `RUTAS` bajo el módulo `erp`. Los endpoints viejos de `/cxc/facturas` (listado, resumen, aging) se quedan; el `POST` viejo delega en el servicio nuevo con tipo factura de venta e ignora el `numero` que reciba.
*Alternativa descartada:* seguir ampliando `erp.py`. Mezcla lo nuevo con 1500 líneas ajenas y complica probarlo.

### 2. Numeración desde la resolución, sin pasar por el WMS
Se añade a `facturacion_dian.py` una función que busca la resolución vigente de la empresa por tipo de documento (`FACTURA_VENTA` o `POS`) y se usa `tomar_numero` para consumir el número dentro de la misma transacción de la factura, de modo que un fallo no deja huecos ni repite números. El CUFE sale de `codigo_unico`.
*Alternativa descartada:* reutilizar `wms_contable.resolucion_vigente`. Obligaría al ERP a importar código del WMS y rompería la independencia. El WMS puede delegar en la función nueva más adelante.

### 3. Una sola matriz de acciones, calculada en el servidor
`acciones_permitidas(factura, hay_pagos)` devuelve un conjunto de acciones a partir de tres datos: estado de cobro, existencia de pagos aplicados y estado DIAN. El detalle la incluye en su respuesta y cada endpoint de acción la consulta antes de ejecutar. El vencimiento no se guarda: se deriva (`saldo > 0` y `fecha_vencimiento < hoy`). El valor `VENCIDA` del enum sigue sin usarse.
*Alternativa descartada:* decidir en el frontend. Cualquiera puede llamar al endpoint directo.

### 4. Cambios de esquema (todos opcionales)
- `erp_facturas_cliente`: `tipo_documento` (por omisión `FACTURA_VENTA`), `tercero_id` y `vendedor_id` (FK a `erp_terceros`), `forma_pago`, `medio_pago`, `remision`, `notas_adicionales`, `firma` (ruta del archivo), `retencion_fuente`, `retencion_ica`, `retencion_iva` y `retenciones_editadas`, los datos del castigo (`cerrada_motivo`, `cerrada_por`, `cerrada_en`) y `comprobante_id`, el asiento de venta vigente.
- `erp_lineas_factura_cliente`: `referencia` (texto) e `item_id` (entero sin clave foránea; lo usará el inventario futuro).
- `erp_terceros`: `es_vendedor`.
- Tablas nuevas: nota débito (espejo simplificado de la de crédito), historial y comentarios de la factura, y adjuntos de la factura con los archivos en `UPLOAD_DIR`.
- `saldo` pasa a significar `total − retenciones − pagos − notas`; se recalcula en un solo lugar.
- Estado nuevo `CASTIGADA` en `EstadoFactura` para «Cerrar sin pago».

### 5. Asientos por reverso y reemisión
Editar o devolver a borrador reversa el comprobante de `factura.comprobante_id` con `erp_motor.reversar` y, al volver a emitir, genera uno nuevo y actualiza esa columna. Anular reversa y marca `ANULADA`. Con un período cerrado el motor ya rechaza la operación, y eso se propaga como rechazo del cambio completo. `VENTA_FACTURA` registra la cartera por `total − retenciones` y las retenciones en sus papeles del lado débito.
*Alternativa descartada:* editar el asiento en sitio. Pierde la huella de auditoría.

### 6. Eventos nuevos en el motor
`VENTA_NOTA_DEBITO` (cartera al débito, ingreso e IVA generado al crédito, espejo de la nota crédito) y `CASTIGO_CARTERA` (gasto por castigo al débito, cartera al crédito), ambos en `NATURALEZA_POR_EVENTO` y con reglas sembradas en `erp_semilla`, que es idempotente y no pisa lo que la empresa haya cambiado.

### 7. Recaudo con una sola ruta de validación
`registrar_recaudo(db, factura, ...)` bloquea la fila de la factura (`SELECT ... FOR UPDATE`), rechaza valor ≤ 0, sobrepago y estados no permitidos, y luego asienta. Lo usan `POST /erp/pagos` (que corrige así su hueco actual), el pago rápido y el pago durante la creación. Esta última llama al servicio de emisión y al de recaudo dentro de una misma transacción.
*Alternativa descartada:* validar solo en el endpoint nuevo. Dejaría `/pagos` con el defecto actual.

### 8. Permisos con claves existentes y una nueva
Crear, emitir y notas: `fin_comprobantes`. Pagos: `fin_pagos`. Anular y volver a borrador: `fin_anular`. Retenciones: `fin_impuestos`. Consultar: `fin_cartera` o `fin_contabilidad`. Castigar: `fin_castigar`, nueva, que se agrega a `permisos_perfil.py`, a la guardia de `erp_permisos.py` y a la matriz de permisos de Roles del frontend.

### 9. Selectores del formulario
Moneda y forma de pago: catálogo maestro (`GLOBAL/MONEDA`, `ERP/FORMA_PAGO`). Medio de pago: entrada nueva `ERP/MEDIO_PAGO` en `CATALOGOS_REGISTRO`. Centro de costo: `erp_centros_costo` filtrado por la empresa o sin empresa, como hace el costeo. Vendedor: terceros activos con `es_vendedor`. Cliente: búsqueda por identificación sobre terceros.

### 10. IA como extractor sin efectos
`POST /erp/ingresos/facturas/ia/extraer` recibe el archivo, lo valida (tipo y tamaño), llama al modelo de visión y devuelve un borrador en el mismo formato que el formulario, sin escribir nada. El proveedor se elige con `ERP_IA_PROVEEDOR`: `anthropic` (por omisión; clave `TITTANBOT_API_KEY`, modelo `ERP_IA_MODELO`, salida con `output_config.format` y respaldo de servidor) u `openai` (clave `OPENAI_API_KEY`, modelo `ERP_IA_MODELO_OPENAI` o el de extracción de las reuniones del CRM, Responses API con `text_format` y `store=False`, el mismo patrón de `reuniones/ia.py`). OpenAI se agregó durante la implementación porque el entorno no tiene clave de Anthropic. Ambos devuelven el mismo esquema y pasan por la misma validación. Exige el permiso `asistente`. Cruza el NIT con los terceros existentes pero no los crea.
*Alternativa descartada:* que el agente guarde la factura. El SRS exige revisión humana y el guardado debe pasar por las validaciones normales.

### 11. Frontend
Página nueva `ERPFacturaVenta.tsx` (listado, detalle en panel y formulario) en la ruta `/erp/ingresos/factura-venta`, con la sección «Ingresos» en `ERP_NAV_ITEMS` y `ERP_SECTIONS` y sus textos en los archivos de idioma. `ERPCxC` y `ERPFacturacion` pierden el diálogo de creación y su botón enlaza a la pantalla nueva.

## Risks / Trade-offs

- **Reverso con períodos cerrados** → el motor ya rechaza; el servicio lo propaga con un mensaje que indica el período, y la matriz no deshabilita el botón por esto (depende de la fecha), así que el rechazo del servidor es la defensa.
- **Dos pagos simultáneos sobre la misma factura** → bloqueo de fila y recálculo del saldo dentro de la transacción.
- **Enum nativo de PostgreSQL para `CASTIGADA`** → verificado el 15 de noviembre de 2026 con `\dT+ estadofactura` en PostgreSQL 16.15: `estado` es un enum nativo y existe **un tipo por esquema** (`public` y `cli_desarrollo`), con los valores BORRADOR, EMITIDA, PARCIALMENTE_PAGADA, PAGADA, VENCIDA y ANULADA. Una base nueva lo crea completo con `create_all`, pero las existentes necesitan `ALTER TYPE estadofactura ADD VALUE IF NOT EXISTS 'CASTIGADA'` en cada esquema, dentro de `_migrar_esquema` y con el valor sin usarse en la misma transacción. El proyecto no tiene este patrón todavía (otros modelos usan columnas de texto para no depender de `ALTER TYPE`); aquí se mantiene el enum porque el resto del código ya compara contra `EstadoFactura`.
- **Facturas que otros módulos emiten con sus propios flujos** → comparten resolución y consecutivo, así que no hay choques de número, pero no pasan por la matriz nueva hasta que se migren. La matriz las cubre al consultarlas, porque solo lee estado, pagos y DIAN.
- **Retención editada a mano** → se marca como modificada y queda en el historial para auditar.
- **IA con costo para la plataforma** → permiso por perfil, límite de tamaño y mensaje claro si no está configurada.
- **Datos antiguos** → las facturas anteriores quedan con `tipo_documento = FACTURA_VENTA` y sin vendedor, y no se recalculan.

## Migration Plan

1. Desplegar el código con las columnas, tablas y el valor de enum nuevos creados al arranque de forma idempotente (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`).
2. La siembra agrega las cuentas y reglas nuevas a cada empresa sin tocar las existentes.
3. Publicar la sección «Ingresos» y retirar la creación de `ERPCxC` y `ERPFacturacion` en el mismo despliegue.
4. **Reversa:** todo es aditivo y opcional. Quitar la ruta del menú y del `RUTAS` deja el sistema como estaba; las columnas sobrantes no estorban.
5. Probar también un servidor con `MODULOS_SERVIDOR=erp`: debe montar las rutas nuevas y no cargar WMS.

## Open Questions

- ~~Cuenta de gasto del castigo.~~ **Resuelto (2026-11-15):** gasto 519910 (D) contra cartera 130505 (C) y, en el mismo comprobante, control en cuentas de orden 832510 «Activos castigados – Deudores» (D) contra 860510 «Activos castigados – Deudores (CR)» (C). Las cuentas de orden no entran al balance ni a resultados. Sembrado y editable desde las reglas contables.
- ~~Tamaño máximo del archivo para la IA.~~ **Resuelto (2026-11-15):** 10 MB, confirmado por el usuario. Es el valor por omisión de `ERP_IA_MAX_MB`.
- ~~Cómo se consume un saldo a favor que deja una nota crédito sobre una factura cobrada.~~ **Resuelto (2026-11-16):** como un anticipo. La nota crédito sobre una factura ya cobrada deja el saldo a favor en 280505 «Anticipos de clientes» y se aplica a otra factura con «Aplicar anticipo» (el mecanismo de `pagos-recibidos-erp`); una devolución al cliente es un egreso contra esa misma cuenta. Esta entrega solo lo deja registrado: la implementación va en un cambio aparte.

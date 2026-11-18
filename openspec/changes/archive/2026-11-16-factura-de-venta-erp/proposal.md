# Proposal

## Why

Hoy el ERP tiene dos formularios para crear una factura de venta a mano (`/erp/cxc` y `/erp/facturacion`), y ninguno cubre lo que un usuario de Finanzas necesita: el número lo digita la persona en vez de salir de la resolución, no hay vendedor ni forma de pago, la factura no distingue factura de ticket, no se puede registrar un pago al crearla y el detalle casi no ofrece acciones (notas, retenciones, anulación, castigo). Además el pago rápido tiene huecos que ya afectan la contabilidad: un pago mayor que el saldo no se rechaza, pero el asiento sí registra el monto completo.

El documento de requerimientos «Módulo ERP: Ingresos — Factura de Venta» (v1.0, 15 de noviembre de 2026) pide un flujo completo de ingresos. Este cambio lo lleva al código sin volver al ERP dependiente del WMS ni del POS.

## What Changes

- Nuevo menú **Ingresos** en el ERP con la pantalla **Factura de venta**, que pasa a ser la única puerta para crear facturas a mano. `ERPCxC` conserva cartera y aging; `ERPFacturacion` conserva la parte de facturación electrónica.
- El consecutivo sale de la resolución vigente de la empresa (`tomar_numero`), por tipo de documento: factura de venta, ticket (resolución `POS`), nota crédito y nota débito. El cliente deja de enviar el `numero`. **BREAKING** para quien llame `POST /erp/cxc/facturas` mandando su propio número.
- El formulario completa los campos que faltan: tipo de documento, moneda, centro de costo (de `erp_centros_costo`), vendedor, forma y medio de pago, líneas con referencia en texto, notas, firma, comentarios y pago al crear.
- El tercero gana la marca `es_vendedor` y la factura un `vendedor_id`.
- El detalle ofrece las acciones del SRS, gobernadas por una sola matriz de estados en el backend: la regla es que editar, volver a borrador y anular solo se permiten sin pagos y sin transmitir a la DIAN; después se corrige con nota crédito o débito.
- Pago rápido desde el listado, con validaciones nuevas: no se acepta un pago mayor que el saldo ni sobre una factura anulada.
- Retenciones por factura, editables a mano y prellenadas con `erp_impuestos.calcular`. Ajustan saldo y asiento.
- «Cerrar sin pago» es un castigo de cartera con motivo, asiento propio (evento nuevo) y permiso `fin_castigar`.
- Factura asistida por IA a partir de un PDF o imagen, con campos editables antes de guardar.
- «Aplicar anticipo» aparece deshabilitado: queda diferido hasta que exista la sección de tesorería.

Fuera de alcance: inventario propio del ERP (ítems, bodegas, lista de precios, descuento de stock), anticipos de cliente, transmisión real a la DIAN, facturas recurrentes y recordatorios, la sincronización de las cinco listas de centros de costo y la corrección del CUFE de relleno del `PATCH` de la factura.

## Capabilities

### New Capabilities
- `erp/factura-venta-emision`: creación de factura de venta o ticket, numeración por resolución, selectores, líneas, totales y validaciones al guardar.
- `erp/factura-venta-consulta`: acceso por el menú Ingresos, listado, detalle y matriz de acciones permitidas según el estado.
- `erp/factura-venta-recaudo`: registro de pagos (rápido y al crear), abonos, validaciones y trazabilidad a cartera y bancos.
- `erp/factura-venta-ajustes`: edición, vuelta a borrador, anulación, notas crédito y débito, retenciones y castigo de cartera.
- `erp/factura-venta-ia`: completar el formulario de factura desde un archivo con el agente de IA.

### Modified Capabilities
<!-- Ninguna: el proyecto no tiene specs principales todavía (`openspec list --specs` está vacío). -->

## Impact

- **Backend:**
  - `api/v1/endpoints/erp.py`: endpoints `/cxc/facturas` y `/pagos`, matriz de estados y validaciones.
  - `api/v1/endpoints/erp_facturacion.py`: notas crédito y débito.
  - `core/erp_motor.py`: evento `CASTIGO_CARTERA` en `NATURALEZA_POR_EVENTO`.
  - `core/erp_semilla.py`: cuenta de gasto y regla del castigo.
  - `core/erp_permisos.py` y `core/permisos_perfil.py`: permiso `fin_castigar`.
  - `core/facturacion_dian.py`: reutilizado para numeración y CUFE.
  - `infrastructure/models`: nuevos campos en `ERPFacturaCliente`, `ERPLineaFacturaCliente` y `ERPTercero`; modelo de nota débito; `MEDIO_PAGO` en `catalogo.py`.
  - `main.py`: `ALTER` al arranque para las columnas nuevas, ya que Alembic solo llega a la migración 008.
- **Frontend:** `Sidebar.tsx` (sección Ingresos), `App.tsx` (ruta), página nueva de Factura de venta, `ERPTerceros.tsx` (marca de vendedor), `ERPCxC.tsx` y `ERPFacturacion.tsx` (se quita la creación manual) y la matriz de permisos de Roles.
- **Datos:** columnas nuevas, todas opcionales; las facturas existentes no se modifican y quedan sin vendedor.
- **Sin cambios para** WMS, POS y reencauche: siguen emitiendo con sus propios flujos sobre la misma resolución.
- **Dependencias de módulos:** ninguna nueva. El ERP sigue funcionando sin WMS ni POS.

# pagos-recibidos-consulta Specification

## Purpose

Da acceso al submódulo Pagos recibidos desde el menú Ingresos para consultar, filtrar e imprimir los recibos de caja con los que la empresa registra el dinero que recibe.

## Requirements

### Requirement: Acceso desde el menú Ingresos
El menú del ERP SHALL mostrar «Pagos recibidos» dentro de la sección Ingresos a quien tenga permiso de consultar cartera. La pantalla MUST ofrecer el listado de recibos y, a quien tenga permiso de registrar pagos, el botón «+ Nuevo ingreso».

#### Scenario: Usuario con permiso de cartera
- **WHEN** un usuario con permiso de cartera abre el menú Ingresos
- **THEN** ve la opción «Pagos recibidos» y, al elegirla, el listado de recibos

#### Scenario: Sin permiso de registrar pagos
- **WHEN** un usuario puede consultar cartera pero no registrar pagos
- **THEN** ve el listado sin el botón «+ Nuevo ingreso»

### Requirement: Columnas del listado
Cada fila del listado SHALL mostrar el número del recibo, el cliente, un detalle breve (facturas cruzadas, «Anticipo» o la cuenta del otro ingreso), la fecha del pago, la cuenta bancaria, el estado y el monto recibido.

#### Scenario: Recibo que cruza dos facturas
- **WHEN** un recibo aplicó dinero a las facturas FV-120 y FV-131
- **THEN** su fila muestra en el detalle ambos números y en el monto la suma recibida

#### Scenario: Estado visible
- **WHEN** el listado incluye recibos aplicados, pendientes por aplicar y anulados
- **THEN** cada estado se distingue con un indicador visual propio

### Requirement: Orden por omisión
El listado SHALL mostrar los recibos del más reciente al más antiguo por fecha de pago y, a igual fecha, por número descendente.

#### Scenario: Orden cronológico
- **WHEN** existen recibos del 10 y del 15 de noviembre
- **THEN** el del 15 aparece primero

### Requirement: Filtros combinables
El listado SHALL filtrar por número de identificación del cliente, nombre del cliente, rango de fechas, cuenta bancaria, estado y rango de monto. Los filtros MUST combinarse con Y lógico y aplicarse en el servidor.

#### Scenario: Dos filtros a la vez
- **WHEN** se filtra por la cuenta «Bancolombia» y el estado «Pendiente por aplicar»
- **THEN** solo aparecen los recibos que cumplen ambas condiciones

#### Scenario: Rango de montos
- **WHEN** se indica un monto mínimo de 100.000 y un máximo de 500.000
- **THEN** solo aparecen los recibos con monto dentro de ese rango, incluidos los extremos

### Requirement: Limpiar filtros y resultado vacío
La pantalla SHALL ofrecer «Limpiar filtros» para volver al listado completo y MUST mostrar un mensaje informativo, sin error, cuando ningún recibo cumpla los filtros.

#### Scenario: Sin coincidencias
- **WHEN** los filtros no coinciden con ningún recibo
- **THEN** la tabla muestra un mensaje de que no hay pagos recibidos con esos criterios

#### Scenario: Limpiar
- **WHEN** el usuario pulsa «Limpiar filtros»
- **THEN** los filtros quedan vacíos y el listado vuelve a mostrar todos los recibos

### Requirement: Detalle del recibo
Al seleccionar un recibo, el sistema SHALL mostrar sus datos generales, las facturas cruzadas con el valor aplicado a cada una, las líneas de otros ingresos, las retenciones, las aplicaciones del anticipo y su saldo, las notas, los comentarios internos y el comprobante contable.

#### Scenario: Anticipo aplicado en parte
- **WHEN** se abre un anticipo de 1.000.000 del que se aplicaron 600.000 a la factura FV-140
- **THEN** el detalle muestra la aplicación a FV-140 por 600.000 y un saldo por aplicar de 400.000

### Requirement: PDF del recibo de caja
El sistema SHALL generar el PDF del recibo con los datos de la empresa y el cliente, el número, la fecha, la cuenta, las facturas o líneas, las retenciones, el valor recibido y las notas. Los comentarios internos MUST NOT aparecer en el PDF.

#### Scenario: Notas sí, comentarios no
- **WHEN** un recibo tiene la nota «Pago por transferencia ref. 8891» y el comentario interno «Llamar al cliente el lunes»
- **THEN** el PDF incluye la nota y no incluye el comentario

### Requirement: Pagos anteriores en el listado
Los cobros registrados antes de este submódulo SHALL aparecer en el listado como recibos de pago a factura, con su número original.

#### Scenario: Pago antiguo
- **WHEN** existía un pago `RC-FV-100-1` registrado con el pago rápido anterior
- **THEN** aparece en el listado con ese número, su factura, su cuenta y su monto

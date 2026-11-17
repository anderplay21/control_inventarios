# factura-venta-recaudo Specification

## Purpose

Permite registrar el cobro total o parcial de una factura de venta, desde el listado o al crearla, y mantener la cartera, los bancos y el estado de la factura consistentes con cada pago.

## Requirements

### Requirement: Formulario de pago rápido
Al pulsar «Agregar pago» sobre una factura por cobrar, el sistema SHALL abrir un formulario con el valor precargado con el saldo pendiente, la cuenta bancaria de destino, la fecha del pago y el método de pago. El valor y la fecha MUST ser editables.

#### Scenario: Valor precargado
- **WHEN** se abre el formulario sobre una factura con saldo de 500.000
- **THEN** el valor aparece con 500.000 y puede cambiarse para registrar un abono

#### Scenario: Fecha por omisión
- **WHEN** se abre el formulario
- **THEN** la fecha trae el día actual y puede modificarse

#### Scenario: Cuenta bancaria obligatoria
- **WHEN** se intenta confirmar un pago sin elegir una cuenta bancaria
- **THEN** el sistema no lo registra y pide elegirla

### Requirement: Efecto del pago en la factura
Al confirmar un pago, el sistema SHALL reducir el saldo de la factura por el valor pagado. Si el pago cubre todo el saldo, el estado MUST pasar a «Cobrada»; si lo cubre en parte, MUST quedar como parcialmente pagada.

#### Scenario: Pago total
- **WHEN** se registra un pago igual al saldo
- **THEN** el saldo queda en cero y la factura pasa a «Cobrada»

#### Scenario: Abono parcial
- **WHEN** se registra un pago menor que el saldo
- **THEN** el saldo baja por ese valor y la factura sigue «Por cobrar» como parcialmente pagada

### Requirement: Pagos inválidos
El sistema MUST rechazar un pago cuyo valor sea menor o igual a cero, mayor que el saldo pendiente o aplicado a una factura anulada o en borrador. Un pago rechazado no MUST dejar asiento ni movimiento bancario.

#### Scenario: Pago mayor que el saldo
- **WHEN** se intenta registrar un pago de 600.000 sobre una factura con saldo de 500.000
- **THEN** el sistema lo rechaza con un mensaje que indica el saldo máximo y no cambia nada

#### Scenario: Factura anulada
- **WHEN** se intenta registrar un pago sobre una factura anulada
- **THEN** el sistema lo rechaza y la factura no cambia

#### Scenario: Valor cero o negativo
- **WHEN** se envía un pago de cero o negativo
- **THEN** el sistema lo rechaza

### Requirement: Trazabilidad contable del pago
Cada pago registrado SHALL generar su asiento de recaudo contra la cartera y la cuenta bancaria elegida, y MUST quedar vinculado a la factura en su detalle y reflejado en los saldos de cartera y bancos.

#### Scenario: Asiento y vínculo
- **WHEN** se confirma un pago de 200.000 a la cuenta bancaria elegida
- **THEN** existe un comprobante de recaudo por 200.000, el pago aparece en el detalle de la factura y el saldo de cartera baja en 200.000

#### Scenario: Período cerrado
- **WHEN** la fecha del pago cae en un período contable cerrado
- **THEN** el sistema rechaza el pago y no modifica el saldo ni los libros

### Requirement: Pago durante la creación
El formulario de nueva factura SHALL permitir registrar un pago al guardar. La factura y el recaudo MUST guardarse en una sola operación: si cualquiera falla, no queda ninguno.

#### Scenario: Factura de contado pagada
- **WHEN** se guarda una factura de contado con un pago por el total y una cuenta bancaria
- **THEN** queda emitida y cobrada, con el pago registrado y ambos asientos contabilizados

#### Scenario: Falla el recaudo
- **WHEN** el pago incluido es inválido
- **THEN** la factura tampoco se guarda y el sistema muestra el motivo

### Requirement: Medio de pago
El método de pago SHALL elegirse de una lista de medios de pago configurable por la empresa, que incluya al menos efectivo, transferencia y tarjeta.

#### Scenario: Lista configurable
- **WHEN** la empresa agrega un medio de pago a la lista
- **THEN** aparece como opción en el formulario de pago y en el de nueva factura

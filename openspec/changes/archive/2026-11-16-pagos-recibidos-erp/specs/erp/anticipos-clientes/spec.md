# Spec Delta

## Purpose

Lleva el saldo a favor que dejan los anticipos de cada cliente y permite aplicarlo a sus facturas de venta, convirtiendo el pasivo en cobro de cartera sin registrar dos veces el ingreso ni las retenciones.

## ADDED Requirements

### Requirement: Saldo a favor del cliente
El sistema SHALL calcular el saldo a favor de cada cliente como la suma de los saldos por aplicar de sus anticipos vigentes, por moneda.

#### Scenario: Dos anticipos
- **WHEN** un cliente tiene un anticipo con 400.000 por aplicar y otro con 1.000.000 por aplicar, ambos en COP
- **THEN** su saldo a favor en COP es 1.400.000

### Requirement: Aplicar anticipo desde la factura
La acción «Aplicar anticipo» de la factura de venta SHALL estar habilitada cuando la factura es cobrable y su cliente tiene saldo a favor en la misma moneda. Al usarla, el usuario MUST elegir el anticipo y el valor a aplicar.

#### Scenario: Acción habilitada
- **WHEN** se abre una factura emitida con saldo de cliente que tiene anticipos por aplicar en la moneda de la factura
- **THEN** «Aplicar anticipo» está habilitada y lista los anticipos con su saldo

#### Scenario: Sin saldo a favor
- **WHEN** el cliente de la factura no tiene anticipos por aplicar
- **THEN** la acción aparece deshabilitada con el motivo

### Requirement: Límites de la aplicación
El valor aplicado SHALL ser mayor que cero y no superar ni el saldo de la factura ni el saldo por aplicar del anticipo elegido.

#### Scenario: Supera el saldo de la factura
- **WHEN** se intenta aplicar 700.000 a una factura con saldo de 600.000
- **THEN** el sistema lo rechaza indicando el máximo aplicable

### Requirement: Efecto contable y de saldos de la aplicación
Aplicar un anticipo SHALL generar un comprobante que debita anticipos de clientes y acredita cartera por el valor aplicado, reducir el saldo de la factura y el saldo por aplicar del anticipo, y no MUST mover la cuenta bancaria.

#### Scenario: Aplicación parcial del anticipo
- **WHEN** se aplican 600.000 de un anticipo de 1.000.000 a una factura con saldo de 600.000
- **THEN** la factura queda «Cobrada», el anticipo queda «Pendiente por aplicar» con 400.000 y existe el comprobante por 600.000

#### Scenario: Anticipo consumido
- **WHEN** se aplica el saldo restante de un anticipo
- **THEN** el anticipo pasa a «Aplicado»

### Requirement: Aplicación cuenta como pago de la factura
Una aplicación de anticipo vigente SHALL contar como pago de la factura para su historial y para la matriz de acciones: la factura no MUST poder editarse, volver a borrador ni anularse mientras la tenga.

#### Scenario: Editar factura con anticipo aplicado
- **WHEN** se intenta editar una factura que tiene un anticipo aplicado
- **THEN** la acción está deshabilitada con el motivo de que tiene pagos

### Requirement: Retenciones del anticipo en la factura
Al aplicar un anticipo con retenciones, la factura SHALL mostrar esas retenciones como practicadas en el anticipo, en proporción al valor aplicado, sin volver a contabilizarlas. Si la factura ya tiene retenciones propias, el sistema MUST advertir del posible doble registro antes de confirmar.

#### Scenario: Retención trasladada
- **WHEN** se aplica a una factura la mitad de un anticipo que tuvo 40.000 de retención en la fuente
- **THEN** la factura muestra 20.000 de retención en la fuente practicada en ese anticipo y no se genera otro asiento por ella

#### Scenario: Factura con retenciones propias
- **WHEN** se aplica un anticipo con retenciones a una factura que ya registra retención en la fuente
- **THEN** el sistema muestra una advertencia y pide confirmación antes de aplicar

### Requirement: Desaplicar anticipo
Un usuario con permiso de registrar pagos SHALL poder desaplicar un anticipo de una factura indicando un motivo. La desaplicación MUST reversar el comprobante de la aplicación y devolver el valor al saldo de la factura y al saldo por aplicar del anticipo.

#### Scenario: Desaplicar
- **WHEN** se desaplica una aplicación de 600.000 de una factura cobrada
- **THEN** la factura vuelve a «Por cobrar» con 600.000 de saldo y el anticipo recupera 600.000 por aplicar

#### Scenario: Factura castigada o anulada
- **WHEN** la factura de la aplicación fue castigada
- **THEN** el sistema rechaza la desaplicación

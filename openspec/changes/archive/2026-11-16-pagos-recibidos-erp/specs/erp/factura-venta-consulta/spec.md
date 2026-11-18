# Spec Delta

## MODIFIED Requirements

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

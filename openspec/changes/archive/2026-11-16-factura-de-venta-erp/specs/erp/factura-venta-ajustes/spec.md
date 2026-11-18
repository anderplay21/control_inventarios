# Spec Delta

## Purpose

Define cómo se corrige una factura de venta ya emitida: editarla, devolverla a borrador o anularla mientras sea seguro, y ajustarla con notas crédito y débito, retenciones o un castigo de cartera cuando ya no lo es.

## ADDED Requirements

### Requirement: Edición, vuelta a borrador y anulación
El sistema SHALL permitir editar, convertir a borrador y anular una factura solo si no tiene pagos aplicados y no ha sido transmitida a la DIAN. Al hacerlo MUST reversar su asiento contable, y al volver a emitir una factura editada MUST generar uno nuevo.

#### Scenario: Factura libre
- **WHEN** se convierte a borrador una factura emitida sin pagos y con estado DIAN «por transmitir»
- **THEN** su asiento queda reversado, su saldo deja de contar en cartera y la factura queda en borrador editable

#### Scenario: Con pagos aplicados
- **WHEN** se intenta editar, volver a borrador o anular una factura que tiene pagos
- **THEN** el sistema lo rechaza e indica que debe corregirse con una nota crédito o débito

#### Scenario: Ya transmitida a la DIAN
- **WHEN** se intenta editar o anular una factura cuyo estado DIAN ya no es «por transmitir»
- **THEN** el sistema lo rechaza e indica que debe corregirse con una nota

#### Scenario: Anulación libre
- **WHEN** se anula una factura libre
- **THEN** pasa a «Anulada», su asiento se reversa y ya no admite edición ni pagos

### Requirement: Notas crédito y débito
El sistema SHALL permitir aplicar una nota crédito o una nota débito sobre una factura emitida, cada una con su propio consecutivo y motivo, vinculada a la factura y reflejada en su saldo y en los libros.

#### Scenario: Nota crédito parcial
- **WHEN** se aplica una nota crédito por 100.000 sobre una factura con saldo de 400.000
- **THEN** la nota recibe su número, queda asociada a la factura, el saldo pasa a 300.000 y se contabiliza el asiento inverso correspondiente

#### Scenario: Nota débito
- **WHEN** se aplica una nota débito por 50.000
- **THEN** el saldo aumenta en 50.000 y el asiento queda vinculado a la factura

#### Scenario: Nota sobre factura cobrada
- **WHEN** se aplica una nota crédito sobre una factura cobrada
- **THEN** el sistema acepta la nota y deja el saldo a favor registrado, sin reabrir la factura

#### Scenario: Motivo obligatorio
- **WHEN** se intenta aplicar una nota sin motivo
- **THEN** el sistema la rechaza

### Requirement: Retenciones por factura
El sistema SHALL permitir registrar y editar la retención en la fuente, la de ICA y la de IVA de cada factura. Los valores MUST venir prellenados con el cálculo del sistema para ese cliente y esa base, ser editables, y ajustar el saldo y el asiento.

#### Scenario: Prellenado
- **WHEN** se abre «Editar retenciones» de una factura de un cliente agente de retención
- **THEN** los campos muestran los valores calculados con la explicación de por qué aplica cada retención

#### Scenario: Valor distinto al calculado
- **WHEN** se guarda una retención diferente a la sugerida
- **THEN** el sistema conserva el valor digitado, ajusta el saldo y el asiento, y deja constancia de que fue modificado a mano

#### Scenario: Con pagos o transmitida
- **WHEN** se intenta editar las retenciones de una factura con pagos o ya transmitida
- **THEN** el sistema lo rechaza e indica que debe corregirse con una nota

### Requirement: Castigo de cartera
La acción «Cerrar sin pago» SHALL dar por perdido el saldo de una factura con un asiento de castigo, un motivo obligatorio y el registro de quién y cuándo lo hizo. Solo MUST estar disponible para quien tenga el permiso de castigar.

#### Scenario: Castigo con permiso
- **WHEN** una persona con el permiso de castigar cierra sin pago una factura con saldo de 300.000 e indica el motivo
- **THEN** el saldo queda en cero, se contabiliza el castigo por 300.000 contra la cartera y la factura queda cerrada con su motivo

#### Scenario: Sin permiso
- **WHEN** una persona sin ese permiso intenta cerrar sin pago
- **THEN** el sistema lo rechaza y no cambia la factura

#### Scenario: Factura cobrada
- **WHEN** se intenta cerrar sin pago una factura cobrada
- **THEN** el sistema lo rechaza porque no tiene saldo

#### Scenario: Sin motivo
- **WHEN** se intenta cerrar sin pago sin indicar el motivo
- **THEN** el sistema lo rechaza

### Requirement: Clonar y adjuntar
El sistema SHALL permitir clonar una factura en un borrador nuevo sin número, sin pagos y con las líneas copiadas, y adjuntar archivos a cualquier factura, sea cual sea su estado.

#### Scenario: Clonar
- **WHEN** se clona una factura emitida
- **THEN** se crea un borrador con los mismos datos y líneas, sin número, sin pagos y con la fecha del día

#### Scenario: Adjuntar a una anulada
- **WHEN** se adjunta un archivo a una factura anulada
- **THEN** el archivo queda asociado a ella

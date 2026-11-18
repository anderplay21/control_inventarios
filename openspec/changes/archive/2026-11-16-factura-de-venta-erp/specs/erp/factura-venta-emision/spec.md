# Spec Delta

## Purpose

Permite crear una factura de venta o un ticket desde el ERP con numeración automática por resolución, datos del cliente y condiciones comerciales, líneas libres y totales calculados, sin depender del WMS ni del POS.

## ADDED Requirements

### Requirement: Tipo de documento y numeración automática
El sistema SHALL permitir elegir entre «Factura de venta» y «Ticket», y MUST asignar el número al guardar desde la resolución vigente de la empresa para ese tipo. La persona usuaria MUST NOT digitar el número.

#### Scenario: Factura de venta con resolución vigente
- **WHEN** se guarda una factura de venta y la empresa tiene una resolución vigente de factura de venta con consecutivos disponibles
- **THEN** la factura recibe el siguiente número de esa resolución y el consecutivo avanza en uno

#### Scenario: Ticket con su propia numeración
- **WHEN** se guarda un documento de tipo «Ticket»
- **THEN** el número sale de la resolución de tipo POS de la empresa, independiente del consecutivo de las facturas de venta

#### Scenario: Sin resolución vigente
- **WHEN** se guarda un documento y no hay resolución vigente para su tipo
- **THEN** el sistema rechaza el guardado con un mensaje que indica qué resolución falta configurar y no crea la factura

#### Scenario: Resolución agotada
- **WHEN** la resolución vigente ya no tiene números disponibles
- **THEN** el sistema rechaza el guardado y no consume ni repite un número

### Requirement: Datos generales del documento
El formulario SHALL pedir moneda, centro de costo, vendedor y forma de pago. El centro de costo MUST salir de los centros de costo del ERP de la empresa, y el vendedor MUST ser un tercero marcado como vendedor.

#### Scenario: Selectores con datos de la empresa
- **WHEN** se abre el formulario de nueva factura
- **THEN** la moneda, el centro de costo, el vendedor y la forma de pago se eligen de listas desplegables con los valores configurados de la empresa

#### Scenario: Solo terceros vendedores
- **WHEN** se despliega la lista de vendedores
- **THEN** solo aparecen terceros activos marcados como vendedor

#### Scenario: Formulario sin bodega ni lista de precios
- **WHEN** se abre el formulario en una empresa sin inventario propio
- **THEN** no se muestran los campos de bodega ni de lista de precios y el precio de cada línea se digita a mano

### Requirement: Cliente y condiciones comerciales
El formulario SHALL pedir el tipo y número de identificación, el nombre o razón social y el correo electrónico del cliente, además de la forma y el medio de pago. Si el cliente ya existe como tercero, sus datos MUST autocompletarse.

#### Scenario: Cliente existente
- **WHEN** se ingresa un tipo y número de identificación que ya existen como tercero cliente
- **THEN** el nombre o razón social y el correo se completan con los datos del tercero y siguen siendo editables

#### Scenario: Cliente nuevo
- **WHEN** se ingresa una identificación que no existe
- **THEN** el documento se guarda con los datos digitados y el sistema crea el tercero cliente correspondiente

#### Scenario: Correo con formato inválido
- **WHEN** se digita un correo que no tiene formato de correo electrónico
- **THEN** el sistema marca el campo y no permite guardar hasta corregirlo

### Requirement: Líneas libres y remisión
El sistema SHALL permitir agregar y quitar líneas con referencia, descripción, cantidad, precio, descuento e impuesto, y MUST calcular el total de cada línea. La referencia es texto libre. El documento puede incluir una remisión como dato informativo.

#### Scenario: Línea calculada
- **WHEN** se ingresa una cantidad de 3, un precio de 100.000, un descuento del 10 % y un impuesto del 19 %
- **THEN** la línea muestra un subtotal de 270.000, un impuesto de 51.300 y un total de 321.300

#### Scenario: Agregar y quitar líneas
- **WHEN** se pulsa agregar línea o se elimina una existente
- **THEN** la lista de líneas y los totales del documento se actualizan al instante

### Requirement: Totales del documento
El sistema SHALL mostrar subtotal, descuentos, impuestos y total neto a pagar, calculados a partir de las líneas, y el total guardado MUST coincidir con el mostrado.

#### Scenario: Totales coinciden
- **WHEN** se guarda un documento con varias líneas con distintos descuentos e impuestos
- **THEN** el subtotal, los descuentos, los impuestos y el total guardados son iguales a los que mostraba el formulario

### Requirement: Validación al guardar
Al guardar, el sistema MUST validar los campos obligatorios y que exista al menos una línea con cantidad y precio válidos. Si todo es correcto, SHALL almacenar el documento, contabilizarlo y emitirlo.

#### Scenario: Sin líneas
- **WHEN** se intenta guardar un documento sin ninguna línea válida
- **THEN** el sistema lo rechaza y señala que se requiere al menos un ítem

#### Scenario: Guardado correcto
- **WHEN** se guarda un documento válido
- **THEN** queda emitido con su número, su asiento contable y su cuenta por cobrar por el total, y se abre su detalle

#### Scenario: Período contable cerrado
- **WHEN** la fecha del documento cae en un período cerrado
- **THEN** el sistema rechaza el guardado y no deja un documento sin asiento

### Requirement: Complementos del documento
El formulario SHALL permitir notas visibles en el documento, notas adicionales internas, adjuntar o capturar una firma, comentarios internos y registrar un pago durante la creación.

#### Scenario: Notas e internos separados
- **WHEN** se escriben una nota y una nota adicional
- **THEN** la nota aparece en el documento impreso y la nota adicional solo en la vista interna

#### Scenario: Pago al crear
- **WHEN** se registra un pago con cuenta bancaria durante la creación
- **THEN** la factura y su recaudo se guardan juntos o no se guarda ninguno

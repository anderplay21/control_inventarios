# pagos-recibidos-registro Specification

## Purpose

Registra el dinero que recibe la empresa como un recibo de caja, ya sea pago de facturas, anticipo u otro ingreso, y mantiene consistentes la cartera, los bancos y la contabilidad, incluida su anulación.

## Requirements

### Requirement: Cliente registrado obligatorio
Todo recibo nuevo creado desde «+ Nuevo ingreso» SHALL pertenecer a un tercero registrado de la empresa. Un recibo de ese formulario sin tercero MUST rechazarse. La única excepción es el pago hecho desde una factura de venta que no tiene tercero (consumidor final o POS): ese recibo SHALL aceptarse sin tercero y tomar el nombre y la identificación de la factura.

#### Scenario: Sin tercero
- **WHEN** se intenta guardar un recibo escribiendo un nombre de cliente que no está registrado como tercero
- **THEN** el sistema lo rechaza y pide elegir un tercero existente

#### Scenario: Pago de una factura de consumidor final
- **WHEN** se registra un pago desde una factura de venta sin tercero (consumidor final o POS)
- **THEN** el recibo se crea sin tercero, con el nombre y la identificación de la factura, y la factura queda pagada o abonada como con cualquier otro recibo

### Requirement: Datos generales del recibo
El formulario SHALL pedir el tipo de ingreso, la cuenta bancaria o caja de destino, la fecha de pago, la forma y el medio de pago, la moneda y el centro de costo. La cuenta, la fecha y el tipo de ingreso MUST ser obligatorios.

#### Scenario: Cuenta faltante
- **WHEN** se intenta guardar sin elegir la cuenta bancaria
- **THEN** el sistema no guarda y pide elegirla

#### Scenario: Fecha por omisión
- **WHEN** se abre un nuevo ingreso
- **THEN** la fecha trae el día actual y puede cambiarse

### Requirement: Tipos de ingreso
El recibo SHALL ser de uno de tres tipos: «Pago a factura», «Anticipo» u «Otros ingresos». El tipo MUST determinar qué secciones muestra el formulario y cómo se contabiliza.

#### Scenario: Cambio de tipo
- **WHEN** el usuario cambia el tipo de «Pago a factura» a «Otros ingresos»
- **THEN** se oculta la tabla de facturas por cobrar y aparece la selección de cuenta contable y de retenciones

### Requirement: Facturas por cobrar del cliente
En «Pago a factura», al elegir el cliente, el sistema SHALL cargar todas sus facturas emitidas con saldo pendiente en la moneda del recibo, incluidas las antiguas que coinciden por número de identificación aunque no estén vinculadas al tercero.

#### Scenario: Factura antigua por NIT
- **WHEN** el cliente con NIT 900123456 tiene una factura anterior guardada solo con ese NIT
- **THEN** la factura aparece en la tabla de facturas por cobrar

#### Scenario: Otra moneda
- **WHEN** el cliente tiene una factura en USD y el recibo es en COP
- **THEN** esa factura no aparece en la tabla

### Requirement: Monto a recibir por factura
Cada factura cargada SHALL tener un campo «Monto a recibir» para el saldo total o un abono. El valor MUST ser mayor que cero y no superar el saldo pendiente, y el recibo MUST aplicar dinero al menos a una factura.

#### Scenario: Pago total y abono en el mismo recibo
- **WHEN** se reciben 800.000 para FV-120 (saldo 800.000) y 300.000 para FV-131 (saldo 500.000)
- **THEN** el recibo queda por 1.100.000, FV-120 pasa a «Cobrada» y FV-131 queda «Por cobrar» con saldo 200.000

#### Scenario: Monto mayor que el saldo
- **WHEN** se ingresan 600.000 para una factura con saldo de 500.000
- **THEN** el sistema rechaza el recibo indicando el saldo máximo y no guarda nada

#### Scenario: Ninguna factura con monto
- **WHEN** se guarda un pago a factura con todos los montos vacíos o en cero
- **THEN** el sistema lo rechaza

### Requirement: Retenciones según el tipo
En «Pago a factura» el recibo SHALL mostrar en modo lectura las retenciones registradas en cada factura. En «Anticipo» y «Otros ingresos» SHALL permitir agregar retenciones eligiendo el tipo de una lista (retención en la fuente, de IVA o de ICA) y su concepto; el porcentaje MUST sugerirse según la configuración fiscal y ser editable.

#### Scenario: Retención en otro ingreso
- **WHEN** se registra un otro ingreso de 1.000.000 con retención en la fuente del 4%
- **THEN** el valor bruto es 1.000.000, la retención 40.000 y el valor que entra al banco 960.000

#### Scenario: Pago a factura sin retenciones nuevas
- **WHEN** se registra un pago a factura
- **THEN** el formulario no permite agregar retenciones y muestra las de cada factura

### Requirement: Otros ingresos contra cuenta elegida
En «Otros ingresos» el usuario SHALL elegir, para cada línea, una cuenta contable de movimiento del plan de cuentas, una descripción y un valor. Las cuentas agrupadoras MUST rechazarse.

#### Scenario: Cuenta agrupadora
- **WHEN** se elige la cuenta 4210 «Financieros», que es agrupadora
- **THEN** el sistema la rechaza y pide una subcuenta

#### Scenario: Asiento del otro ingreso
- **WHEN** se guarda un otro ingreso de 1.000.000 contra 421005 con retención en la fuente de 40.000
- **THEN** el comprobante debita bancos por 960.000 y la retención soportada por 40.000, y acredita 421005 por 1.000.000

### Requirement: Anticipo como pasivo
Un recibo de tipo «Anticipo» SHALL contabilizarse contra la cuenta de anticipos recibidos de clientes, no como ingreso, y MUST dejar un saldo a favor del cliente igual a su valor bruto.

#### Scenario: Registro del anticipo
- **WHEN** se recibe un anticipo de 1.000.000 sin retenciones
- **THEN** el comprobante debita bancos y acredita anticipos de clientes por 1.000.000, el recibo queda «Pendiente por aplicar» y el cliente tiene 1.000.000 a favor

### Requirement: Consecutivo único
Al guardar, el sistema SHALL asignar al recibo el siguiente número del talonario de recibos de caja de la empresa. El número MUST ser único, sin repeticiones con guardados simultáneos, y un guardado fallido no MUST consumir un número visible.

#### Scenario: Dos recibos a la vez
- **WHEN** dos usuarios guardan un recibo en el mismo instante
- **THEN** cada uno recibe un número distinto y consecutivo

### Requirement: Efecto en la cuenta bancaria
Cada recibo guardado SHALL crear un movimiento de entrada en la cuenta bancaria elegida por el valor neto recibido, vinculado a su comprobante, y MUST aumentar el saldo contable de la cuenta por ese valor.

#### Scenario: Saldo de la cuenta
- **WHEN** se guarda un recibo con valor neto de 960.000 en una cuenta con saldo contable de 5.000.000
- **THEN** la cuenta queda con 5.960.000 y aparece un movimiento de entrada por 960.000 sin conciliar

### Requirement: Guardado atómico
El recibo, sus aplicaciones, su asiento, el movimiento bancario y el cambio de saldo de las facturas SHALL guardarse en una sola operación. Si cualquier parte falla, incluido un período contable cerrado, no MUST quedar nada.

#### Scenario: Período cerrado
- **WHEN** la fecha del recibo cae en un período contable cerrado
- **THEN** el sistema rechaza el recibo y no cambian las facturas, el banco ni los libros

### Requirement: Notas y comentarios
El recibo SHALL tener «Notas y detalles adicionales», que se imprimen en el PDF, y «Comentarios y recordatorios» internos, que no se imprimen. Los comentarios MUST poder agregarse también después de guardar.

#### Scenario: Comentario posterior
- **WHEN** se agrega un comentario a un recibo ya guardado
- **THEN** queda registrado con su autor y fecha en el detalle

### Requirement: Pago desde la factura de venta
El pago rápido de la factura de venta y el registro de cobros por el endpoint general de pagos SHALL crear un recibo de pago a factura de una sola línea, con el mismo consecutivo, validaciones y efectos.

#### Scenario: Pago rápido
- **WHEN** se registra un pago rápido de 200.000 sobre la factura FV-150
- **THEN** existe un recibo nuevo con el siguiente número del talonario, aplicado a FV-150 por 200.000, y aparece en Pagos recibidos

### Requirement: Anulación del recibo
Un usuario con permiso de anular SHALL poder anular un recibo indicando un motivo. La anulación MUST reversar su asiento y su movimiento bancario y devolver a cada factura el valor aplicado, recalculando su estado. El recibo MUST quedar «Anulado» y visible.

#### Scenario: Anular un pago a factura
- **WHEN** se anula un recibo que dejó «Cobrada» a FV-120
- **THEN** FV-120 recupera su saldo y vuelve a «Por cobrar», el banco baja por el valor neto y existe el comprobante de reverso

#### Scenario: Factura castigada o anulada
- **WHEN** una de las facturas del recibo fue castigada o anulada después del pago
- **THEN** el sistema rechaza la anulación del recibo e indica la factura

#### Scenario: Anticipo ya aplicado
- **WHEN** se intenta anular un anticipo que tiene aplicaciones vigentes
- **THEN** el sistema lo rechaza e indica que primero se desapliquen

#### Scenario: Sin motivo
- **WHEN** se intenta anular sin indicar motivo
- **THEN** el sistema lo rechaza

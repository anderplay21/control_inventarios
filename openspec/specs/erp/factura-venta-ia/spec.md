# factura-venta-ia Specification

## Purpose

Permite completar el formulario de una nueva factura de venta a partir de un PDF o una imagen, para ahorrar digitación y errores, dejando siempre la revisión y el guardado en manos de la persona usuaria.

## Requirements

### Requirement: Activación y carga del archivo
El formulario de nueva factura SHALL mostrar el botón del Agente de IA con la opción «Completar usando un archivo o imagen», y MUST aceptar archivos PDF, JPG, JPEG y PNG. Cualquier otro formato MUST rechazarse con un mensaje.

#### Scenario: Formato permitido
- **WHEN** se elige un archivo `.pdf`, `.jpg`, `.jpeg` o `.png`
- **THEN** el sistema lo recibe y comienza el análisis

#### Scenario: Formato no permitido
- **WHEN** se elige un archivo de otro tipo
- **THEN** el sistema lo rechaza e indica los formatos aceptados, sin llamar al agente

#### Scenario: Archivo demasiado grande
- **WHEN** el archivo supera el tamaño máximo permitido
- **THEN** el sistema lo rechaza e indica el límite

### Requirement: Autocompletado con indicador de avance
Mientras el agente analiza el archivo, el sistema SHALL mostrar un indicador de procesamiento. Al terminar, MUST rellenar los datos del cliente, las fechas, la moneda, la forma y el medio de pago, las líneas y las notas que haya detectado.

#### Scenario: Extracción exitosa
- **WHEN** el agente termina de analizar una factura legible
- **THEN** el formulario muestra el cliente, las fechas, la moneda, los pagos, las líneas con referencia, descripción, cantidad, precio, descuento e impuesto, y las notas detectadas

#### Scenario: Datos que no detecta
- **WHEN** el archivo no contiene un dato, como el vendedor o el centro de costo
- **THEN** ese campo queda vacío para que la persona lo complete

#### Scenario: Archivo ilegible
- **WHEN** el agente no logra leer el archivo
- **THEN** el sistema avisa que no pudo extraer datos y deja el formulario como estaba

### Requirement: Revisión antes de guardar
Los campos autocompletados MUST permanecer editables, y el agente MUST NOT guardar ni emitir ningún documento. Al guardar, el sistema SHALL aplicar las mismas validaciones que en la creación manual.

#### Scenario: Edición libre
- **WHEN** la persona cambia un valor autocompletado
- **THEN** el formulario conserva su cambio y los totales se recalculan

#### Scenario: Guardar aplica las validaciones normales
- **WHEN** se guarda una factura completada por el agente que no tiene líneas válidas o le falta un dato obligatorio
- **THEN** el sistema la rechaza igual que si se hubiera creado a mano

#### Scenario: Sin guardado automático
- **WHEN** el agente termina de rellenar el formulario
- **THEN** no existe ninguna factura nueva hasta que la persona pulse Guardar

### Requirement: Disponibilidad y permiso
La función SHALL estar disponible solo para perfiles con el permiso del asistente de IA, y MUST informar con claridad cuando no está configurada en el servidor, sin afectar la creación manual.

#### Scenario: Sin permiso
- **WHEN** una persona sin el permiso del asistente abre el formulario
- **THEN** el botón del Agente de IA no se muestra y la creación manual funciona igual

#### Scenario: IA no configurada
- **WHEN** el servidor no tiene configurado el servicio de IA
- **THEN** el botón indica que la función no está disponible y el formulario sigue operando

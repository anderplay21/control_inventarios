# Mapeo de Alegra (Colombia) para el módulo contable ERP-financiero

Levantado el 18 de noviembre de 2026 sobre una cuenta **Plan Demo** de Alegra Contabilidad (versión Colombia) en `app.alegra.com` y `mi.alegra.com`.
El recorrido fue de solo lectura: no se creó, editó ni borró nada en la cuenta.

**Qué no cubre:**
- Pantallas que exigen una acción irreversible o habilitar algo, como elegir el catálogo de cuentas, habilitar la facturación electrónica o activar Nómina, POS o CRM.
- Matrices de permisos por rol: los roles personalizados piden un plan Pro o Plus.
- Los formularios que se abren desde el detalle de un registro no se probaron uno por uno.

La sección 10 cruza lo encontrado con lo que ya existe en `backend/app` (modelos `erp*.py` y endpoints `erp*.py`).

---

## 1. Estructura de navegación

### 1.1 Barra superior
| Elemento | Función |
|---|---|
| Buscar | Búsqueda global (documentos, contactos, ítems) |
| **+** (creación rápida) | Ingreso, Gasto, Factura de venta, Remisión, Factura de proveedor, Orden de compra, Ítem de venta, Contacto, Cotización, Otros |
| ? | Centro de ayuda |
| Ícono de sostenibilidad | Enlace informativo |
| Cuadrícula de apps | Cambio entre soluciones (Contabilidad, Nómina, POS, etc.) |
| Selector de empresa | Varias empresas por usuario |
| Avatar | Perfil y cuenta (`mi.alegra.com`) |

### 1.2 Menú lateral (árbol completo con rutas)

```
Inicio                                  /
Bandeja de entrada                      /inbox            (Principal · Novedades · Archivo)
Habilitar factura electrónica           (asistente DIAN)
Ingresos
  Factura de venta                      /invoice            · /invoice/add
  Facturas de venta recurrentes         /recurring-invoice  · /recurring-invoice/add
  Pagos recibidos (recibos de caja)     /income-payments    · /income-payments/add
  Devoluciones en ventas (NC)           /credit-note        · /credit-note/add
  Notas débito (ventas)                 /income-debit-note  · /income-debit-note/add
  Cotizaciones                          /estimate           · /estimate/add
  Remisiones                            /remission          · /remission/add
Gastos
  Compras automáticas (XML DIAN)        /stamp-document-received
  Facturas de compra                    /bill               · /bill/add
  Documento soporte                     /support-document   · /support-document/add
  Notas de ajuste (a doc. soporte)      /adjustment-note    · /adjustment-note/add
  Pagos (comprobantes de egreso)        /payment            · /payment/add
  Pagos recurrentes                     /recurring-payment  · /recurring-payment/add
  Notas débito (compras)                /debit-note         · /debit-note/add
  Órdenes de compra                     /purchase-order     · /purchase-order/add
Contactos                               /client             · /client/add
Inventario
  Ítems de venta                        /item               · /item/add
  Valor de inventario                   /report/inventory
  Ajustes de inventario                 /inventory-adjustment · /inventory-adjustment/add
  Gestión de ítems (importar/masivo)    /item/items-management
  Listas de precios                     /price-list         · /price-list/add
  Bodegas                               /warehouse          · /warehouse/add
  Categorías                            /item-category      · /item-category/add
  Atributos                             /item/attributes
Bancos
  Bancos y cajas                        /account
  Conciliaciones bancarias              /conciliation
Contabilidad
  Sincronización contable               /accounting-synchronization
  Catálogo de cuentas                   /category
  Comprobante contable                  /ledger/documents   · /ledger/documents/add
  Activos (fijos)                       /fixed-assets       · /fixed-assets/add
  Libro diario                          /report/ledger-journals
  Contabilizador fiscal (nuevo)         /tax-accountant/col
  Conciliador fiscal                    /tax-conciliator    (se retira el 31/01/2027)
  Complementos contables                /accounting
Reportes                                /report
Mis tareas                              /task-manager
Configuración                           /configuration
Descubre más soluciones: Nómina · POS · Portal de clientes · CRM · Alegra Ventas
```

### 1.3 Inicio (tablero)
- Lista de primeros pasos: facturación electrónica, primera venta, primera factura de compra e invitar al contador.
- **Resumen del negocio**, con filtro de periodo y botón "Agregar gráfica":
  - Cuentas por cobrar y por pagar, separadas en vigentes y vencidas, con importe y número de documentos.
  - Impuestos en venta, productos vendidos, devoluciones de clientes, clientes con ventas y la gráfica de total de ventas.
  - Cada tarjeta enlaza a su reporte.

---

## 2. Contabilidad (núcleo del módulo)

### 2.1 Catálogo de cuentas `/category`
- Dos catálogos precargados para elegir:
  - **Catálogo NIIF**: el que se usa por defecto. Está organizado por grupos de presentación: Activos (corrientes y no corrientes), Pasivos, Patrimonio (capital, reservas, resultado, ganancias acumuladas, superávit, ajustes por saldos iniciales), Ingresos (ordinarios y otros), Gastos (venta, administración, generales, financieros, otros, impuestos), Costos (ventas y operación, producción, MPD, MO, CIF, contratos de servicios), Cuentas de orden y Transferencias bancarias.
  - **Catálogo PUC**: clases 1 a 8 con sus grupos de 2 dígitos (11 Disponible … 74 Contratos de servicios), según el PUC colombiano.
- Otras funciones:
  - **Importar un catálogo propio**.
  - **Saldos iniciales**: carga de apertura.
  - **Exportar a Excel**.
  - Asistente "¿Te ayudamos a elegir el catálogo?".
- **Parametrización contable** (`/category/accounting-parameterization`): define las cuentas por defecto que usa la contabilización automática.

| Grupo | Parámetro | Cuenta por defecto |
|---|---|---|
| Ventas | Ingresos por ventas | Ventas |
| | Devoluciones en ventas | Devoluciones en ventas |
| | Devolución del IVA en ventas | IVA devuelto en ventas |
| Inventario | Inventario | Inventarios |
| | Ajustes de inventario | Ajustes al inventario |
| Clientes y proveedores | CxC clientes | CxC clientes nacionales |
| | Devoluciones de clientes | Devoluciones de clientes |
| | CxP proveedores | CxP proveedores nacionales |
| | Devoluciones de proveedores | Devoluciones a proveedores |
| | Devolución del IVA en compras | IVA devuelto en compras a favor |
| Patrimonio | Ganancias acumuladas / Utilidad / Pérdida del ejercicio | (homónimas) |
| | Ajuste saldos iniciales bancos / inventarios | Ajustes iniciales en bancos / inventario |
| Ajustes y aproximaciones | Ganancia / pérdida por diferencia en cambio | (homónimas) |
| | Aproximación decimal en ingresos / gastos | Ajustes por aproximaciones en cálculos |
| Impuestos y retenciones | Se configuran en cada impuesto y retención | — |

- **Actualizar registros contables** (`/company/accounting-configuration`): define desde qué fecha se usan las cuentas propias de cada entidad, en vez de las cuentas generales. Al cambiarlo, **se recontabilizan los movimientos de los periodos abiertos**.
  - Cuentas asociadas a cada impuesto y retención.
  - Cuenta de CxC de cada cliente.
  - Cuenta de CxP de cada proveedor.
  - Cuenta de inventario de cada producto.
  - Cuentas de costo de ventas de cada ítem.

### 2.2 Comprobante contable `/ledger/documents/add`
- **Encabezado**:
  - Fecha.
  - Numeración (por ejemplo "Ajuste contable").
  - Tipo de comprobante contable.
  - Código.
  - Observaciones.
- **Movimientos** (una línea por partida):
  - Cuenta contable.
  - **Contacto (tercero)**.
  - N° de documento.
  - Descripción.
  - **Centro de costos**.
  - Débito y crédito.
- **Totales**: débito, crédito y **diferencia**. El comprobante no se puede guardar si está descuadrado.
- Botones Guardar y "Guardar y crear otro".

**Tipos de comprobante** (`/ledger/document-types`): son 17 y se pueden crear más con nombre y código.

| Código | Tipo | Código | Tipo |
|---|---|---|---|
| AC | Ajuste contable | CPP | Cuentas por pagar |
| CSC | Ajustes por cierre sin pago en compras | D | Depreciaciones |
| CSV | Ajustes por cierre sin pago en ventas | IMP | Impuestos |
| DTB | Ajustes por diferencia en tasa de bancos | ING | Ingresos |
| A | Amortizaciones | NO | Nómina |
| AE | Avances y anticipos entregados | PV | Provisiones |
| AR | Avances y anticipos recibidos | RET | Retenciones |
| CA | Cierre de periodos contables | SI | Saldos iniciales |
| CPC | Cuentas por cobrar | | |

**Numeraciones contables** (`/ledger/numerations`): cada una tiene tipo de comprobante, nombre, si es la preferida, prefijo y siguiente número. Hay una por tipo, y el prefijo coincide con el código del tipo.

**Prefijos que se ven en el libro diario según el documento de origen:**

| Prefijo | Documento de origen |
|---|---|
| FV | Factura de venta |
| RC | Recibo de caja |
| CE | Comprobante de egreso |
| NC | Nota crédito |
| CB | Movimiento de banco o saldo inicial |
| II | Inventario inicial |

### 2.3 Libro diario `/report/ledger-journals`
- **Filtros**: rango de fechas y tipo de registro.
- **Columnas**: asiento, estado, fecha, tercero, código, cuenta contable, centro de costo, débito y crédito.
- Es paginado y se puede descargar.
- **Todo documento operativo genera su asiento automáticamente.** Por ejemplo, una factura de venta con ítem inventariable produce:
  - Débito a CxC y crédito a Ventas y a IVA por pagar.
  - Además, débito a Costo y crédito a Inventarios.

### 2.4 Activos fijos `/fixed-assets/add`
- **Información general**:
  - Tipo de activo: tangible o intangible.
  - Nombre, referencia y tipo.
  - Fecha de adquisición.
  - Proveedor.
  - Costo de adquisición, cantidad y monto total de inversión.
  - Cuenta contable del activo.
- **Depreciación**:
  - Cuenta de depreciación acumulada.
  - Cuenta de gasto.
  - Valor residual: no puede superar el costo.
  - Fecha de inicio de la depreciación.
  - Método.
  - Periodo.
  - Forma de cálculo: por vida útil o por porcentaje.
- **Otros**:
  - Foto.
  - Adjuntos (pdf, jpeg, png, docx; máximo 10 MB).
  - Casilla "Contabilizar creación del activo".
- La lista muestra el costo de adquisición, el valor depreciado y el valor actual.

### 2.5 Cierres
- **Cierre contable de resultados** (`/closing-periods`):
  - Tabla de meses del 1 al 12 con su última apertura, último cierre y estado (abierto o cerrado).
  - Una fila 13 "Año" con el botón **Cerrar año**, que traslada el resultado del ejercicio.
- **Cierre de cuentas por tercero** (en Complementos): cierre anual de los movimientos de cada tercero y sus cuentas.
- **Certificados de retención**: envío masivo a los proveedores seleccionados.

### 2.6 Automatización fiscal con la DIAN
- **Contabilizador fiscal**:
  - Sincroniza ventas, compras y contactos desde la DIAN para cualquier periodo.
  - Clasifica y contabiliza automáticamente, con IA, en cuentas o productos.
  - Concilia los documentos de la DIAN con los registrados en Alegra.
  - Genera la **declaración de IVA** lista para presentar.
- **Conciliador fiscal**: compara lo reportado ante la DIAN con lo contabilizado. Necesita la facturación electrónica habilitada.
- **Sincronización contable**: migra el catálogo, los saldos iniciales y otra información desde cualquier sistema o archivo.

---

## 3. Ingresos (ventas)

### 3.1 Factura de venta `/invoice/add`
- **Encabezado**:
  - Tipo de documento: Factura de venta o Tiquete.
  - Moneda.
  - Vendedor.
  - Numeración y número.
  - Cliente: búsqueda por número de identificación, autocompletado y opción "Nuevo contacto".
  - Fecha.
  - Forma de pago: contado o crédito, con plazo.
  - Medio de pago.
- **Líneas**:
  - Ítem y referencia.
  - Precio y descuento (%).
  - **Impuesto**: puede haber varios si se activa en la configuración avanzada.
  - Descripción, cantidad y total.
- **Otros campos**:
  - "Agregar remisión", para facturar remisiones existentes.
  - Firma y logo.
  - Términos y condiciones, notas y pie de factura.
- **Pago recibido en línea** (opcional): numeración, fecha, cuenta bancaria, método y valor.
- Comentarios y recordatorios con @menciones.
- Botones Vista previa, Guardar y "Guardar y crear nueva".

**Detalle de la factura** (`/invoice/view/id/{id}`):
- **Acciones**: editar, imprimir, descargar PDF, compartir, agregar pago y "Más acciones".
- **Cifras**: valor total, retenido, cobrado y por cobrar, más el estado (Por cobrar o Cobrada).
- **Información adicional**: forma y medio de pago, tipo de factura, lista de precios y bodega.
- **Pestañas**:
  - *Pagos recibidos*: fecha, número de pago, estado, método, monto y observaciones.
  - **Contabilidad**: el asiento que generó la factura.
- Adjuntos y comentarios.

**Lista de facturas**: número, cliente, creación, vencimiento, total, por cobrar y estado.

### 3.2 Otros documentos de venta
| Documento | Campos y comportamiento clave |
|---|---|
| Factura recurrente | Recurrencia: fecha de inicio, frecuencia en meses, fecha fin o indefinida, y día de emisión. "Generar como" (borrador o emitida). Cliente, forma de pago, ítems, retenciones, notas y términos |
| Pago recibido (recibo de caja) | Cliente, cuenta bancaria, fecha, forma de pago, moneda y centro de costo. **Tipo de ingreso**: *Pago a facturas del cliente* (trae sus facturas por cobrar) u *Otros ingresos* (a cuentas contables). Estado de conciliación bancaria |
| Devolución (nota crédito) | Tipo: Nota crédito o Nota de ajuste POS. Cliente, **tipo de nota crédito** (concepto DIAN), fecha y **factura asociada** (obligatoria) |
| Nota débito | Asociada a una factura |
| Cotización | Cliente, fecha y vencimiento, ítems, descuento masivo sobre los ítems seleccionados, retenciones sugeridas, notas y términos. Se convierte en factura o remisión |
| Remisión | Entrega sin factura; luego se factura desde "Agregar remisión" |

---

## 4. Gastos (compras)

| Documento | Campos y comportamiento clave |
|---|---|
| Compras automáticas | Importa XML de facturas electrónicas recibidas y genera la factura de compra automáticamente, con mapeo a productos o cuentas |
| Factura de compra `/bill/add` | Moneda, **bodega**, **centro de costo**, proveedor (con opción de crearlo), creación y vencimiento. Líneas con concepto (ítem **o cuenta contable**), precio, descuento, impuesto, cantidad y observaciones. **Agregar retención** y asociar **órdenes de compra**. Botón "Guardar y agregar pago" |
| Documento soporte (no obligados a facturar) | Proveedor, fecha de operación, forma y medio de pago, conceptos con costo, descuento, impuesto y cantidad, retenciones, OC y texto de autorización. Es un documento electrónico ante la DIAN |
| Nota de ajuste | Corrige un documento soporte: tipo de nota, documento soporte asociado y razón |
| Pago (comprobante de egreso) `/payment/add` | Contacto, cuenta bancaria, fecha, forma de pago, moneda, centro de costo y nota. Puede pagar **facturas de compra** o ir directo a **cuentas contables** (concepto, valor, impuesto, cantidad, observaciones). **Retenciones sobre cuentas contables**. Para mover dinero entre cuentas propias se usa *Transferencias* |
| Pagos recurrentes | Egresos programados |
| Nota débito (compras) | Asociada a una factura de compra |
| Orden de compra | Proveedor, fecha, **fecha de entrega**, bodega, centro de costo y líneas. Se convierte en factura de compra |

---

## 5. Bancos

- **Bancos y cajas** (`/account`):
  - Tipos de cuenta: banco, tarjeta de crédito y efectivo (caja).
  - Columnas: nombre, tipo, número enmascarado, saldo y botón **Conciliar**.
  - Resumen: saldo en bancos y efectivo, deuda en tarjetas y saldo total.
  - Gráfica de ingresos y gastos de los últimos 6 meses.
  - **Transferir** entre cuentas.
  - Formulario de alta: nombre, tipo, moneda, número, **saldo inicial** y su fecha, y descripción. El saldo inicial genera un asiento CB contra "Ajustes iniciales en bancos".
- **Conciliaciones bancarias** (`/conciliation`):
  - Se elige la cuenta y se marcan los movimientos.
  - Genera un reporte por conciliación.
  - Los pagos muestran su estado "No conciliado" o "Conciliado".
- **Diferencia en cambio de bancos**: tiene su propio reporte y tipo de comprobante (DTB).

---

## 6. Inventario (lo que toca a contabilidad)

- **Ítem** (`/item/add`):
  - Tipo: producto, servicio o combo. No se puede cambiar después de crearlo.
  - Nombre, bodega, categoría, unidad de medida, referencia y descripción.
  - Cantidad inicial, **costo por unidad**, precio base, impuesto y precio total.
  - Información fiscal: **clasificación de IVA** y **código del producto o servicio**.
  - Variantes.
  - Cantidades por bodega.
  - Listas de precios.
  - Campos adicionales (por ejemplo código de barras).
  - **Configuración contable**: cuenta contable de ingreso, **cuenta de inventario** y **cuenta de costo de venta**.
  - Opciones "Inventariable" (lleva costo promedio) y "Venta en negativo".
- **Ajuste de inventario**:
  - Fecha, bodega, numeración, centro de costo y observaciones.
  - Por ítem: tipo (entrada o salida), cantidad, cantidad final, **costo promedio** y total ajustado.
  - Permite cargar los ítems desde un archivo o una imagen.
- **Valor de inventario**: ítem, cantidad, unidad, estado, costo promedio y total. Se filtra por fecha de corte y bodega.
- Bodegas con **centro de costo** y transferencias entre bodegas.
- Listas de precios por valor o porcentaje.
- Importación y actualización masiva desde Excel.

---

## 7. Reportes `/report` (42 en 7 categorías, más Favoritos, Recientes e Historial de exportables)

| Categoría | Reportes (ruta) |
|---|---|
| **Ventas (8)** | Ventas generales `/report/sales` · Ventas por ítem `/sales-by-item` · por cliente `/sales-by-client` · Rentabilidad por ítem `/items-profit` · por vendedor `/sales-by-seller` · Estado de cuenta por cliente `/client-statement` · por bodega `/sales-by-warehouse` |
| **Administrativos (7)** | Cuentas por cobrar `/invoices-open` (edades: 0-30, 31-60, 61-90, 91+ y no vencidas) · Cuentas por pagar `/bills-open` · Ingresos y gastos `/income-and-expense` · Transacciones `/transactions` · Compras `/purchases` · Reporte anual `/annual` · Valor de inventario `/inventory` |
| **Financieros (1)** | Flujo de caja `/cash-flow-financial` |
| **Contables (11)** | Estado de resultados `/profit-and-loss` · Estado de situación financiera `/general-balance` · Estado de flujo de efectivo `/statement-of-cash-flows` · Diferencia en cambio de bancos · Movimientos por cuenta `/ledger-journal-categories` · Movimientos por cuenta agrupado por tercero `/ledger-journal-categories-grouped` · Libro diario `/ledger-journals` · Auxiliar por tercero `/subsidiary-ledger-by-client` · Balance de prueba `/general-ledger` · Balance de prueba por tercero · Libro mayor y balances |
| **Fiscales (4)** | Reporte detallado de impuestos `/detailed-tax-report` · Impuestos y retenciones `/taxes-and-retentions` · Comprobante de informe diario (POS) · **Formulario 350** (retención en la fuente) `/withholding-tax` |
| **Para trabajar (3)** | Exportar facturas · Informe contador · Informe de mandatos (ingresos para terceros) |
| **Información exógena (8)** | Formatos **1001, 1003, 1005, 1006, 1007, 1008, 1009, 2276**, configurables por formato y año en `/report/exogenous-config` |

**Comportamientos comunes que conviene replicar:**
- **Estado de resultados** y **Situación financiera**:
  - Filtros de periodo y **centro de costos**.
  - Vista detallada o resumida.
  - **Comparativo contra el mismo periodo del año anterior**, con % sobre ingresos (análisis vertical), variación absoluta y variación relativa.
  - Panel "Interpretando tu reporte".
- **Balance de prueba**:
  - Columnas de saldo inicial, movimientos y saldo final, cada una con débito y crédito.
  - Filtro de centro de costo.
  - Opciones "Cierre anual" y "Cuentas con saldos en cero".
  - Buscador de cuenta.
- **Flujo de efectivo**:
  - Usa el **método indirecto**: parte de la utilidad neta, ajusta por partidas no monetarias y luego por variaciones de capital de trabajo.
  - Actividades de operación, inversión y financiación.
  - Estructura **personalizable**: cada concepto se mapea a cuentas, y el reporte avisa si hay conceptos sin cuentas.
  - Comparativo contra el mes anterior.
- **Impuestos y retenciones**:
  - Pestañas Ventas y Compras.
  - Columnas: nombre, tipo, tarifa, base gravable, impuesto, base de devoluciones, impuesto en devoluciones y saldo.
- **Auxiliar por tercero** y **Movimientos por cuenta**: exigen elegir el tercero o la cuenta antes de generar.
- Todos los reportes se descargan en Excel o PDF y se pueden marcar como favoritos.

---

## 8. Configuración `/configuration`

| Bloque | Opciones (ruta) |
|---|---|
| **Empresa** | Empresa `/company/edit` · Usuarios · Mi perfil · **Centros de costos** `/cost-center` · **Monedas** `/currency` |
| **Facturación** | Términos de pago `/term` (contado, 8, 15, 30, 60 días o manual) · **Numeraciones** `/resolution` (preferida, electrónica, resolución DIAN, prefijo, siguiente número) · Configuración de documentos `/company/edit-document-info` (notas y términos por factura, cotización y remisión, e incluir el estado de cuenta en la factura) · Vendedores `/sellers` · Propinas `/additional-charges` |
| **Plantillas de impresión** | Por factura, cotización, transacción u orden de compra. Varias plantillas (general, clásica, estándar) y firma digitalizada |
| **Impuestos** | Impuestos `/tax` · Retenciones `/retention` · Configuración avanzada `/tax/settings` (varios impuestos por ítem o cuenta, y **retenciones asumidas**) |
| **Contabilidad** | Actualizar registros contables · Parametrización contable · Cierre contable de resultados · Configurar información exógena · Tipos de comprobantes contables · Numeraciones contables |
| **Notificaciones y correos** | Notificaciones de cobro · Plantillas de correo y WhatsApp · Preferencias de correo |
| **Inventario** | Campos adicionales · Variantes |
| **Integraciones** | `mi.alegra.com/integrations` |
| **Historial** | Historial de correos de facturas · Historial de operaciones (auditoría) |
| **Suscripción** | Planes y métodos de pago |

**Detalles de configuración:**
- **Empresa**:
  - Persona natural o jurídica, tipo y número de identificación, nombre y nombre comercial.
  - **Responsabilidad tributaria** (por ejemplo Responsable de IVA).
  - **Obligaciones tributarias del RUT, renglón 53**: gran contribuyente, autorretenedor, agente de retención de IVA, régimen simple o ninguna.
  - Municipio, dirección y contacto.
  - Sector y número de empleados.
  - **Moneda**, **precisión decimal** y separador decimal.
  - Estado de la facturación electrónica y del documento equivalente POS.
- **Impuestos**:
  - Tipos: **IVA, INC, ICUI, ICL y Otro**.
  - Campos: nombre, tarifa y descripción.
  - Indicador de "impuesto acreditable".
  - **4 cuentas por impuesto**: ventas, compras, devoluciones en ventas y devoluciones en compras.
  - Vienen precargados: IVA 19%, IVA 5%, IVA exento y IVA excluido.
- **Retenciones precargadas**: nombre, porcentaje, tipo, descripción y estado.

| Retención | % |
|---|---|
| Arrendamiento de bienes muebles | 4 |
| Arrendamiento de bienes raíces | 3,5 |
| Compras, declarante | 2,5 |
| Compras, no declarante | 3,5 |
| Honorarios y comisiones, persona natural | 10 |
| Honorarios y comisiones, persona jurídica | 11 |
| Servicios de aseo y vigilancia | 2 |
| Hoteles y restaurantes | 3,5 |
| Servicios en general, declarante | 4 |
| Servicios en general, no declarante | 6 |
| Transporte de carga | 1 |
| ReteICA | 0 (configurable) |
| ReteIVA | 15 (sobre el IVA) |

- **Monedas**:
  - Tasa de cambio manual o automática.
  - Histórico de tasas con su variación en pesos y en %.
  - Gráfica de la tasa en 1 semana, 1 mes o 1 año.
- **Centros de costo**: código, nombre, fecha de creación y descripción. Se usan en líneas de comprobante, documentos, bodegas y como filtro de reportes.
- **Contactos** (formulario avanzado):
  - Tipo cliente y/o proveedor.
  - Tipo y número de identificación, nombres y apellidos, municipio, dirección y código postal.
  - Correos y teléfonos.
  - Personas asociadas, con notificación de vencimientos.
  - **Información comercial**: plazo y límite de crédito, lista de precios, vendedor.
  - **Configuración contable**: cuenta de CxC y CxP propias del contacto.
  - Comentarios.
  - Enviar estado de cuenta.
  - Creación del contacto a partir de una foto o PDF del RUT (beta).

---

## 9. Cuenta, usuarios e integraciones (`mi.alegra.com`)

- **Menú de la cuenta**:
  - Empresa, Soluciones, Perfil.
  - Seguridad: acceso a la cuenta e historial de operaciones.
  - Usuarios y Roles.
  - Integraciones.
  - Suscripciones: resumen, consumo, métodos de pago, datos de facturación e historial.
  - **Habilitaciones DIAN**: facturación electrónica y documento equivalente POS electrónico.
  - Soportes legales y Portal de clientes.
- **Roles predeterminados**:
  - **Administrador**: acceso total y gestión de usuarios.
  - **Contador**: todo menos suscripciones y pagos.
  - **Asistente de ventas**: clientes, cotizaciones y pedidos.
  - Los roles personalizados con permisos granulares exigen el plan Plus.
- **Soluciones**: Contabilidad, Nómina electrónica (DIAN) y POS. También aparecen CRM, Portal de clientes y Alegra Ventas.
- **Integraciones**:
  - **API REST** con autenticación por **JWT** (nombre, plataforma, expiración) o por usuario y token. Tiene documentación pública.
  - **MCP de Alegra**: conecta la cuenta con Claude, ChatGPT, Perplexity y Cursor para consultar bancos, gastos y reportes en lenguaje natural.
  - **Ventas en línea**: Shopify, Mercado Libre y WooCommerce.
  - **Cobros**: Bold (link de pago y datáfono), **Bre-B**, ePayco, MercadoPago, Nequi, PayU y Wompi.
  - **Automatización**: facturación desde Google Sheets, carga masiva de imágenes, Handy (ventas en campo), SaludVitale y conectores de CRM, mercadeo, nómina/HR, compras e inventario.
- **Colaboración**:
  - Comentarios y @menciones en todos los documentos.
  - Recordatorios inteligentes.
  - **Mis tareas**: lista o tablero con estados Próximas, En progreso, Hecho y Archivadas.
  - **Bandeja de entrada** con avisos.

---

## 10. Comparación con lo que ya existe en `control_inventarios`

Se revisaron solo los nombres de modelos y endpoints (`backend/app/infrastructure/models/erp*.py`, `backend/app/api/v1/endpoints/erp*.py`, `plataforma_contable.py`). El estado indica si la pieza existe, no si está completa o probada.

| Capacidad Alegra | Estado en el repo | Observación |
|---|---|---|
| Plan de cuentas jerárquico (PUC/NIIF) | ✅ `ERPPlanCuenta` (código, naturaleza, nivel, padre, `norma`) | Falta la plantilla **PUC precargada**, importar catálogo propio y **saldos iniciales** como flujo |
| Comprobante contable con débito y crédito | ✅ `ERPComprobante` + `ERPComprobanteLinea` | **El tercero en la línea es texto libre**, no FK a `ERPTercero`. Eso bloquea el auxiliar por tercero, la exógena y los certificados |
| Tipos de comprobante + numeraciones contables | 🟡 `TipoComprobante` es un enum fijo; existe `ERPConsecutivo` | Alegra los tiene como catálogo editable (17 tipos, con prefijo y preferida) |
| Contabilización automática desde documentos | ✅ `ERPEventoContable` + `ERPReglaContable` (`/contabilidad/eventos`, `/reglas`) | Equivale a la *Parametrización contable*. Falta la jerarquía cuenta propia → cuenta por defecto (ítem, contacto, impuesto) y la **recontabilización de periodos abiertos** |
| Periodos y cierre | ✅ `ERPPeriodo` (cerrar y reabrir) | Falta el **cierre anual** (traslado de resultados, tipo CA) y el **cierre por tercero** |
| Centros de costo | ✅ `ERPCentroCosto` | Alegra los usa también como filtro de todos los estados financieros |
| Terceros con DV | ✅ `ERPTercero` + `/terceros/dv/{nit}` | Agregar las cuentas CxC y CxP por tercero |
| Impuestos | 🟡 `ERPTipoImpuesto` (2 cuentas) + `ERPReglaImpuesto` | Alegra usa **4 cuentas** (venta, compra, devolución venta, devolución compra), tipos IVA/INC/ICUI/ICL y la marca de acreditable |
| Retenciones | 🟡 Mencionadas en `erp.py`/`erp_nucleo.py`; no hay catálogo propio en el ERP | Crear un catálogo de retenciones (ReteFuente por concepto, ReteIVA, ReteICA por municipio) y las **retenciones asumidas** |
| Libro diario, mayor, balance de prueba, ER, ESF | ✅ endpoints `/contabilidad/*` | Agregar el **comparativo interanual**, el análisis vertical, el filtro por CC, la vista detallada o resumida, y Excel/PDF |
| Estado de flujo de efectivo (indirecto) | ❌ | Necesita un mapeo configurable de concepto a cuentas |
| Auxiliar por tercero / balance por tercero / movimientos por cuenta | 🟡 Existe el libro mayor | Depende de que el tercero sea FK en la línea |
| Facturación de venta + resolución DIAN | ✅ `ERPFacturaCliente`, `ERPResolucionFacturacion`, `facturacion_dian.py` | Faltan la nota débito, el tipo "tiquete" y la factura recurrente |
| Notas crédito | ✅ `ERPNotaCreditoCliente` | Agregar el **concepto DIAN** de la NC |
| Cotizaciones / remisiones | 🟡 Hay algo de cotización en `erp.py`; remisión no aparece | Flujo cotización → remisión → factura |
| Factura de compra + OC | ✅ `ERPFacturaProveedor`, `ERPOrdenCompra`, `/cxp/recepciones-por-facturar` | Faltan las líneas a **cuenta contable** (no solo a ítem) y la importación de **XML DIAN** |
| Documento soporte + nota de ajuste | ❌ | Es obligatorio en Colombia para compras a no obligados a facturar |
| Recibos de caja / comprobantes de egreso | 🟡 `ERPPago` (una factura por pago) | Alegra permite **un pago para varias facturas** y pagos directos a cuentas contables con retenciones |
| Bancos, movimientos y conciliación | ✅ `ERPCuentaBancaria`, `ERPMovimientoBancario`, `/tesoreria/conciliar` | Faltan las transferencias entre cuentas, el saldo inicial con asiento y el tipo tarjeta de crédito |
| Multimoneda / diferencia en cambio | 🟡 `ERPTasaCambio`, `moneda` y `tasa_cambio` en facturas | Falta el ajuste DTB y su reporte |
| Activos fijos y depreciación | ✅ `ERPActivoFijo`, `ERPDepreciacionActivo`, `/activos/{id}/schedule` | Verificar la contabilización de la creación del activo y de la depreciación mensual |
| Valor de inventario / costo promedio | ✅ `wms_contable.py` (puente WMS → contabilidad) | Agregar las cuentas de inventario y de costo por ítem |
| Cartera por edades (CxC/CxP) | ✅ `/cxc/aging`, `/cxp/resumen` | — |
| Formulario 350 / declaración de IVA | ❌ (hay `/tributacion/simular`) | Agregar las salidas oficiales |
| Información exógena (1001-2276) | ❌ | Configuración de conceptos por formato y año, más la exportación a Excel |
| Certificados de retención | ❌ | Depende del catálogo de retenciones y del tercero como FK |
| Auditoría / historial de operaciones | ✅ `ERPAuditoria`, `/auditoria` | — |
| Presupuestos, proyectos, costeo ABC, EPM, consolidación | ✅ (Alegra **no** los tiene) | Ventaja propia del proyecto |
| API pública / MCP | — | El backend ya expone API REST; un MCP sería un diferenciador |

### Prioridades sugeridas (de mayor a menor impacto contable)
1. **Tercero como FK en `ERPComprobanteLinea`**. Desbloquea el auxiliar por tercero, la exógena, los certificados y el cierre por tercero.
2. **Catálogo PUC precargado**, saldos iniciales y la jerarquía de cuentas por defecto: ítem → contacto → impuesto → parametrización general.
3. **Impuestos con 4 cuentas** y un catálogo de **retenciones** que incluya las retenciones asumidas.
4. **Recibos de caja y egresos multi-factura**, con pagos directos a cuenta contable.
5. **Documento soporte**, nota débito e importación de XML de compras.
6. **Tipos y numeraciones de comprobante editables**, y el cierre anual.
7. **Reportes**: comparativos y análisis vertical, flujo de efectivo indirecto, Formulario 350 y exógena.

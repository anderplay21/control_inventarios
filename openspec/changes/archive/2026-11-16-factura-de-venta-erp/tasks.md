# Tasks

Las pruebas de integración van en `backend/tests/integration/test_erp_ventas.py` (mismo estilo que `test_erp_contable.py`: PostgreSQL real, no SQLite) y las de lógica pura en `backend/tests/unit/`. Se corren en el contenedor con `docker exec ci_backend python -m pytest <ruta>`. Las pruebas de humo por HTTP crecen en `backend/tests/humo_erp.py`, y las de navegador van en `frontend/tools/probar_factura_venta_*.cjs`. Los escenarios citados salen de `specs/erp/`.

## 1. Esquema, catálogo y vendedor

- [x] 1.1 Verificar cómo está creado `EstadoFactura` en la base (enum nativo o texto) con `\dT+ estadofactura` en una empresa existente, y dejar la conclusión escrita en `design.md` (Risks); verificar que la nota indica el comando usado y su resultado.
- [x] 1.2 Modelos: columnas nuevas de `ERPFacturaCliente` (incluido `comprobante_id`, agregado al implementar 3.3) y `ERPLineaFacturaCliente`, `es_vendedor` en `ERPTercero`, el valor `CASTIGADA` en `EstadoFactura`, y las tablas de nota débito, historial/comentarios y adjuntos (decisión 4); verificar que `import app.main` arranca sin errores de mapeadores.
- [x] 1.3 Migración en `main._migrar_esquema` con `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS` y el alta del valor de enum según lo hallado en 1.1; verificar arrancando el backend dos veces contra la empresa `demo` y contra una base limpia, sin errores ni duplicados, y que las facturas existentes quedan con `tipo_documento = FACTURA_VENTA`.
- [x] 1.4 Catálogo `ERP/MEDIO_PAGO` en `CATALOGOS_REGISTRO` con efectivo, transferencia y tarjeta de base; verificar en `AdminCatalogos` que aparece y que `resolver_valor_catalogo` rechaza un valor inexistente.
- [x] 1.5 `es_vendedor` en los esquemas de crear, editar y respuesta de terceros, y `vendedor` como valor del filtro `tipo` en `erp_contable.py`; verificar con una prueba que un tercero marcado aparece en `GET /erp/terceros?tipo=vendedor` y uno sin marca no.
- [x] 1.6 Prueba de integración del grupo (`test_erp_ventas.py`): las columnas y tablas nuevas existen, la migración es idempotente y el filtro de vendedores funciona; verificar que pasa en el contenedor.

## 2. Reglas contables y permiso de castigo

- [x] 2.1 Eventos `VENTA_NOTA_DEBITO` y `CASTIGO_CARTERA` en `NATURALEZA_POR_EVENTO` (`erp_motor.py`) con los papeles de la decisión 6; verificar con una prueba que un asiento de cada evento cuadra y que el motor rechaza un papel no declarado.
- [x] 2.2 Reglas y cuenta de gasto del castigo en `erp_semilla.py`, idempotentes (la cuenta de gasto la confirma contabilidad antes de desplegar; ver Open Questions del diseño); verificar que `sembrar_empresa` sobre una empresa existente agrega lo nuevo y no pisa una regla editada a mano.
- [x] 2.3 Permiso `fin_castigar` en `permisos_perfil.py`, su guardia en `erp_permisos.py` y la fila en la matriz de permisos de Roles del frontend; verificar que un usuario sin el permiso recibe 403 en una ruta que lo exige y que un administrador pasa.
- [x] 2.4 Pruebas del grupo en `test_erp_ventas.py` para los escenarios de asiento de nota débito y castigo («Castigo con permiso», «Sin permiso»); verificar que pasan.

## 3. Emisión de la factura

- [x] 3.1 Función en `facturacion_dian.py` que devuelve la resolución vigente de una empresa por tipo de documento (`FACTURA_VENTA` o `POS`), sin importar nada del WMS; verificar con pruebas de unidad los casos vigente, vencida, agotada y sin resolución.
- [x] 3.2 Cálculo de línea y de totales en `core/erp_ventas.py` (subtotal, descuento, impuesto y total por línea y del documento); verificar con una prueba de unidad que el ejemplo del escenario «Línea calculada» da 270.000, 51.300 y 321.300 y que el total guardado coincide con el mostrado.
- [x] 3.3 Servicio de emisión: valida campos y líneas, toma el número con `tomar_numero` dentro de la transacción, calcula el CUFE con `codigo_unico`, vincula o crea el tercero cliente, guarda vendedor, centro de costo y notas, y asienta `VENTA_FACTURA` con la cartera por `total − retenciones`; verificar los escenarios «Factura de venta con resolución vigente», «Ticket con su propia numeración», «Sin resolución vigente», «Resolución agotada», «Sin líneas» y «Período contable cerrado».
- [x] 3.4 Router `erp_ventas.py` con `POST /erp/ingresos/facturas` y registro en `RUTAS` bajo el módulo `erp`, más la delegación del `POST /erp/cxc/facturas` viejo (ignora `numero`); verificar que ambas rutas crean una factura con número de la resolución y que el número enviado por el cliente se ignora.
- [x] 3.5 Cliente existente y nuevo: autocompletar por identificación y crear el tercero si no existe, con la validación de correo; verificar «Cliente existente», «Cliente nuevo» y «Correo con formato inválido».
- [x] 3.6 Pruebas del grupo en `test_erp_ventas.py` con todos los escenarios de `factura-venta-emision` salvo el pago al crear (que va en el grupo 4); verificar que pasan y que los consecutivos no se repiten con dos emisiones simultáneas.

## 4. Recaudo

- [x] 4.1 `registrar_recaudo` con bloqueo de la fila de la factura, rechazo de valor ≤ 0, sobrepago, factura anulada o en borrador, y cuenta bancaria obligatoria, y cálculo del saldo y estado; verificar «Pago total», «Abono parcial», «Pago mayor que el saldo», «Factura anulada», «Valor cero o negativo» y «Cuenta bancaria obligatoria».
- [x] 4.2 Que `POST /erp/pagos` use `registrar_recaudo`, con lo que se corrige su hueco actual; verificar con la prueba del sobrepago que ya no queda asiento por el monto completo y que los pagos de facturas de proveedor siguen funcionando.
- [x] 4.3 Endpoint de pago rápido `POST /erp/ingresos/facturas/{id}/pagos`; verificar «Asiento y vínculo» (comprobante de recaudo, pago en el detalle, saldo de cartera) y «Período cerrado».
- [x] 4.4 Pago durante la creación: la emisión llama al recaudo en la misma transacción; verificar «Factura de contado pagada» y «Falla el recaudo» (no queda la factura).
- [x] 4.5 Medio de pago validado contra el catálogo del paso 1.4; verificar «Lista configurable» agregando un medio y usándolo en un pago.

## 5. Consulta y matriz de acciones

- [x] 5.1 Función `acciones_permitidas` en `core/erp_ventas.py` con la matriz de la decisión 3; verificar con una prueba de unidad parametrizada que cada celda de la tabla de acciones por estado da el resultado esperado.
- [x] 5.2 `GET /erp/ingresos/facturas`: orden descendente, paginación con `X-Total-Count`, estado visible derivado («Por cobrar», «Cobrada», «Vencida») y filtros; verificar «Orden por defecto», «Por cobrar», «Cobrada», «Vencida sin tarea de fondo» y «Volumen grande» con 20.000 facturas de la siembra de carga.
- [x] 5.3 `GET /erp/ingresos/facturas/{id}` con líneas, totales, retenciones, pagos, notas, adjuntos, historial y las acciones permitidas; verificar «Historial trazable» y «Aplicar anticipo diferido».
- [x] 5.4 Guardia común que consulta la matriz antes de cada acción; verificar «Acción ejecutada sin estar permitida» llamando directo al endpoint de una acción prohibida.
- [x] 5.5 PDF de la factura con número, cliente, líneas, totales, resolución y CUFE; verificar «Descargar PDF» abriendo el archivo generado.
- [x] 5.6 Pruebas del grupo (`test_erp_ventas.py`) para `factura-venta-consulta`; verificar que pasan.

## 6. Ajustes de la factura

- [x] 6.1 Reverso y reemisión: editar, volver a borrador y anular reversan el asiento con `erp_motor.reversar` y reemiten uno nuevo; verificar «Factura libre», «Con pagos aplicados», «Ya transmitida a la DIAN» y «Anulación libre».
- [x] 6.2 Notas crédito (reutiliza `ERPNotaCreditoCliente`) y notas débito, cada una con su resolución, motivo obligatorio y asiento; verificar «Nota crédito parcial», «Nota débito», «Nota sobre factura cobrada» y «Motivo obligatorio».
- [x] 6.3 Retenciones por factura: cálculo sugerido con `erp_impuestos.calcular`, edición manual con marca y registro en el historial, y ajuste del saldo y del asiento; verificar «Prellenado», «Valor distinto al calculado» y «Con pagos o transmitida».
- [x] 6.4 Castigo de cartera: motivo obligatorio, estado `CASTIGADA`, asiento `CASTIGO_CARTERA`, usuario y fecha, con la guardia `fin_castigar`; verificar «Castigo con permiso», «Sin permiso», «Factura cobrada» y «Sin motivo».
- [x] 6.5 Clonar, adjuntar archivos y comentarios internos; verificar «Clonar» y «Adjuntar a una anulada» y que el archivo queda en `UPLOAD_DIR` y se descarga.
- [x] 6.6 Pruebas del grupo (`test_erp_ventas.py`) para todos los escenarios de `factura-venta-ajustes`; verificar que pasan y que el saldo de cartera del mayor coincide con la suma de saldos de las facturas tras una secuencia de pago, nota y castigo.

## 7. Factura asistida por IA

- [x] 7.1 Módulo `core/erp_ventas_ia.py` con el cliente reutilizado de TittanBot, modelo configurable, validación de tipo y tamaño de archivo, y la extracción a un borrador con el formato del formulario; verificar con una prueba que usa un motor de pruebas (como `_motor_de_pruebas` en reuniones) sin llamar a la red.
- [x] 7.2 Endpoint `POST /erp/ingresos/facturas/ia/extraer` con el permiso `asistente`, sin escribir en la base y cruzando el NIT con terceros sin crearlos; verificar «Formato permitido», «Formato no permitido», «Archivo demasiado grande», «Sin guardado automático», «Sin permiso» e «IA no configurada».
- [x] 7.3 Pruebas del grupo para «Extracción exitosa», «Datos que no detecta», «Archivo ilegible» y «Guardar aplica las validaciones normales»; verificar que pasan.

## 8. Frontend

- [x] 8.1 Sección «Ingresos» en `ERP_NAV_ITEMS` y `ERP_SECTIONS` de `Sidebar.tsx`, ruta `/erp/ingresos/factura-venta` en `App.tsx` con su permiso y los textos de navegación por el mismo mecanismo de `nav.cxc`; verificar con `probar_barra_modulos.cjs` que el menú aparece, y «Sin permiso» entrando a la ruta sin el permiso del ERP.
- [x] 8.2 Página `ERPFacturaVenta.tsx`: listado paginado con estado visible, «Agregar pago» en filas por cobrar y formulario de pago rápido; verificar «Valor precargado», «Fecha por omisión» y «Fila cobrada o anulada» en el navegador.
- [x] 8.3 Formulario de nueva factura: tipo de documento, selectores (moneda, centro de costo, vendedor, forma y medio de pago), cliente con autocompletado, líneas, totales en vivo, notas, firma, comentarios y pago al crear; verificar «Selectores con datos de la empresa», «Solo terceros vendedores», «Formulario sin bodega ni lista de precios» y «Agregar y quitar líneas».
- [x] 8.4 Detalle en panel con las acciones habilitadas según las `acciones_permitidas` que trae el servidor, incluidas las deshabilitadas con su motivo y «Aplicar anticipo» como «próximamente»; verificar «Factura cobrada» y «Factura anulada» en el navegador.
- [x] 8.5 Diálogos de nota crédito y débito, retenciones, castigo (con motivo) y volver a borrador/anular; verificar en el navegador que cada uno refleja el rechazo del servidor cuando no aplica.
- [x] 8.6 Botón del Agente de IA con carga de archivo, indicador de avance y relleno editable del formulario; verificar «Formato no permitido» y «Sin permiso» en el navegador y que con un archivo de prueba el formulario se completa.
- [x] 8.7 `ERPTerceros.tsx` con el casillero «Vendedor»; `ERPCxC.tsx` y `ERPFacturacion.tsx` pierden el diálogo de creación y su botón lleva a la pantalla nueva; verificar sin errores de `tsc` y que ningún botón de creación vieja queda en las dos pantallas.
- [x] 8.8 Recorrido completo con `probar_factura_venta_flujo.cjs`: crear factura, abonar, aplicar una nota, retener y cerrar la última con castigo; verificar que el script termina sin errores y deja las capturas del recorrido.

## 9. Integración y documentación

- [x] 9.1 Servidor solo del ERP: con `MODULOS_SERVIDOR=erp` las rutas `/erp/ingresos/*` existen y los archivos de WMS y POS no se importan, añadido a `tests/unit/test_modulos_servidor.py`; verificar que pasa y que `modulos_servidor.DEPENDENCIAS` no cambió por esta funcionalidad.
- [x] 9.2 Extender `backend/tests/humo_erp.py` con el recorrido factura → pago → nota → castigo por HTTP, y comprobar que POS, WMS 3PL y reencauche siguen facturando con sus resoluciones; verificar que el humo termina en verde y que sus facturas siguen apareciendo en el listado nuevo.
- [x] 9.3 Documentar en el `README.md` la sección «Ingresos · Factura de venta» (menú, tipos de documento, matriz de acciones, permisos incluido `fin_castigar`, IA) y en `.env.example` las variables nuevas si las hay; verificar que los pasos documentados se reproducen tal cual en una empresa de prueba.
- [x] 9.4 Checklist de despliegue: confirmar con contabilidad la cuenta del castigo, el tamaño máximo del archivo de IA y el tratamiento del saldo a favor (Open Questions del diseño) y dejar las respuestas en `design.md`; verificar que la sección ya no tiene preguntas sin respuesta.

## Workflow follow-up

- Hacer commit en `feature_modulo_erp` por grupo, separado de los cambios de `wms_3pl` y dependencias.
- Archivar el cambio cuando termine la revisión del equipo, y luego sincronizar las specs a `openspec/specs/`.

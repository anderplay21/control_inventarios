/**
 * Recorrido completo de ERP · Ingresos · Factura de venta en un navegador real:
 * menú, formulario con totales en vivo, emisión, abono desde el listado,
 * nota crédito, retenciones, castigo de la última factura, botones según el
 * estado y que CxC y Facturación ya no crean facturas por su cuenta.
 *
 * Necesita una empresa con empresa ERP, resolución de factura y de nota crédito
 * vigentes, una cuenta bancaria activa y un tercero vendedor.
 *
 *   APP=... GATEWAY=... EMPRESA=desarrollo USUARIO=admin CLAVE=... node probar_factura_venta_flujo.cjs
 */
const { chromium } = require('playwright-core');

const APP = process.env.APP || 'http://localhost:5173';
const GATEWAY = process.env.GATEWAY || '';
const EMPRESA = process.env.EMPRESA || 'desarrollo';
const USUARIO = process.env.USUARIO || 'admin';
const CLAVE = process.env.CLAVE;
const SALIDA = process.env.SALIDA || '/salida';
if (!CLAVE) { console.error('Falta CLAVE'); process.exit(1); }

let fallos = 0;
const comprobar = (c, t) => { if (!c) fallos++; console.log(`${c ? 'ok ' : 'X  '} ${t}`); };
const foto = async (p, nombre) => p.screenshot({ path: `${SALIDA}/factura_venta_${nombre}.png`, fullPage: true });

(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/chromium-browser',
    args: ['--no-sandbox', ...(GATEWAY ? [`--host-resolver-rules=MAP localhost ${GATEWAY}`] : [])] });
  const p = await (await b.newContext({ viewport: { width: 1366, height: 900 }, locale: 'es-CO' })).newPage();
  const errores = [];
  p.on('pageerror', e => errores.push(String(e.message).slice(0, 160)));
  const respuestas500 = [];
  p.on('response', r => { if (r.status() >= 500) respuestas500.push(`${r.status()} ${r.url()}`); });

  // ── Entrar ──
  await p.goto(APP + '/', { waitUntil: 'networkidle', timeout: 60000 });
  // La primera pantalla pide el código de la empresa; su campo no siempre queda
  // asociado a la etiqueta, así que se reconoce por la ruta.
  if (/\/empresa/.test(p.url())) {
    await p.locator('input:visible').first().fill(EMPRESA);
    await p.getByRole('button', { name: /Continuar/i }).click();
  }
  const clave = p.locator('input[type="password"]');
  await clave.waitFor({ timeout: 30000 });
  await p.locator('input:not([type="password"]):visible').first().fill(USUARIO);
  await clave.fill(CLAVE);
  await p.getByRole('button', { name: /Ingresar/i }).first().click();
  await clave.waitFor({ state: 'detached', timeout: 60000 });

  // ── 8.1 Menú Ingresos ──
  await p.goto(APP + '/erp', { waitUntil: 'networkidle', timeout: 60000 });
  comprobar(await p.getByText('Ingresos', { exact: true }).count() > 0, 'el menú del ERP muestra la sección «Ingresos»');
  await p.getByText('Factura de venta', { exact: true }).first().click();
  await p.waitForURL(/ingresos\/factura-venta/, { timeout: 20000 });
  await p.getByRole('button', { name: 'Nueva factura de venta' }).waitFor({ timeout: 20000 }).catch(() => {});
  comprobar(await p.getByRole('button', { name: 'Nueva factura de venta' }).isVisible(), 'la vista inicial ofrece crear una factura');
  await foto(p, '1_listado');

  // ── 8.3 Formulario ──
  const crear = async (cliente, precio) => {
    await p.getByRole('button', { name: 'Nueva factura de venta' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.locator('[data-prueba="cliente-nombre"]').fill(cliente);
    const linea = dlg.locator('[data-prueba="linea"]').first();
    const campos = linea.locator('input');
    await campos.nth(0).fill('SRV-1');
    await campos.nth(1).fill('Servicio de prueba');
    await campos.nth(2).fill('3');
    await campos.nth(3).fill(String(precio));
    await campos.nth(4).fill('10');
    return dlg;
  };
  let dlg = await crear('Cliente Navegador SAS', 100000);
  comprobar((await dlg.locator('[data-prueba="total-linea"]').first().innerText()).includes('270.000'),
    'la línea calcula su total en vivo (3 × 100.000 − 10 %)');
  await dlg.locator('[data-prueba="agregar-linea"]').click();
  comprobar(await dlg.locator('[data-prueba="linea"]').count() === 2, 'agregar línea suma una fila');
  await dlg.locator('[data-prueba="linea"]').nth(1).getByRole('button').click();
  comprobar(await dlg.locator('[data-prueba="linea"]').count() === 1, 'quitar línea la elimina');
  comprobar(await dlg.getByLabel(/Bodega/).count() === 0 && await dlg.getByLabel(/Lista de precios/).count() === 0,
    'el formulario no pide bodega ni lista de precios');
  await dlg.getByLabel('Correo electrónico').fill('correo-malo');
  comprobar(await dlg.locator('[data-prueba="guardar-factura"]').isDisabled(), 'un correo inválido impide guardar');
  await dlg.getByLabel('Correo electrónico').fill('pagos@navegador.co');
  const ia = dlg.locator('[data-prueba="boton-ia"]');
  comprobar(await ia.count() <= 1, `botón del Agente de IA: ${await ia.count() ? (await ia.isDisabled() ? 'visible y deshabilitado (sin configurar)' : 'disponible') : 'oculto por permiso'}`);
  await foto(p, '2_formulario');
  await dlg.locator('[data-prueba="guardar-factura"]').click();
  await p.locator('[data-prueba="estado-detalle"]').waitFor({ timeout: 20000 });
  comprobar((await p.locator('[data-prueba="estado-detalle"]').innerText()) === 'Por cobrar', 'la factura queda emitida y por cobrar');
  await foto(p, '3_detalle_emitida');

  // ── 8.4 Detalle: acciones según el estado ──
  const accion = clave => p.locator(`[data-prueba="accion-${clave}"]`);
  comprobar(await accion('aplicar_anticipo').isDisabled(), '«Aplicar anticipo» deshabilitada (próximamente)');
  comprobar(await accion('anular').isEnabled(), 'sin pagos se puede anular');

  // ── 8.2 Abono desde el detalle ──
  await accion('agregar_pago').click();
  let pago = p.getByRole('dialog', { name: /Agregar pago/ });
  const valorInicial = await pago.locator('[data-prueba="pago-valor"]').inputValue();
  comprobar(Number(valorInicial) === 270000, `el valor viene con el saldo (3 × 100.000 − 10 %, sin IVA) (${valorInicial})`);
  comprobar((await pago.locator('[data-prueba="pago-fecha"]').inputValue()) === new Date().toISOString().slice(0, 10), 'la fecha viene con hoy');
  await pago.locator('[data-prueba="pago-valor"]').fill('100000');
  await pago.getByLabel('Cuenta bancaria').click();
  await p.getByRole('option').first().click();
  await pago.getByRole('button', { name: 'Agregar pago' }).click();
  await p.waitForTimeout(1500);
  comprobar(await accion('anular').isDisabled(), 'con un pago ya no se puede anular');

  // ── 8.5 Nota crédito ──
  await accion('nota_credito').click();
  const nota = p.getByRole('dialog', { name: /nota crédito/ });
  await nota.getByLabel('Valor (sin IVA)').fill('20000');
  await nota.getByLabel('Motivo').fill('Rebaja por prueba de navegador');
  await nota.getByRole('button', { name: 'Aplicar' }).click();
  await p.waitForTimeout(1500);
  comprobar(await p.getByText(/Crédito .* Rebaja por prueba de navegador/).count() > 0, 'la nota aparece en el detalle');
  await foto(p, '4_detalle_con_pago_y_nota');
  await p.getByRole('button').filter({ has: p.locator('svg[data-testid="CloseIcon"]') }).first().click();

  // ── 8.2 Fila cobrada no ofrece pago; fila por cobrar sí ──
  await p.waitForTimeout(800);
  const filas = p.locator('[data-prueba="fila-factura"]');
  comprobar(await filas.first().getByRole('button', { name: 'Agregar pago' }).count() === 1, 'la fila por cobrar ofrece «Agregar pago»');

  // ── 8.5 Retenciones y anulación sobre una factura libre ──
  dlg = await crear('Cliente Anulado SAS', 80000);
  await dlg.locator('[data-prueba="guardar-factura"]').click();
  await p.locator('[data-prueba="estado-detalle"]').waitFor({ timeout: 20000 });
  await accion('editar_retenciones').click();
  const ret = p.getByRole('dialog', { name: 'Editar retenciones' });
  await ret.getByLabel('Retención en la fuente').fill('5000');
  await ret.getByRole('button', { name: 'Guardar' }).click();
  await p.waitForTimeout(1500);
  comprobar(await p.getByText('Retenciones modificadas a mano').count() > 0, 'las retenciones editadas quedan marcadas');
  await accion('anular').click();
  await p.locator('[data-prueba="motivo"]').fill('Prueba de anulación');
  await p.locator('[data-prueba="confirmar-motivo"]').click();
  await p.waitForTimeout(1500);
  comprobar((await p.locator('[data-prueba="estado-detalle"]').innerText()) === 'Anulada', 'la factura queda anulada');
  comprobar(await accion('editar').isDisabled() && await accion('nota_credito').isDisabled()
    && await accion('agregar_pago').isDisabled(), 'anulada: sin edición, notas ni pagos');
  comprobar(await accion('descargar_pdf').isEnabled() && await accion('clonar').isEnabled(),
    'anulada: se puede descargar y clonar');
  await foto(p, '5_anulada');
  await p.getByRole('button').filter({ has: p.locator('svg[data-testid="CloseIcon"]') }).first().click();

  // ── 8.8 Segunda factura: castigo ──
  dlg = await crear('Cliente Incobrable SAS', 50000);
  await dlg.locator('[data-prueba="guardar-factura"]').click();
  await p.locator('[data-prueba="estado-detalle"]').waitFor({ timeout: 20000 });
  await accion('cerrar_sin_pago').click();
  await p.locator('[data-prueba="motivo"]').fill('Cliente liquidado');
  await p.locator('[data-prueba="confirmar-motivo"]').click();
  await p.waitForTimeout(1500);
  comprobar((await p.locator('[data-prueba="estado-detalle"]').innerText()) === 'Castigada', 'la factura castigada cambia de estado');
  comprobar(await accion('agregar_pago').isDisabled() && await accion('editar').isDisabled(),
    'castigada: sin pagos ni edición');
  await foto(p, '5_castigada');

  // ── 8.7 CxC y Facturación llevan a la pantalla nueva ──
  for (const ruta of ['/erp/cxc', '/erp/facturacion']) {
    await p.goto(APP + ruta, { waitUntil: 'networkidle', timeout: 60000 });
    await p.getByRole('button', { name: /Nueva Factura/i }).first().click();
    await p.waitForTimeout(800);
    comprobar(/ingresos\/factura-venta/.test(p.url()) && await p.getByRole('dialog').count() === 0,
      `${ruta}: «Nueva factura» lleva a Factura de venta, sin diálogo propio`);
  }

  comprobar(errores.length === 0, `sin errores de JavaScript (${errores.join(' | ')})`);
  comprobar(respuestas500.length === 0, `sin respuestas 500 (${respuestas500.join(' | ')})`);
  await b.close();
  console.log(fallos ? `\n${fallos} comprobaciones fallaron` : '\nTodo en orden');
  process.exit(fallos ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

/**
 * Factura de venta asistida por IA, en un navegador real y contra el proveedor
 * configurado (gasta una llamada al modelo por corrida):
 *  - el botón «Agente de IA» está disponible,
 *  - un PDF de factura llena el formulario y los campos siguen editables,
 *  - leer no guarda ninguna factura,
 *  - un archivo que no es lo que dice ser se rechaza con un mensaje.
 *
 *   APP=... GATEWAY=... CLAVE=... MUESTRA=/muestras/factura_muestra.pdf \
 *   FALSO=/muestras/no_es_imagen.png node probar_factura_venta_ia.cjs
 */
const { chromium } = require('playwright-core');

const APP = process.env.APP || 'http://localhost:5173';
const GATEWAY = process.env.GATEWAY || '';
const EMPRESA = process.env.EMPRESA || 'desarrollo';
const USUARIO = process.env.USUARIO || 'admin';
const CLAVE = process.env.CLAVE;
const SALIDA = process.env.SALIDA || '/salida';
const MUESTRA = process.env.MUESTRA || '/muestras/factura_muestra.pdf';
const FALSO = process.env.FALSO || '/muestras/no_es_imagen.png';
if (!CLAVE) { console.error('Falta CLAVE'); process.exit(1); }

let fallos = 0;
const comprobar = (c, t) => { if (!c) fallos++; console.log(`${c ? 'ok ' : 'X  '} ${t}`); };
const foto = async (p, nombre) => p.screenshot({ path: `${SALIDA}/factura_ia_${nombre}.png`, fullPage: true });

(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/chromium-browser',
    args: ['--no-sandbox', ...(GATEWAY ? [`--host-resolver-rules=MAP localhost ${GATEWAY}`] : [])] });
  const p = await (await b.newContext({ viewport: { width: 1366, height: 900 }, locale: 'es-CO' })).newPage();
  const errores = [];
  p.on('pageerror', e => errores.push(String(e.message).slice(0, 160)));
  const creaciones = [];
  p.on('request', r => { if (r.method() === 'POST' && /\/erp\/ingresos\/facturas$/.test(r.url())) creaciones.push(r.url()); });

  // ── Entrar ──
  await p.goto(APP + '/', { waitUntil: 'networkidle', timeout: 60000 });
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

  await p.goto(APP + '/erp/ingresos/factura-venta', { waitUntil: 'networkidle', timeout: 60000 });
  await p.getByRole('button', { name: 'Nueva factura de venta' }).click();
  const dlg = p.getByRole('dialog');
  const boton = dlg.locator('[data-prueba="boton-ia"]');

  // Con SIN_PERMISO=1 se entra con un usuario cuyo perfil no tiene «asistente»:
  // el formulario abre igual, pero el botón no aparece.
  if (process.env.SIN_PERMISO) {
    await dlg.locator('[data-prueba="cliente-nombre"]').waitFor({ timeout: 20000 });
    await p.waitForResponse(r => /\/ia\/estado$/.test(r.url()), { timeout: 5000 }).catch(() => {});
    await p.waitForTimeout(1000);
    comprobar(await dlg.locator('[data-prueba="guardar-factura"]').isVisible(), 'sin «asistente» el formulario abre');
    comprobar(await boton.count() === 0, 'sin «asistente» el botón «Agente de IA» no aparece');
    await foto(p, '0_sin_permiso');
    comprobar(errores.length === 0, `sin errores de JavaScript (${errores.join(' | ')})`);
    await b.close();
    console.log(fallos ? `\n${fallos} comprobaciones fallaron` : '\nTodo en orden');
    process.exit(fallos ? 1 : 0);
  }
  await boton.waitFor({ timeout: 20000 });
  comprobar(await boton.isEnabled(), 'el botón «Agente de IA» está disponible');

  // ── Formato no permitido ──
  const entrada = dlg.locator('input[type="file"][accept=".pdf,.jpg,.jpeg,.png"]');
  await entrada.setInputFiles(FALSO);
  const aviso = p.getByText(/formato|no corresponde|no es/i).first();
  await aviso.waitFor({ timeout: 20000 }).catch(() => {});
  comprobar(await aviso.isVisible(), `un archivo falso se rechaza con mensaje (${await aviso.isVisible() ? await aviso.innerText() : 'sin mensaje'})`);

  // ── Leer la factura de muestra ──
  const inicio = Date.now();
  const respuesta = p.waitForResponse(r => /\/ia\/extraer$/.test(r.url()), { timeout: 180000 });
  await entrada.setInputFiles(MUESTRA);
  const r = await respuesta;
  const cuerpo = await r.json().catch(() => ({}));
  console.log(`    respuesta ${r.status()} en ${((Date.now() - inicio) / 1000).toFixed(1)} s`);
  comprobar(r.status() === 200, `la extracción responde 200 (${r.status()} ${r.status() !== 200 ? JSON.stringify(cuerpo).slice(0, 200) : ''})`);
  await p.waitForTimeout(800);
  await foto(p, '1_formulario_lleno');

  const nombre = await dlg.locator('[data-prueba="cliente-nombre"]').inputValue();
  comprobar(/Andina/i.test(nombre), `toma al COMPRADOR como cliente, no al vendedor («${nombre}»)`);
  const ident = await dlg.locator('[data-prueba="cliente-identificacion"]').inputValue();
  comprobar(ident.replace(/\D/g, '').startsWith('900123456'), `identificación del cliente (${ident})`);
  const lineas = dlg.locator('[data-prueba="linea"]');
  comprobar(await lineas.count() === 2, `dos líneas leídas (${await lineas.count()})`);
  const primera = lineas.first().locator('input');
  const desc = await primera.nth(1).inputValue();
  comprobar(/Tornillo/i.test(desc), `descripción de la primera línea («${desc}»)`);
  comprobar(Number(await primera.nth(2).inputValue()) === 4 && Number(await primera.nth(3).inputValue()) === 85000,
    'cantidad y precio de la primera línea');
  if (cuerpo.advertencias?.length) console.log('    advertencias: ' + cuerpo.advertencias.join(' | '));

  // Los campos siguen editables y nada se guardó.
  await primera.nth(2).fill('5');
  comprobar(await primera.nth(2).inputValue() === '5', 'los campos leídos se pueden editar');
  comprobar(await dlg.locator('[data-prueba="guardar-factura"]').isVisible(), 'el formulario sigue abierto esperando revisión');
  comprobar(creaciones.length === 0, 'leer el archivo no crea ninguna factura');

  comprobar(errores.length === 0, `sin errores de JavaScript (${errores.join(' | ')})`);
  await b.close();
  console.log(fallos ? `\n${fallos} comprobaciones fallaron` : '\nTodo en orden');
  process.exit(fallos ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

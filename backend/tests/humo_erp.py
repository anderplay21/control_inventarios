"""Prueba de humo del ERP contable, contra la app entera y por HTTP interno.

Va por la aplicación —no llamando a las funciones— porque lo que se quiere
comprobar acá es justamente lo que las pruebas de unidad no ven: que las
dependencias de permisos estén bien puestas, que los esquemas de Pydantic acepten
lo que la pantalla envía y que las rutas existan con el nombre que el frontend
usa.

La autenticación se sustituye por un usuario administrador de mentira. Es lo
único que se simula; todo lo demás —base de datos, motor contable, reglas— es el
de verdad.
"""
import asyncio
import os
import sys

os.environ.setdefault("SQLALCHEMY_ECHO", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import text

import app.main as m
from app.core.database import AsyncSessionLocal, get_db
from app.core.dependencies import get_current_user, require_admin
from app.infrastructure.models.usuario import Usuario

ESQUEMA = os.environ.get("ESQUEMA", "public")


class UsuarioFalso:
    id = 1
    username = "prueba"
    nombre = "Prueba de humo"
    rol_id = 1
    # `rol` es la columna de texto que usa el resto de la plataforma para
    # reconocer al administrador; sin ella el guardia de Finanzas no lo exime.
    rol = "ADMINISTRADOR"
    email = "prueba@local"
    activo = True


async def _db():
    async with AsyncSessionLocal(info={"esquema": ESQUEMA}) as s:
        await s.execute(text('SET search_path TO "%s"' % ESQUEMA))
        # Como el get_db de verdad: confirma al final. Los endpoints que no hacen
        # commit propio (WMS) perderían lo escrito si la prueba no lo hiciera.
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


# El token es de verdad y va firmado con la clave de la instancia: hay una
# dependencia global que exige sesión en toda la API, y saltársela dejaría sin
# probar justamente el camino por el que pasan las peticiones reales.
from app.core.sesiones import token_de_herramienta as create_access_token
TOKEN = create_access_token(subject=1, esquema=ESQUEMA, usuario="prueba")

m.app.dependency_overrides[get_current_user] = lambda: UsuarioFalso()
m.app.dependency_overrides[require_admin] = lambda: UsuarioFalso()
m.app.dependency_overrides[get_db] = _db

API = "/api/v1"
fallos = []


def revisar(nombre, r, esperado=(200, 201)):
    ok = r.status_code in esperado
    print(("  ok  " if ok else "FALLA ") + nombre + "  ->  " + str(r.status_code))
    if not ok:
        fallos.append(nombre)
        print("        " + r.text[:300])
    return r


async def principal():
    transporte = httpx.ASGITransport(app=m.app)
    async with httpx.AsyncClient(
        transport=transporte, base_url="http://local",
        headers={"Authorization": "Bearer " + TOKEN},
    ) as c:

        # ── Una empresa sobre la que trabajar ──
        r = await c.get(API + "/erp/empresas")
        revisar("GET  /erp/empresas", r)
        empresas = r.json() if r.status_code == 200 else []
        if empresas:
            eid = empresas[0]["id"]
            print("      usando la empresa existente %s" % eid)
        else:
            r = await c.post(API + "/erp/empresas", json={
                "nit": "900000001", "razon_social": "Empresa de humo",
                "pais": "Colombia", "moneda_base": "COP"})
            revisar("POST /erp/empresas", r)
            if r.status_code not in (200, 201):
                return
            eid = r.json()["id"]

        # ── Sembrar el núcleo ──
        revisar("POST /erp/contabilidad/sembrar",
                await c.post(API + "/erp/contabilidad/sembrar", params={"empresa_id": eid}))

        # ── Terceros ──
        revisar("GET  /erp/terceros/dv/890903938",
                await c.get(API + "/erp/terceros/dv/890903938"))
        r = await c.post(API + "/erp/terceros", json={
            "empresa_id": eid, "numero_identificacion": "800197268",
            "razon_social": "Tercero de humo", "es_cliente": True,
            "codigo_municipio": "05001", "responsabilidades": ["O-13"]})
        revisar("POST /erp/terceros", r, (200, 201, 409))
        revisar("GET  /erp/terceros",
                await c.get(API + "/erp/terceros", params={"empresa_id": eid}))

        # ── Períodos ──
        revisar("GET  /erp/contabilidad/periodos",
                await c.get(API + "/erp/contabilidad/periodos", params={"empresa_id": eid}))

        # ── Reglas ──
        rr = revisar("GET  /erp/contabilidad/reglas",
                     await c.get(API + "/erp/contabilidad/reglas", params={"empresa_id": eid}))
        if rr.status_code == 200:
            n = sum(len(e["reglas"]) for e in rr.json().get("eventos", []))
            print("      %d reglas contables sembradas" % n)
            if n == 0:
                fallos.append("la siembra no dejó reglas contables")

        revisar("GET  /erp/tributacion/reglas",
                await c.get(API + "/erp/tributacion/reglas",
                            params={"empresa_id": eid, "vigentes": False}))
        revisar("GET  /erp/tributacion/parametros",
                await c.get(API + "/erp/tributacion/parametros"))
        revisar("POST /erp/tributacion/simular",
                await c.post(API + "/erp/tributacion/simular",
                             params={"empresa_id": eid, "base": 5000000,
                                     "concepto": "Compras generales"}))

        # ── Lo que la facturación necesita: resoluciones y una cuenta bancaria ──
        import datetime
        hoy = datetime.date.today().isoformat()
        en_un_anio = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        r = revisar("GET  /erp/resoluciones",
                    await c.get(API + "/erp/resoluciones", params={"empresa_id": eid}))
        vigentes = {x["tipo_documento"] for x in (r.json() if r.status_code == 200 else [])
                    if x.get("activa") and x.get("vigencia_hasta", "") >= hoy}
        for tipo, prefijo in (("FACTURA_VENTA", "HFV"), ("NOTA_CREDITO", "HNC")):
            if tipo not in vigentes:
                revisar("POST /erp/resoluciones (%s)" % tipo, await c.post(API + "/erp/resoluciones", json={
                    "empresa_id": eid, "tipo_documento": tipo, "numero_resolucion": "HUMO-" + prefijo,
                    "fecha_resolucion": hoy, "prefijo": prefijo, "desde": 1, "hasta": 100000,
                    "vigencia_desde": hoy, "vigencia_hasta": en_un_anio, "clave_tecnica": "humo"}))
        r = revisar("GET  /erp/tesoreria/cuentas", await c.get(API + "/erp/tesoreria/cuentas"))
        cuentas = [x for x in (r.json() if r.status_code == 200 else []) if x.get("activo")]
        if cuentas:
            cuenta_id = cuentas[0]["id"]
        else:
            b = revisar("POST /erp/tesoreria/bancos",
                        await c.post(API + "/erp/tesoreria/bancos", json={"nombre": "Banco de humo"}))
            k = revisar("POST /erp/tesoreria/cuentas", await c.post(API + "/erp/tesoreria/cuentas", json={
                "banco_id": b.json()["id"], "numero": "HUMO-001", "tipo": "CORRIENTE", "empresa_id": eid}))
            cuenta_id = k.json()["id"]

        # ── Ruta vieja de cartera: delega en la emisión nueva e ignora el número ──
        r = await c.post(API + "/erp/cxc/facturas", json={
            "empresa_id": eid, "numero": "NUMERO-DIGITADO",
            "cliente_nombre": "Cliente de humo", "fecha": hoy,
            "fecha_vencimiento": hoy, "lineas": [
                {"descripcion": "Servicio", "cantidad": 1,
                 "precio_unitario": 1000000, "descuento_pct": 0}]})
        revisar("POST /erp/cxc/facturas (genera asiento)", r)
        if r.status_code == 201 and r.json()["numero"] == "NUMERO-DIGITADO":
            fallos.append("la ruta vieja guardó el número digitado")

        # ── Ingresos · Factura de venta: emitir, cobrar, corregir, castigar ──
        r = revisar("POST /erp/ingresos/facturas", await c.post(API + "/erp/ingresos/facturas", json={
            "empresa_id": eid, "cliente_identificacion": "800197268", "cliente_nombre": "Tercero de humo",
            "cliente_email": "humo@local.co", "fecha": hoy,
            "lineas": [{"referencia": "H-1", "descripcion": "Servicio de humo", "cantidad": 2,
                        "precio_unitario": 500000, "descuento_pct": 10}]}))
        if r.status_code == 201:
            fid = r.json()["id"]
            print("      factura %s por %s" % (r.json()["numero"], r.json()["total"]))
            rl = revisar("GET  /erp/ingresos/facturas",
                         await c.get(API + "/erp/ingresos/facturas", params={"limite": 5}))
            if rl.status_code == 200 and fid not in [x["id"] for x in rl.json()]:
                fallos.append("la factura nueva no encabeza el listado")
            revisar("POST /erp/ingresos/facturas/{id}/pagos", await c.post(
                API + "/erp/ingresos/facturas/%s/pagos" % fid,
                json={"monto": 300000, "cuenta_bancaria_id": cuenta_id, "fecha": hoy}))
            revisar("POST /erp/ingresos/facturas/{id}/pagos (sobrepago rechazado)", await c.post(
                API + "/erp/ingresos/facturas/%s/pagos" % fid,
                json={"monto": 99999999, "cuenta_bancaria_id": cuenta_id, "fecha": hoy}), (422,))
            revisar("POST /erp/ingresos/facturas/{id}/notas-credito", await c.post(
                API + "/erp/ingresos/facturas/%s/notas-credito" % fid,
                json={"valor": 100000, "motivo": "Rebaja de humo"}), (201,))
            revisar("POST /erp/ingresos/facturas/{id}/anular (con pagos: rechazado)", await c.post(
                API + "/erp/ingresos/facturas/%s/anular" % fid, json={"motivo": "humo"}), (409,))
            d = revisar("POST /erp/ingresos/facturas/{id}/castigar", await c.post(
                API + "/erp/ingresos/facturas/%s/castigar" % fid, json={"motivo": "Castigo de humo"}))
            if d.status_code == 200:
                det = d.json()
                print("      estado %s · saldo %s · %d eventos en el historial"
                      % (det["estado"], det["saldo"], len(det["historial"])))
                if det["estado"] != "CASTIGADA" or float(det["saldo"]) != 0:
                    fallos.append("el castigo no dejó la factura castigada en cero")
            revisar("GET  /erp/ingresos/facturas/{id}/pdf",
                    await c.get(API + "/erp/ingresos/facturas/%s/pdf" % fid))

        # ── Ingresos · Pagos recibidos: anticipo → factura → aplicar → pagar el
        #    resto → anular → otro ingreso con retención ──
        PR = API + "/erp/ingresos/pagos-recibidos"

        async def saldo_banco():
            rc = await c.get(API + "/erp/tesoreria/cuentas")
            fila = next((x for x in rc.json() if x["id"] == cuenta_id), {}) if rc.status_code == 200 else {}
            return float(fila.get("saldo_contable") or 0)

        async def saldo_cuenta(codigo):
            """Saldo débito − crédito de una cuenta del PUC según el balance de prueba."""
            rb = await c.get(API + "/erp/contabilidad/balance-comprobacion",
                             params={"empresa_id": eid, "hasta": hoy})
            fila = next((x for x in rb.json()["filas"] if x["codigo"] == codigo), {}) if rb.status_code == 200 else {}
            return float(fila.get("saldo_final_debito") or 0) - float(fila.get("saldo_final_credito") or 0)

        rc = revisar("GET  /pagos-recibidos/clientes", await c.get(PR + "/clientes", params={"buscar": "800197268"}))
        tid = rc.json()[0]["id"] if rc.status_code == 200 and rc.json() else None
        if tid is None:
            fallos.append("no se encontró el tercero de humo para Pagos recibidos")
        else:
            banco_antes = await saldo_banco()
            anticipos_antes, cartera_antes = await saldo_cuenta("280505"), await saldo_cuenta("130505")
            ra = revisar("POST /pagos-recibidos (anticipo)", await c.post(PR, json={
                "tipo_ingreso": "ANTICIPO", "tercero_id": tid, "cuenta_bancaria_id": cuenta_id,
                "fecha": hoy, "valor": 400000, "notas_adicionales": "Anticipo de humo"}), (201,))
            rf = revisar("POST /erp/ingresos/facturas (para el anticipo)", await c.post(
                API + "/erp/ingresos/facturas", json={
                    "empresa_id": eid, "cliente_identificacion": "800197268",
                    "cliente_nombre": "Tercero de humo", "fecha": hoy,
                    "lineas": [{"descripcion": "Servicio anticipado", "cantidad": 1,
                                "precio_unitario": 1000000}]}), (201,))
            if ra.status_code == 201 and rf.status_code == 201:
                aid, fid2 = ra.json()["id"], rf.json()["id"]
                det = (await c.get(API + "/erp/ingresos/facturas/%s" % fid2)).json()
                if not det["acciones"]["aplicar_anticipo"]["permitida"]:
                    fallos.append("«Aplicar anticipo» no se habilitó con saldo a favor")
                revisar("POST /erp/ingresos/facturas/{id}/anticipos", await c.post(
                    API + "/erp/ingresos/facturas/%s/anticipos" % fid2,
                    json={"recibo_id": aid, "monto": 400000}), (201,))
                saldo = float((await c.get(API + "/erp/ingresos/facturas/%s" % fid2)).json()["saldo"])
                rp = revisar("POST /pagos-recibidos (pago del resto)", await c.post(PR, json={
                    "tipo_ingreso": "PAGO_FACTURA", "tercero_id": tid, "cuenta_bancaria_id": cuenta_id,
                    "fecha": hoy, "facturas": [{"factura_id": fid2, "monto": saldo}]}), (201,))
                det = (await c.get(API + "/erp/ingresos/facturas/%s" % fid2)).json()
                if det["estado"] != "PAGADA" or float(det["saldo"]) != 0:
                    fallos.append("anticipo + recibo no dejaron la factura cobrada")
                ant = (await c.get(PR + "/%s" % aid)).json()
                if ant["estado"] != "APLICADO" or float(ant["saldo_por_aplicar"]) != 0:
                    fallos.append("el anticipo no quedó aplicado del todo")
                if rp.status_code == 201:
                    revisar("POST /pagos-recibidos/{id}/anular", await c.post(
                        PR + "/%s/anular" % rp.json()["id"], json={"motivo": "Anulación de humo"}))
                    det = (await c.get(API + "/erp/ingresos/facturas/%s" % fid2)).json()
                    if abs(float(det["saldo"]) - saldo) > 0.005:
                        fallos.append("anular el recibo no devolvió el saldo a la factura")
                revisar("POST /pagos-recibidos/{id}/anular (anticipo aplicado: rechazado)", await c.post(
                    PR + "/%s/anular" % aid, json={"motivo": "humo"}), (409,))
                # El banco: entró el anticipo; el pago del resto entró y salió al anularlo.
                if abs(await saldo_banco() - banco_antes - 400000) > 0.005:
                    fallos.append("el saldo contable de la cuenta no cuadra con los recibos")
                # Anticipo aplicado completo: la 280505 vuelve a quedar como estaba.
                if abs(await saldo_cuenta("280505") - anticipos_antes) > 0.005:
                    fallos.append("la cuenta de anticipos 280505 no quedó en cero tras aplicarlo")
                # La cartera queda con lo que la factura debe tras el anticipo y la anulación.
                if abs(await saldo_cuenta("130505") - cartera_antes - saldo) > 0.005:
                    fallos.append("la cartera 130505 no cuadra con el saldo de la factura")
            rk = revisar("GET  /pagos-recibidos/cuentas-contables",
                         await c.get(PR + "/cuentas-contables", params={"buscar": "421005"}))
            if rk.status_code == 200 and rk.json():
                ro = revisar("POST /pagos-recibidos (otro ingreso con retención)", await c.post(PR, json={
                    "tipo_ingreso": "OTROS", "tercero_id": tid, "cuenta_bancaria_id": cuenta_id, "fecha": hoy,
                    "lineas": [{"cuenta_id": rk.json()[0]["id"], "descripcion": "Intereses", "valor": 1000000}],
                    "retenciones": [{"tipo": "RETEFUENTE", "concepto": "Servicios generales",
                                     "porcentaje": 4}]}), (201,))
                if ro.status_code == 201 and float(ro.json()["valor_neto"]) != 960000:
                    fallos.append("el otro ingreso no descontó la retención")
                if ro.status_code == 201:
                    revisar("GET  /pagos-recibidos/{id}/pdf", await c.get(PR + "/%s/pdf" % ro.json()["id"]))
            rl = revisar("GET  /pagos-recibidos (filtros)", await c.get(PR, params={
                "identificacion": "800197268", "estado": "ANULADO", "limite": 5}))
            if rl.status_code == 200 and not rl.json():
                fallos.append("el filtro de recibos anulados no encontró el anulado")

        # ── Otros módulos siguen facturando: WMS 3PL por su endpoint, y las
        #    facturas de POS, WMS, 3PL y reencauche se ven en el listado nuevo ──
        rd = revisar("GET  /wms/depositantes", await c.get(API + "/wms/depositantes"))
        dep = next((x for x in rd.json() if x.get("codigo") == "HUMO3PL"), None) if rd.status_code == 200 else None
        if dep is None and rd.status_code == 200:
            dep = revisar("POST /wms/depositantes", await c.post(API + "/wms/depositantes", json={
                "codigo": "HUMO3PL", "nombre": "Depositante de humo", "nit": "900555111"}), (201,)).json()
        if dep:
            revisar("PUT  /wms/3pl/tarifas (mínimo mensual)", await c.put(
                API + "/wms/3pl/tarifas", params={"depositante_id": dep["id"]},
                json=[{"concepto": "MINIMO_MES", "valor": 300000, "iva_pct": 19}]))
            # Un mes cerrado que no esté liquidado todavía: cada corrida toma el anterior libre.
            fin = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
            rl3 = None
            for _ in range(36):
                inicio = fin.replace(day=1)
                rl3 = await c.post(API + "/wms/3pl/liquidaciones", json={
                    "depositante_id": dep["id"], "desde": inicio.isoformat(), "hasta": fin.isoformat()})
                if rl3.status_code != 409:
                    break
                fin = inicio - datetime.timedelta(days=1)
            revisar("POST /wms/3pl/liquidaciones", rl3, (201,))
            if rl3.status_code == 201:
                rf3 = revisar("POST /wms/3pl/liquidaciones/{id}/facturar",
                              await c.post(API + "/wms/3pl/liquidaciones/%s/facturar" % rl3.json()["id"]))
                if rf3.status_code == 200 and not rf3.json().get("factura_id"):
                    fallos.append("la liquidación 3PL no quedó con su factura")

        # La factura de cada origen se abre en la pantalla nueva con la resolución
        # de su tipo (POS con la de POS, el resto con la de factura de venta) y
        # aparece en el listado al buscarla por número.
        async with AsyncSessionLocal() as s:
            await s.execute(text('SET search_path TO "%s"' % ESQUEMA))
            filas = (await s.execute(text(
                "SELECT DISTINCT ON (f.origen) f.id, f.numero, f.origen, r.tipo_documento, r.prefijo "
                "FROM erp_facturas_cliente f JOIN erp_resolucion_facturacion r ON r.id = f.resolucion_id "
                "WHERE f.origen IN ('POS', 'WMS', 'WMS_3PL', 'REENCAUCHE') ORDER BY f.origen, f.id DESC"))).all()
        vistos = {f.origen for f in filas}
        for faltante in sorted({"WMS_3PL"} - vistos):
            fallos.append("no hay factura de %s: el módulo no facturó" % faltante)
        for otro in sorted({"POS", "WMS", "REENCAUCHE"} - vistos):
            print("      sin facturas de %s en esta base (corra humo_reencauche.py antes para cubrirlo)" % otro)
        for f in filas:
            esperado = "POS" if f.origen == "POS" else "FACTURA_VENTA"
            if f.tipo_documento != esperado:
                fallos.append("la factura %s de %s usó una resolución %s" % (f.numero, f.origen, f.tipo_documento))
            det = revisar("GET  /erp/ingresos/facturas/{id} (%s)" % f.origen,
                          await c.get(API + "/erp/ingresos/facturas/%s" % f.id))
            if det.status_code == 200 and (det.json().get("resolucion") or {}).get("prefijo") != f.prefijo:
                fallos.append("el detalle de %s no muestra la resolución %s" % (f.numero, f.prefijo))
            rb = revisar("GET  /erp/ingresos/facturas?buscar (%s)" % f.origen,
                         await c.get(API + "/erp/ingresos/facturas", params={"buscar": f.numero}))
            if rb.status_code == 200 and f.id not in [x["id"] for x in rb.json()]:
                fallos.append("la factura %s de %s no aparece en el listado nuevo" % (f.numero, f.origen))

        # ── Libros ──
        revisar("GET  /erp/contabilidad/balance-comprobacion",
                await c.get(API + "/erp/contabilidad/balance-comprobacion",
                            params={"empresa_id": eid, "hasta": hoy}))
        revisar("GET  /erp/contabilidad/libro-diario",
                await c.get(API + "/erp/contabilidad/libro-diario",
                            params={"empresa_id": eid, "desde": hoy, "hasta": hoy}))
        revisar("GET  /erp/contabilidad/estado-situacion",
                await c.get(API + "/erp/contabilidad/estado-situacion",
                            params={"empresa_id": eid, "hasta": hoy}))
        r = revisar("GET  /erp/contabilidad/estado-resultados",
                    await c.get(API + "/erp/contabilidad/estado-resultados",
                                params={"empresa_id": eid,
                                        "desde": hoy[:4] + "-01-01", "hasta": hoy}))
        if r.status_code == 200:
            d = r.json()
            print("      ingresos %s · costos %s · gastos %s"
                  % (d.get("total_ingresos"), d.get("total_costos"), d.get("total_gastos")))

        revisar("GET  /erp/auditoria",
                await c.get(API + "/erp/auditoria", params={"empresa_id": eid}))
        revisar("GET  /erp/contabilidad/eventos",
                await c.get(API + "/erp/contabilidad/eventos", params={"empresa_id": eid}))

    print()
    if fallos:
        print("FALLARON %d:" % len(fallos))
        for f in fallos:
            print("  - " + f)
        sys.exit(1)
    print("todo bien")


asyncio.run(principal())

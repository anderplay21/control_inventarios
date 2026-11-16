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
        yield s


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

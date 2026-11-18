"""Pruebas de la factura de venta del ERP (Ingresos · Factura de venta).

Corren contra un PostgreSQL de verdad, igual que `test_erp_contable.py`: el
estado de la factura es un enum nativo, los consecutivos usan `FOR UPDATE` y la
migración altera tipos y tablas, y nada de eso se comporta igual en SQLite.

Necesitan `URL_PRUEBAS` apuntando a una base DESECHABLE: se barren tablas y se
crean esquemas. Nunca a la base de trabajo.
"""
import os
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.main as principal
from app.api.v1.endpoints import erp_contable
from app.core import erp_motor, erp_semilla
from app.core.database import Base
from app.core.erp_motor import ErrorContable, Linea
from app.infrastructure.models.erp import (
    ERPComprobanteLinea, ERPEmpresa, ERPPlanCuenta, TipoComprobante,
)
from app.infrastructure.models.erp_nucleo import ERPReglaContable, ERPTercero

URL = os.environ["URL_PRUEBAS"]

# Año fijo, como en `test_erp_contable.py`: con el año en curso la batería
# empezaría a fallar sola cada enero.
FECHA = date(2025, 6, 15)

# Esquema propio para probar la migración, separado del `public` donde
# `create_all` deja las tablas de las demás pruebas.
ESQUEMA_MIGRACION = "pruebas_factura_venta"


# ─── Montaje ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def motor():
    mot = create_async_engine(URL, echo=False)
    async with mot.begin() as cx:
        await cx.run_sync(Base.metadata.create_all)
    # `create_all` no altera tablas que ya existen en la base de pruebas: se pasa
    # también la migración real, con un motor de pruebas aparte (ver
    # `migracion_en_pruebas`) y nunca con el de trabajo.
    original, aparte = principal.engine, create_async_engine(URL, echo=False)
    principal.engine = aparte
    try:
        await principal._migrar_esquema("public")
    finally:
        principal.engine = original
        await aparte.dispose()
    yield mot
    await mot.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db(motor) -> AsyncSession:
    async with motor.begin() as cx:
        await cx.execute(text(
            "TRUNCATE erp_factura_cliente_evento, erp_factura_cliente_adjunto, "
            "erp_nota_debito_cliente, erp_lineas_factura_cliente, erp_facturas_cliente, "
            "erp_comprobante_lineas, erp_comprobantes, erp_auditoria, erp_periodos, "
            "erp_reglas_contables, erp_reglas_impuesto, erp_parametros_fiscales, "
            "erp_eventos_contables, erp_plan_cuentas, erp_consecutivos, "
            "erp_pagos, erp_cuentas_bancarias, erp_bancos, "
            "erp_terceros, erp_empresas RESTART IDENTITY CASCADE"))
    fabrica = async_sessionmaker(motor, class_=AsyncSession, expire_on_commit=False)
    async with fabrica() as sesion:
        yield sesion
        await sesion.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def empresa(db) -> ERPEmpresa:
    emp = ERPEmpresa(nit="900123456", razon_social="Pruebas SAS", pais="Colombia")
    db.add(emp)
    await db.flush()
    return emp


@pytest_asyncio.fixture(loop_scope="session")
async def empresa_sembrada(db, empresa) -> ERPEmpresa:
    """Empresa con plan de cuentas, reglas y parámetros: lista para contabilizar."""
    await erp_semilla.sembrar_parametros(db)
    await erp_semilla.sembrar_empresa(db, empresa.id)
    await db.flush()
    return empresa


@pytest_asyncio.fixture(loop_scope="session")
async def migracion_en_pruebas(monkeypatch):
    """La migración de `main` usa su propio motor, que apunta a la base de
    trabajo. Se cambia por uno de pruebas PROPIO: la migración deja el
    `search_path` de sus conexiones en el esquema migrado, y si volvieran al
    pool compartido las demás pruebas escribirían en ese esquema."""
    aparte = create_async_engine(URL, echo=False)
    monkeypatch.setattr(principal, "engine", aparte)
    yield principal._migrar_esquema
    await aparte.dispose()


async def _cuenta(db, empresa_id: int, codigo: str) -> ERPPlanCuenta:
    return (await db.execute(select(ERPPlanCuenta).where(
        ERPPlanCuenta.empresa_id == empresa_id, ERPPlanCuenta.codigo == codigo))).scalar_one()


async def _columnas(motor, esquema, tabla):
    async with motor.connect() as cx:
        filas = await cx.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"), {"e": esquema, "t": tabla})
        return {c for (c,) in filas.all()}


# ─── Grupo 1: esquema y migración ─────────────────────────────────────────────

COLUMNAS_FACTURA = {
    "tipo_documento", "tercero_id", "vendedor_id", "forma_pago", "medio_pago",
    "remision", "notas_adicionales", "firma", "retencion_fuente", "retencion_ica",
    "retencion_iva", "retenciones_editadas", "cerrada_motivo", "cerrada_por", "cerrada_en",
    "comprobante_id",
}
TABLAS_NUEVAS = {"erp_nota_debito_cliente", "erp_factura_cliente_evento",
                 "erp_factura_cliente_adjunto"}


@pytest.mark.asyncio(loop_scope="session")
async def test_migracion_idempotente_deja_columnas_tablas_y_estado(motor, migracion_en_pruebas):
    """Dos vueltas seguidas no fallan y dejan todo lo nuevo en su sitio."""
    await migracion_en_pruebas(ESQUEMA_MIGRACION)
    await migracion_en_pruebas(ESQUEMA_MIGRACION)

    assert COLUMNAS_FACTURA <= await _columnas(motor, ESQUEMA_MIGRACION, "erp_facturas_cliente")
    assert {"referencia", "item_id"} <= await _columnas(
        motor, ESQUEMA_MIGRACION, "erp_lineas_factura_cliente")
    assert "es_vendedor" in await _columnas(motor, ESQUEMA_MIGRACION, "erp_terceros")

    async with motor.connect() as cx:
        tablas = {t for (t,) in (await cx.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = :e"),
            {"e": ESQUEMA_MIGRACION})).all()}
        estados = (await cx.execute(text(
            "SELECT array_agg(e.enumlabel) FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = 'estadofactura' AND n.nspname = :e"),
            {"e": ESQUEMA_MIGRACION})).scalar()
    assert TABLAS_NUEVAS <= tablas
    assert "CASTIGADA" in estados


@pytest.mark.asyncio(loop_scope="session")
async def test_factura_anterior_queda_como_factura_de_venta(motor, migracion_en_pruebas):
    """Una base de antes del cambio: sin la columna y con una factura emitida.

    Tras migrar, esa factura es FACTURA_VENTA, sin retenciones y sin marca de
    edición. Nada de lo que ya existía cambia de significado.
    """
    await migracion_en_pruebas(ESQUEMA_MIGRACION)
    async with motor.begin() as cx:
        # LOCAL: solo esta transacción. Un SET normal dejaría la conexión del pool
        # apuntando a este esquema para las pruebas que vengan después.
        await cx.execute(text(f'SET LOCAL search_path TO "{ESQUEMA_MIGRACION}"'))
        await cx.execute(text("DELETE FROM erp_facturas_cliente WHERE numero = 'VIEJA-1'"))
        await cx.execute(text("ALTER TABLE erp_facturas_cliente DROP COLUMN tipo_documento"))
        await cx.execute(text(
            "INSERT INTO erp_facturas_cliente (numero, cliente_nombre, fecha, "
            "fecha_vencimiento, subtotal, descuento, total_impuestos, total, saldo, "
            "estado, activo, created_at, updated_at) VALUES ('VIEJA-1', 'Cliente viejo', "
            "current_date, current_date, 100, 0, 19, 119, 119, 'EMITIDA', true, now(), now())"))

    await migracion_en_pruebas(ESQUEMA_MIGRACION)

    async with motor.connect() as cx:
        fila = (await cx.execute(text(
            f'SELECT tipo_documento, retencion_fuente, retencion_ica, retencion_iva, '
            f'retenciones_editadas, vendedor_id FROM "{ESQUEMA_MIGRACION}".erp_facturas_cliente '
            f"WHERE numero = 'VIEJA-1'"))).one()
    assert fila.tipo_documento == "FACTURA_VENTA"
    assert (fila.retencion_fuente, fila.retencion_ica, fila.retencion_iva) == (0, 0, 0)
    assert fila.retenciones_editadas is False
    assert fila.vendedor_id is None


# ─── Grupo 1: vendedores ──────────────────────────────────────────────────────

async def _tercero(db, empresa, nit, nombre, **marcas):
    t = ERPTercero(empresa_id=empresa.id, tipo_identificacion="CC",
                   numero_identificacion=nit, razon_social=nombre, **marcas)
    db.add(t)
    await db.flush()
    return t


async def _listar(db, empresa, tipo):
    return await erp_contable.listar_terceros(
        empresa_id=empresa.id, buscar=None, tipo=tipo, limite=200, db=db, usuario=None)


@pytest.mark.asyncio(loop_scope="session")
async def test_filtro_de_vendedores(db, empresa):
    """Solo aparece el tercero marcado como vendedor."""
    await _tercero(db, empresa, "1001", "Ana Vendedora", es_vendedor=True)
    await _tercero(db, empresa, "1002", "Beto Cliente", es_cliente=True)
    await _tercero(db, empresa, "1003", "Carla Empleada", es_empleado=True)

    nombres = {t.razon_social for t in await _listar(db, empresa, "vendedor")}
    assert nombres == {"Ana Vendedora"}


@pytest.mark.asyncio(loop_scope="session")
async def test_vendedor_es_independiente_de_empleado(db, empresa):
    """Un empleado no es vendedor por serlo, y un vendedor externo no es empleado."""
    await _tercero(db, empresa, "2001", "Empleado vendedor", es_empleado=True, es_vendedor=True)
    await _tercero(db, empresa, "2002", "Agente externo", es_vendedor=True)
    await _tercero(db, empresa, "2003", "Empleado de bodega", es_empleado=True)

    vendedores = {t.razon_social for t in await _listar(db, empresa, "vendedor")}
    empleados = {t.razon_social for t in await _listar(db, empresa, "empleado")}
    assert vendedores == {"Empleado vendedor", "Agente externo"}
    assert empleados == {"Empleado vendedor", "Empleado de bodega"}


@pytest.mark.asyncio(loop_scope="session")
async def test_respuesta_de_tercero_incluye_es_vendedor(db, empresa):
    t = await _tercero(db, empresa, "3001", "Con marca", es_vendedor=True)
    respuesta = erp_contable.TerceroResponse.model_validate(t)
    assert respuesta.es_vendedor is True
    assert erp_contable.TerceroEntrada(
        empresa_id=empresa.id, numero_identificacion="1", razon_social="x").es_vendedor is False


# ─── Grupo 2: eventos y siembra ───────────────────────────────────────────────

async def _lineas_por_cuenta(db, comprobante_id):
    filas = (await db.execute(
        select(ERPPlanCuenta.codigo, ERPComprobanteLinea.debito, ERPComprobanteLinea.credito)
        .join(ERPPlanCuenta, ERPPlanCuenta.id == ERPComprobanteLinea.cuenta_id)
        .where(ERPComprobanteLinea.comprobante_id == comprobante_id))).all()
    # Suma por cuenta: un recibo de varias facturas lleva una línea de cartera por factura.
    total: dict = {}
    for c, d, k in filas:
        td, tk = total.get(c, (Decimal(0), Decimal(0)))
        total[c] = (td + Decimal(d or 0), tk + Decimal(k or 0))
    return total


@pytest.mark.asyncio(loop_scope="session")
async def test_nota_debito_cuadra_y_va_a_sus_cuentas(db, empresa_sembrada):
    comp = await erp_motor.asentar(
        db, empresa_id=empresa_sembrada.id, evento="VENTA_NOTA_DEBITO",
        tipo=TipoComprobante.DIARIO, fecha=FECHA, concepto="Nota débito",
        lineas=[Linea("cartera", debito="59500"), Linea("ingreso", credito="50000"),
                Linea("iva_generado", credito="9500")],
        usuario="prueba")
    lineas = await _lineas_por_cuenta(db, comp.id)
    assert lineas == {"130505": (Decimal("59500"), 0), "413500": (0, Decimal("50000")),
                      "240805": (0, Decimal("9500"))}


@pytest.mark.asyncio(loop_scope="session")
async def test_castigo_cuadra_y_saca_la_cartera_contra_gasto(db, empresa_sembrada):
    comp = await erp_motor.asentar(
        db, empresa_id=empresa_sembrada.id, evento="CASTIGO_CARTERA",
        tipo=TipoComprobante.DIARIO, fecha=FECHA, concepto="Castigo",
        lineas=[Linea("gasto_castigo", debito="300000"), Linea("cartera", credito="300000"),
                Linea("control_castigo", debito="300000"), Linea("contra_castigo", credito="300000")],
        usuario="prueba")
    assert await _lineas_por_cuenta(db, comp.id) == CASTIGO_300K


# Castigo confirmado por contabilidad: gasto contra cartera y, aparte, el control
# en cuentas de orden 832510 / 860510.
CASTIGO_300K = {
    "519910": (Decimal("300000"), 0), "130505": (0, Decimal("300000")),
    "832510": (Decimal("300000"), 0), "860510": (0, Decimal("300000"))}


@pytest.mark.asyncio(loop_scope="session")
async def test_papel_no_declarado_se_rechaza(db, empresa_sembrada):
    """Un papel que el evento no conoce no encuentra cuenta: se detiene."""
    with pytest.raises(ErrorContable):
        await erp_motor.asentar(
            db, empresa_id=empresa_sembrada.id, evento="CASTIGO_CARTERA",
            tipo=TipoComprobante.DIARIO, fecha=FECHA, concepto="Castigo con papel ajeno",
            lineas=[Linea("ingreso", debito="1000"), Linea("cartera", credito="1000")],
            usuario="prueba")


@pytest.mark.asyncio(loop_scope="session")
async def test_gramatica_declara_los_eventos_nuevos():
    assert erp_motor.NATURALEZA_POR_EVENTO["VENTA_NOTA_DEBITO"][1] == {
        "cartera": "D", "ingreso": "C", "iva_generado": "C"}
    assert erp_motor.NATURALEZA_POR_EVENTO["CASTIGO_CARTERA"][1] == {
        "gasto_castigo": "D", "cartera": "C", "control_castigo": "D", "contra_castigo": "C"}


async def _regla(db, empresa_id, evento, papel):
    return (await db.execute(select(ERPReglaContable).where(
        ERPReglaContable.empresa_id == empresa_id, ERPReglaContable.evento == evento,
        ERPReglaContable.papel == papel))).scalar_one_or_none()


@pytest.mark.asyncio(loop_scope="session")
async def test_siembra_completa_empresa_vieja_sin_pisar_reglas_editadas(db, empresa_sembrada):
    """Empresa de antes del cambio: le faltan las reglas nuevas, y alguien ya
    había movido el castigo a otra cuenta. Re-sembrar agrega lo que falta y
    respeta lo editado."""
    eid = empresa_sembrada.id
    # Lo que tendría una empresa vieja: sin las reglas de nota débito.
    for papel in ("cartera", "ingreso", "iva_generado"):
        await db.delete(await _regla(db, eid, "VENTA_NOTA_DEBITO", papel))
    # Y el castigo ya reasignado a mano a otra cuenta.
    otra = (await db.execute(select(ERPPlanCuenta).where(
        ERPPlanCuenta.empresa_id == eid, ERPPlanCuenta.codigo == "539995"))).scalar_one()
    castigo = await _regla(db, eid, "CASTIGO_CARTERA", "gasto_castigo")
    castigo.cuenta_id = otra.id
    await db.flush()

    resultado = await erp_semilla.sembrar_empresa(db, eid)
    await db.flush()

    assert resultado["reglas"] == 3
    for papel in ("cartera", "ingreso", "iva_generado"):
        assert await _regla(db, eid, "VENTA_NOTA_DEBITO", papel) is not None
    assert (await _regla(db, eid, "CASTIGO_CARTERA", "gasto_castigo")).cuenta_id == otra.id
    # Y una segunda vuelta no agrega nada.
    assert (await erp_semilla.sembrar_empresa(db, eid)) == {"cuentas": 0, "reglas": 0, "impuestos": 0}


# ─── Grupo 2: permiso de castigo ──────────────────────────────────────────────
#
# Acá se prueba la guardia; el castigo de una factura completo («Castigo con
# permiso», «Sin permiso» sobre el documento) se prueba con su endpoint en el
# grupo 6.

from fastapi import HTTPException  # noqa: E402

from app.core import erp_permisos  # noqa: E402
from app.core.permisos_perfil import CLAVES, POR_CLAVE  # noqa: E402
from app.infrastructure.models.rol import Rol  # noqa: E402


class _Usuario:
    def __init__(self, rol, rol_id=None):
        self.rol, self.rol_id, self.id = rol, rol_id, 1


async def _perfil(db, nombre, permisos):
    await db.execute(text("DELETE FROM roles WHERE nombre = :n"), {"n": nombre})
    r = Rol(nombre=nombre, permisos=permisos)
    db.add(r)
    await db.flush()
    return r


@pytest.mark.asyncio(loop_scope="session")
async def test_castigar_exige_su_permiso(db):
    con = await _perfil(db, "PRUEBA_CON_CASTIGO", {"erp": True, "fin_castigar": True})
    sin = await _perfil(db, "PRUEBA_SIN_CASTIGO", {"erp": True, "fin_pagos": True, "fin_anular": True})

    assert await erp_permisos.castigar(_Usuario("CARTERA", con.id), db) is not None

    with pytest.raises(HTTPException) as exc:
        await erp_permisos.castigar(_Usuario("CARTERA", sin.id), db)
    assert exc.value.status_code == 403
    # El mensaje nombra el permiso que falta, no un «no autorizado» a secas.
    assert "Castigar cartera" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_administrador_puede_castigar_sin_marca(db):
    assert await erp_permisos.castigar(_Usuario("ADMINISTRADOR"), db) is not None


def test_permiso_aparece_en_finanzas():
    """La consola de perfiles lo muestra solo por estar en esta lista."""
    assert "fin_castigar" in CLAVES
    assert POR_CLAVE["fin_castigar"].grupo == "Finanzas"


# ─── Grupo 3: resolución vigente ──────────────────────────────────────────────
#
# Van contra la base y no como prueba de unidad pura: la función consulta las
# resoluciones y `tomar_numero` numera con UPDATE … RETURNING.

from datetime import timedelta  # noqa: E402

from app.core import facturacion_dian as dian  # noqa: E402
from app.infrastructure.models.erp_facturacion import (  # noqa: E402
    ERPFacturaClienteEvento, ERPResolucionFacturacion,
)

HOY = date.today()


async def _resolucion(db, empresa, tipo="FACTURA_VENTA", prefijo="FV", desde=1, hasta=100,
                      actual=0, inicio=HOY - timedelta(days=30), fin=HOY + timedelta(days=300),
                      activa=True):
    r = ERPResolucionFacturacion(
        empresa_id=empresa.id, tipo_documento=tipo, numero_resolucion=f"R-{prefijo}",
        fecha_resolucion=inicio, prefijo=prefijo, desde=desde, hasta=hasta, actual=actual,
        vigencia_desde=inicio, vigencia_hasta=fin, clave_tecnica="clave", ambiente="2",
        activa=activa)
    db.add(r)
    await db.flush()
    return r


@pytest.mark.asyncio(loop_scope="session")
async def test_resolucion_vigente_y_numeracion(db, empresa):
    r = await _resolucion(db, empresa)
    assert (await dian.resolucion_vigente(db, empresa.id, "FACTURA_VENTA")).id == r.id
    numero, _ = await dian.tomar_numero(db, r.id)
    assert numero == "FV1"
    numero, _ = await dian.tomar_numero(db, r.id)
    assert numero == "FV2"


@pytest.mark.asyncio(loop_scope="session")
async def test_ticket_usa_la_resolucion_pos(db, empresa):
    await _resolucion(db, empresa, tipo="FACTURA_VENTA", prefijo="FV")
    pos = await _resolucion(db, empresa, tipo="POS", prefijo="TK")
    assert (await dian.resolucion_vigente(db, empresa.id, "POS")).id == pos.id


@pytest.mark.asyncio(loop_scope="session")
async def test_resolucion_vencida_no_sirve(db, empresa):
    await _resolucion(db, empresa, inicio=HOY - timedelta(days=400), fin=HOY - timedelta(days=1))
    with pytest.raises(dian.ResolucionInvalida) as exc:
        await dian.resolucion_vigente(db, empresa.id, "FACTURA_VENTA")
    assert "factura electrónica de venta" in str(exc.value)


@pytest.mark.asyncio(loop_scope="session")
async def test_sin_resolucion_dice_que_configurar(db, empresa):
    with pytest.raises(dian.ResolucionInvalida) as exc:
        await dian.resolucion_vigente(db, empresa.id, "POS")
    assert "ticket" in str(exc.value) and "Resoluciones de facturación" in str(exc.value)


@pytest.mark.asyncio(loop_scope="session")
async def test_resolucion_agotada_no_consume_ni_repite(db, empresa):
    r = await _resolucion(db, empresa, desde=1, hasta=3, actual=3)
    elegida = await dian.resolucion_vigente(db, empresa.id, "FACTURA_VENTA")
    with pytest.raises(dian.ResolucionInvalida) as exc:
        await dian.tomar_numero(db, elegida.id)
    assert "Se agotó" in str(exc.value)
    await db.refresh(r)
    assert r.actual == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_prefiere_la_resolucion_con_numeros(db, empresa):
    """La vieja agotada no debe tumbar la factura si otra vigente tiene números."""
    await _resolucion(db, empresa, prefijo="VJ", desde=1, hasta=2, actual=2)
    nueva = await _resolucion(db, empresa, prefijo="NV", desde=1, hasta=50)
    assert (await dian.resolucion_vigente(db, empresa.id, "FACTURA_VENTA")).id == nueva.id


# ─── Grupo 3: emisión ─────────────────────────────────────────────────────────

from app.core import erp_ventas  # noqa: E402
from app.core.erp_ventas import FacturaVentaEntrada, LineaVentaEntrada  # noqa: E402
from app.infrastructure.models.erp import (  # noqa: E402
    ERPComprobante, ERPFacturaCliente, ERPLineaFacturaCliente, ERPTipoImpuesto,
    EstadoFactura, TipoImpuestoEnum,
)
from app.infrastructure.models.erp_nucleo import ERPPeriodo  # noqa: E402


async def _iva19(db) -> ERPTipoImpuesto:
    t = (await db.execute(select(ERPTipoImpuesto).where(ERPTipoImpuesto.codigo == "IVA19-PR"))).scalar()
    if t is None:
        t = ERPTipoImpuesto(nombre="IVA 19 %", codigo="IVA19-PR", tipo=TipoImpuestoEnum.IVA,
                            porcentaje=19)
        db.add(t)
        await db.flush()
    return t


async def _datos(db, **cambios) -> FacturaVentaEntrada:
    iva = await _iva19(db)
    base = dict(fecha=HOY, cliente_identificacion="800111222", cliente_nombre="Cliente SAS",
                cliente_email="pagos@cliente.co",
                lineas=[LineaVentaEntrada(referencia="SRV-1", descripcion="Servicio",
                                          cantidad=3, precio_unitario=100000,
                                          descuento_pct=10, impuesto_id=iva.id)])
    base.update(cambios)
    return FacturaVentaEntrada(**base)


async def _contar(db, modelo):
    return len((await db.execute(select(modelo))).scalars().all())


@pytest.mark.asyncio(loop_scope="session")
async def test_emite_factura_con_resolucion_vigente(db, empresa_sembrada):
    res = await _resolucion(db, empresa_sembrada, prefijo="FV")
    fac = await erp_ventas.emitir_factura(db, await _datos(db), "prueba")

    assert fac.numero == "FV1" and fac.estado == EstadoFactura.EMITIDA
    assert fac.resolucion_id == res.id and fac.estado_dian == "POR_TRANSMITIR"
    assert (fac.subtotal, fac.total_impuestos, fac.total) == (
        Decimal("270000.00"), Decimal("51300.00"), Decimal("321300.00"))
    assert fac.saldo == fac.total
    assert len(fac.cufe) == 96          # SHA-384 en hexadecimal
    await db.refresh(res)
    assert res.actual == 1
    # Su asiento: cartera por el total, ingreso por la base, IVA aparte.
    comp = await db.get(ERPComprobante, fac.comprobante_id)
    assert comp is not None and comp.referencia == fac.numero
    assert await _lineas_por_cuenta(db, comp.id) == {
        "130505": (Decimal("321300"), 0), "413500": (0, Decimal("270000")),
        "240805": (0, Decimal("51300"))}
    # Y el consecutivo avanza en uno con la siguiente.
    otra = await erp_ventas.emitir_factura(db, await _datos(db), "prueba")
    assert otra.numero == "FV2"


@pytest.mark.asyncio(loop_scope="session")
async def test_ticket_con_su_propia_numeracion(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada, tipo="FACTURA_VENTA", prefijo="FV")
    await _resolucion(db, empresa_sembrada, tipo="POS", prefijo="TK")
    fac = await erp_ventas.emitir_factura(db, await _datos(db), "prueba")
    tk = await erp_ventas.emitir_factura(db, await _datos(db, tipo_documento="TICKET"), "prueba")
    assert (fac.numero, tk.numero) == ("FV1", "TK1")
    assert tk.tipo_documento == "TICKET"


@pytest.mark.asyncio(loop_scope="session")
async def test_sin_resolucion_no_crea_factura(db, empresa_sembrada):
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db), "prueba")
    assert exc.value.status_code == 422 and "Resoluciones de facturación" in exc.value.detail
    assert await _contar(db, ERPFacturaCliente) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_resolucion_agotada_no_crea_factura(db, empresa_sembrada):
    res = await _resolucion(db, empresa_sembrada, desde=1, hasta=5, actual=5)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db), "prueba")
    assert "Se agotó" in exc.value.detail
    await db.refresh(res)
    assert res.actual == 5 and await _contar(db, ERPFacturaCliente) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_sin_lineas_validas_se_rechaza(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    vacias = [LineaVentaEntrada(descripcion="Nada", cantidad=0, precio_unitario=100),
              LineaVentaEntrada(descripcion="Gratis", cantidad=2, precio_unitario=0)]
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db, lineas=vacias), "prueba")
    assert "al menos un ítem" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_periodo_cerrado_no_deja_factura_ni_consume_numero(db, empresa_sembrada):
    """El rechazo del asiento deshace todo: ni factura sin asiento ni hueco en el
    consecutivo. Se prueba como lo vive el endpoint: excepción y rollback."""
    res = await _resolucion(db, empresa_sembrada)
    db.add(ERPPeriodo(empresa_id=empresa_sembrada.id, anio=HOY.year, mes=HOY.month, estado="CERRADO"))
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db), "prueba")
    assert exc.value.status_code == 422
    await db.rollback()
    await db.refresh(res)
    assert res.actual == 0
    assert await _contar(db, ERPFacturaCliente) == 0
    assert await _contar(db, ERPLineaFacturaCliente) == 0


# ─── Grupo 3: rutas ───────────────────────────────────────────────────────────

from app.api.v1.endpoints import erp as erp_endpoints  # noqa: E402
from app.api.v1.endpoints import erp_ventas as ventas_endpoints  # noqa: E402


class _Admin:
    id, rol, rol_id, nombre, username = 1, "ADMINISTRADOR", None, "Prueba", "prueba"


def test_rutas_registradas():
    rutas = {(r.path, tuple(sorted(r.methods))) for r in principal.app.routes if hasattr(r, "methods")}
    assert ("/api/v1/erp/ingresos/facturas", ("POST",)) in rutas
    assert ("/api/v1/erp/cxc/facturas", ("POST",)) in rutas


@pytest.mark.asyncio(loop_scope="session")
async def test_ruta_nueva_emite(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada, prefijo="FE")
    fac = await ventas_endpoints.crear_factura(await _datos(db), db=db, usuario=_Admin())
    respuesta = ventas_endpoints.FacturaVentaResponse.model_validate(fac)
    assert respuesta.numero == "FE1" and respuesta.total == Decimal("321300.00")


@pytest.mark.asyncio(loop_scope="session")
async def test_ruta_vieja_delega_e_ignora_el_numero(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada, prefijo="FE")
    iva = await _iva19(db)
    vieja = erp_endpoints.FacturaClienteCreate(
        numero="LO-QUE-DIGITO-EL-USUARIO", cliente_nombre="Cliente viejo", cliente_nit="900555111",
        fecha=HOY, lineas=[erp_endpoints.LineaFacturaCreate(
            descripcion="Servicio", cantidad=1, precio_unitario=1000, impuesto_id=iva.id)])
    fac = await erp_endpoints.crear_factura_cliente(vieja, db=db, current_user=_Admin())
    assert fac.numero == "FE1"
    assert fac.total == Decimal("1190.00") and fac.comprobante_id is not None
    # Sin número en el cuerpo también funciona.
    vieja.numero = None
    assert (await erp_endpoints.crear_factura_cliente(vieja, db=db, current_user=_Admin())).numero == "FE2"


# ─── Grupo 3: cliente ─────────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_cliente_existente_se_vincula_y_autocompleta(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    existente = await _tercero(db, empresa_sembrada, "800111222", "Cliente Registrado SAS",
                               es_cliente=True)
    existente.email = "cartera@registrado.co"
    await db.flush()

    # Autocompletar: la búsqueda devuelve nombre y correo del tercero.
    hallado = await ventas_endpoints.buscar_cliente("800.111.222", db=db, usuario=_Admin())
    assert (hallado.razon_social, hallado.email) == ("Cliente Registrado SAS", "cartera@registrado.co")

    # Al guardar con otro nombre (el campo sigue editable) se vincula, sin duplicar.
    fac = await erp_ventas.emitir_factura(db, await _datos(db, cliente_nombre="Nombre editado"), "prueba")
    assert fac.tercero_id == existente.id and fac.cliente_nombre == "Nombre editado"
    terceros = (await db.execute(select(ERPTercero).where(
        ERPTercero.numero_identificacion == "800111222"))).scalars().all()
    assert len(terceros) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_cliente_nuevo_crea_tercero_cliente(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.buscar_cliente("901999888", db=db, usuario=_Admin())
    assert exc.value.status_code == 404
    fac = await erp_ventas.emitir_factura(
        db, await _datos(db, cliente_identificacion="901999888", cliente_nombre="Nuevo SAS"), "prueba")
    t = await db.get(ERPTercero, fac.tercero_id)
    assert (t.razon_social, t.es_cliente, t.email) == ("Nuevo SAS", True, "pagos@cliente.co")
    assert t.digito_verificacion is not None      # NIT: el DV se calcula


@pytest.mark.asyncio(loop_scope="session")
async def test_correo_invalido_no_guarda(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db, cliente_email="pagos-arroba-cliente"), "prueba")
    assert "correo" in exc.value.detail
    assert await _contar(db, ERPFacturaCliente) == 0


# ─── Grupo 3: selectores, notas y concurrencia ────────────────────────────────

import asyncio  # noqa: E402

from app.infrastructure.models.catalogo import CatalogoMaestro  # noqa: E402
from app.infrastructure.models.erp import ERPCentroCosto  # noqa: E402


async def _catalogo(db, tipo, nombre):
    if not (await db.execute(select(CatalogoMaestro).where(
            CatalogoMaestro.modulo == "ERP", CatalogoMaestro.tipo == tipo,
            CatalogoMaestro.nombre == nombre))).scalar():
        db.add(CatalogoMaestro(modulo="ERP", tipo=tipo, nombre=nombre, orden=1, activo=True))
        await db.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_selectores_validos_quedan_en_la_factura(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    vendedor = await _tercero(db, empresa_sembrada, "5001", "Vendedora", es_vendedor=True)
    cc = ERPCentroCosto(empresa_id=empresa_sembrada.id, codigo="CC-PR-1", nombre="Ventas")
    db.add(cc)
    await _catalogo(db, "FORMA_PAGO", "Contado")
    await _catalogo(db, "MEDIO_PAGO", "Tarjeta")
    await db.flush()
    fac = await erp_ventas.emitir_factura(db, await _datos(
        db, vendedor_id=vendedor.id, centro_costo_id=cc.id, forma_pago="contado",
        medio_pago="TARJETA", moneda="usd"), "prueba")
    assert (fac.vendedor_id, fac.centro_costo_id, fac.forma_pago, fac.medio_pago, fac.moneda) == (
        vendedor.id, cc.id, "Contado", "Tarjeta", "USD")


@pytest.mark.asyncio(loop_scope="session")
async def test_solo_terceros_vendedores(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    no_vendedor = await _tercero(db, empresa_sembrada, "5002", "Solo cliente", es_cliente=True)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db, vendedor_id=no_vendedor.id), "prueba")
    assert "vendedor" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_centro_de_costo_de_otra_empresa_se_rechaza(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    otra = ERPEmpresa(nit="900999000", razon_social="Otra SAS", pais="Colombia")
    db.add(otra)
    await db.flush()
    cc = ERPCentroCosto(empresa_id=otra.id, codigo="CC-OTRA", nombre="Ajeno")
    db.add(cc)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db, centro_costo_id=cc.id), "prueba")
    assert "centro de costo" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_forma_de_pago_fuera_del_catalogo(db, empresa_sembrada):
    await _resolucion(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, await _datos(db, forma_pago="Trueque"), "prueba")
    assert "no está en el catálogo" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_notas_visibles_e_internas_separadas(db, empresa_sembrada):
    """«Notas e internos separados»: cada una en su campo; el PDF usa solo la nota."""
    await _resolucion(db, empresa_sembrada)
    fac = await erp_ventas.emitir_factura(db, await _datos(
        db, notas="Gracias por su compra", notas_adicionales="Cliente pide factura física",
        comentario="Llamar antes del vencimiento"), "prueba")
    assert (fac.observaciones, fac.notas_adicionales) == (
        "Gracias por su compra", "Cliente pide factura física")
    tipos = {e.tipo for e in (await db.execute(select(ERPFacturaClienteEvento).where(
        ERPFacturaClienteEvento.factura_id == fac.id))).scalars().all()}
    assert tipos == {"EMISION", "COMENTARIO"}


@pytest.mark.asyncio(loop_scope="session")
async def test_emisiones_simultaneas_no_repiten_consecutivo(motor, db, empresa_sembrada):
    """Dos sesiones emiten a la vez: cada una recibe su número."""
    await _resolucion(db, empresa_sembrada, prefijo="CC")
    await _iva19(db)
    await db.commit()
    fabrica = async_sessionmaker(motor, class_=AsyncSession, expire_on_commit=False)

    async def emitir():
        async with fabrica() as s:
            fac = await erp_ventas.emitir_factura(s, await _datos(s), "prueba")
            await s.commit()
            return fac.numero

    numeros = await asyncio.gather(*(emitir() for _ in range(5)))
    assert sorted(numeros) == [f"CC{i}" for i in range(1, 6)]


# ─── Grupo 4: recaudo ─────────────────────────────────────────────────────────

from app.core.erp_ventas import PagoVentaEntrada  # noqa: E402
from app.infrastructure.models.erp import (  # noqa: E402
    ERPBanco, ERPCuentaBancaria, ERPPago, TipoCuentaBancaria,
)


async def _cuenta_bancaria(db, empresa, cuenta_contable=None):
    banco = ERPBanco(nombre="Banco de Pruebas")
    db.add(banco)
    await db.flush()
    c = ERPCuentaBancaria(empresa_id=empresa.id, banco_id=banco.id, numero="123-456",
                          tipo=TipoCuentaBancaria.CORRIENTE, cuenta_contable_id=cuenta_contable)
    db.add(c)
    await db.flush()
    return c


async def _factura_emitida(db, empresa, **cambios):
    if not (await db.execute(select(ERPResolucionFacturacion).where(
            ERPResolucionFacturacion.empresa_id == empresa.id))).first():
        await _resolucion(db, empresa)
    await _catalogo(db, "MEDIO_PAGO", "Transferencia")
    return await erp_ventas.emitir_factura(db, await _datos(db, **cambios), "prueba")


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_total_deja_cobrada(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    pago = await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=fac.saldo, cuenta_bancaria_id=cta.id, metodo_pago="transferencia"), "prueba")
    assert fac.saldo == 0 and fac.estado == EstadoFactura.PAGADA
    # El pago rápido crea un recibo de caja con el talonario RCJ (Pagos recibidos).
    assert pago.metodo_pago == "Transferencia" and pago.numero.startswith(f"RCJ-{HOY.year}-")
    assert pago.recibo_id is not None and pago.origen == "RECIBO"


@pytest.mark.asyncio(loop_scope="session")
async def test_abono_parcial(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=100000, cuenta_bancaria_id=cta.id), "prueba")
    assert fac.saldo == Decimal("221300.00") and fac.estado == EstadoFactura.PARCIALMENTE_PAGADA


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("monto,mensaje", [
    (Decimal("321300.01"), "supera el saldo"), (0, "mayor que cero"), (-5, "mayor que cero")])
async def test_pagos_invalidos_no_dejan_rastro(db, empresa_sembrada, monto, mensaje):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    comprobantes_antes = await _contar(db, ERPComprobante)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
            monto=monto, cuenta_bancaria_id=cta.id), "prueba")
    assert mensaje in exc.value.detail
    assert fac.saldo == Decimal("321300.00") and fac.estado == EstadoFactura.EMITIDA
    assert await _contar(db, ERPPago) == 0
    assert await _contar(db, ERPComprobante) == comprobantes_antes


@pytest.mark.asyncio(loop_scope="session")
async def test_factura_anulada_no_admite_pagos(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    fac.estado = EstadoFactura.ANULADA
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
            monto=1000, cuenta_bancaria_id=cta.id), "prueba")
    assert "no admite pagos" in exc.value.detail and await _contar(db, ERPPago) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_cuenta_bancaria_obligatoria(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(monto=1000), "prueba")
    assert "cuenta bancaria" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_asiento_y_vinculo_del_pago(db, empresa_sembrada):
    """«Asiento y vínculo»: el dinero entra a la cuenta contable de esa cuenta
    bancaria y sale de cartera; el pago queda en el historial de la factura."""
    fac = await _factura_emitida(db, empresa_sembrada)
    banco_contable = await _cuenta(db, empresa_sembrada.id, "111005")
    cta = await _cuenta_bancaria(db, empresa_sembrada, cuenta_contable=banco_contable.id)
    pago = await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=200000, cuenta_bancaria_id=cta.id), "prueba")
    assert await _lineas_por_cuenta(db, pago.comprobante_id) == {
        "111005": (Decimal("200000"), 0), "130505": (0, Decimal("200000"))}
    eventos = (await db.execute(select(ERPFacturaClienteEvento).where(
        ERPFacturaClienteEvento.factura_id == fac.id,
        ERPFacturaClienteEvento.tipo == "PAGO"))).scalars().all()
    assert len(eventos) == 1 and eventos[0].datos["pago_id"] == pago.id


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_en_periodo_cerrado_no_cambia_nada(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    db.add(ERPPeriodo(empresa_id=empresa_sembrada.id, anio=2024, mes=1, estado="CERRADO"))
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
            monto=1000, cuenta_bancaria_id=cta.id, fecha=date(2024, 1, 15)), "prueba")
    assert exc.value.status_code == 422
    await db.rollback()
    await db.refresh(fac)
    assert fac.saldo == Decimal("321300.00") and await _contar(db, ERPPago) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_endpoint_de_pago_rapido(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await db.commit()
    pago = await ventas_endpoints.agregar_pago(
        fac.id, PagoVentaEntrada(monto=21300, cuenta_bancaria_id=cta.id), db=db, usuario=_Admin())
    assert ventas_endpoints.PagoVentaResponse.model_validate(pago).monto == Decimal("21300.00")
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.agregar_pago(999999, PagoVentaEntrada(monto=1), db=db, usuario=_Admin())
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_ruta_vieja_de_pagos_ya_no_acepta_sobrepago(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await erp_endpoints.registrar_pago(erp_endpoints.PagoCreate(
            tipo="COBRO", numero="P-1", fecha=HOY, factura_cliente_id=fac.id,
            cuenta_bancaria_id=cta.id, monto=999999999), db=db, current_user=_Admin())
    assert "supera el saldo" in exc.value.detail
    await db.refresh(fac)
    assert fac.saldo == Decimal("321300.00")
    assert await _contar(db, ERPPago) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_ruta_vieja_sigue_pagando_proveedores(db, empresa_sembrada):
    from app.infrastructure.models.erp import ERPFacturaProveedor
    fp = ERPFacturaProveedor(empresa_id=empresa_sembrada.id, numero_proveedor="PRV-1",
                             proveedor_nombre="Proveedor", fecha=HOY, fecha_vencimiento=HOY,
                             subtotal=1000, total_impuestos=0, total=1000, saldo=1000,
                             estado=EstadoFactura.EMITIDA)
    db.add(fp)
    await db.commit()
    pago = await erp_endpoints.registrar_pago(erp_endpoints.PagoCreate(
        tipo="PAGO", numero="E-1", fecha=HOY, factura_proveedor_id=fp.id, monto=400),
        db=db, current_user=_Admin())
    await db.refresh(fp)
    assert pago.id and fp.saldo == 600


@pytest.mark.asyncio(loop_scope="session")
async def test_factura_de_contado_pagada_al_crear(db, empresa_sembrada):
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    fac = await _factura_emitida(db, empresa_sembrada, pago=PagoVentaEntrada(
        monto=Decimal("321300"), cuenta_bancaria_id=cta.id, metodo_pago="Transferencia"))
    assert fac.estado == EstadoFactura.PAGADA and fac.saldo == 0
    assert await _contar(db, ERPPago) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_invalido_al_crear_no_guarda_la_factura(db, empresa_sembrada):
    """«Falla el recaudo»: sin cuenta bancaria, ni pago ni factura."""
    res = await _resolucion(db, empresa_sembrada)
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.crear_factura(
            await _datos(db, pago=PagoVentaEntrada(monto=1000)), db=db, usuario=_Admin())
    assert "cuenta bancaria" in exc.value.detail
    await db.refresh(res)
    assert res.actual == 0
    assert await _contar(db, ERPFacturaCliente) == 0 and await _contar(db, ERPPago) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_medio_de_pago_configurable(db, empresa_sembrada):
    """«Lista configurable»: un medio nuevo se acepta en cuanto está en el catálogo."""
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    with pytest.raises(HTTPException):
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
            monto=1000, cuenta_bancaria_id=cta.id, metodo_pago="Nequi"), "prueba")
    await _catalogo(db, "MEDIO_PAGO", "Nequi")
    pago = await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=1000, cuenta_bancaria_id=cta.id, metodo_pago="nequi"), "prueba")
    assert pago.metodo_pago == "Nequi"


# ─── Grupo 5: consulta ────────────────────────────────────────────────────────

import io  # noqa: E402
import time  # noqa: E402

from fastapi import Response  # noqa: E402

from app.infrastructure.models.erp_facturacion import ERPNotaCreditoCliente  # noqa: E402


async def _listar_facturas(db, **filtros):
    respuesta = Response()
    filtros.setdefault("limite", 50)
    filtros.setdefault("desplazamiento", 0)
    for k in ("estado", "tipo_documento", "buscar", "vendedor_id", "desde", "hasta"):
        filtros.setdefault(k, None)
    filas = await ventas_endpoints.listar_facturas(respuesta, db=db, usuario=_Admin(), **filtros)
    return filas, int(respuesta.headers["X-Total-Count"])


@pytest.mark.asyncio(loop_scope="session")
async def test_listado_mas_reciente_primero_y_paginado(db, empresa_sembrada):
    vieja = await _factura_emitida(db, empresa_sembrada, fecha=HOY - timedelta(days=5))
    nueva = await _factura_emitida(db, empresa_sembrada, fecha=HOY)
    media = await _factura_emitida(db, empresa_sembrada, fecha=HOY - timedelta(days=2))
    filas, total = await _listar_facturas(db)
    assert [f.id for f in filas] == [nueva.id, media.id, vieja.id] and total == 3
    pagina2, total = await _listar_facturas(db, limite=1, desplazamiento=1)
    assert [f.id for f in pagina2] == [media.id] and total == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_estados_visibles_y_vencida_sin_tarea(db, empresa_sembrada):
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    por_cobrar = await _factura_emitida(db, empresa_sembrada)
    vencida = await _factura_emitida(db, empresa_sembrada, fecha=HOY - timedelta(days=40),
                                     fecha_vencimiento=HOY - timedelta(days=10))
    cobrada = await _factura_emitida(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, cobrada, PagoVentaEntrada(
        monto=cobrada.saldo, cuenta_bancaria_id=cta.id), "prueba")

    filas, _ = await _listar_facturas(db)
    vistos = {f.id: (f.estado_visible, f.vencida) for f in filas}
    assert vistos == {por_cobrar.id: ("Por cobrar", False), vencida.id: ("Por cobrar", True),
                      cobrada.id: ("Cobrada", False)}
    # El estado guardado de la vencida no cambió: se calcula al consultar.
    assert vencida.estado == EstadoFactura.EMITIDA
    assert {f.id for f in (await _listar_facturas(db, estado="vencida"))[0]} == {vencida.id}
    assert {f.id for f in (await _listar_facturas(db, estado="cobrada"))[0]} == {cobrada.id}
    assert {f.id for f in (await _listar_facturas(db, estado="por_cobrar"))[0]} == {
        por_cobrar.id, vencida.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_listado_con_volumen_grande(db, empresa_sembrada):
    """«Volumen grande»: 20.000 facturas; se trae una página y el total sale de la base."""
    await db.execute(text(
        "INSERT INTO erp_facturas_cliente (empresa_id, numero, cliente_nombre, fecha, "
        "fecha_vencimiento, subtotal, descuento, total_impuestos, total, saldo, estado, "
        "tipo_documento, activo, created_at, updated_at) "
        "SELECT :e, 'VOL-' || g, 'Cliente ' || g, current_date - (g % 365), current_date, "
        "100, 0, 19, 119, 119, 'EMITIDA', 'FACTURA_VENTA', true, now(), now() "
        "FROM generate_series(1, 20000) g"), {"e": empresa_sembrada.id})
    await db.flush()
    inicio = time.perf_counter()
    filas, total = await _listar_facturas(db)
    tardo = time.perf_counter() - inicio
    assert total == 20000 and len(filas) == 50
    assert filas[0].fecha >= filas[-1].fecha
    assert tardo < 3, f"el listado tardó {tardo:.2f} s"


@pytest.mark.asyncio(loop_scope="session")
async def test_detalle_trazable(db, empresa_sembrada):
    """«Historial trazable»: pago, nota y retención vinculados al documento."""
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=100000, cuenta_bancaria_id=cta.id), "prueba")
    db.add(ERPNotaCreditoCliente(empresa_id=empresa_sembrada.id, numero="NC-PR-1",
                                 factura_id=fac.id, fecha=HOY, motivo="Devolución parcial",
                                 subtotal=10000, total_impuestos=1900, total=11900,
                                 creado_por="prueba"))
    fac.retencion_fuente = Decimal("6750")
    await db.flush()

    det = await ventas_endpoints.ver_factura(fac.id, db=db, usuario=_Admin())
    assert [l.referencia for l in det.lineas] == ["SRV-1"]
    assert [p.monto for p in det.pagos] == [Decimal("100000.00")]
    assert [(n.tipo, n.numero, n.motivo) for n in det.notas] == [("CREDITO", "NC-PR-1", "Devolución parcial")]
    assert det.retencion_fuente == Decimal("6750")
    assert [e.tipo for e in det.historial] == ["EMISION", "PAGO"]
    assert all(e.usuario and e.created_at for e in det.historial)
    assert det.resolucion.prefijo == "FV"
    # Sin anticipos del cliente, «Aplicar anticipo» está deshabilitada con su motivo.
    assert det.acciones["aplicar_anticipo"]["permitida"] is False
    assert "anticipos por aplicar" in det.acciones["aplicar_anticipo"]["motivo"]
    assert det.acciones["agregar_pago"]["permitida"]
    # Con un pago ya no se edita ni se anula.
    assert not det.acciones["anular"]["permitida"]


@pytest.mark.asyncio(loop_scope="session")
async def test_accion_no_permitida_la_rechaza_el_servidor(db, empresa_sembrada):
    """«Acción ejecutada sin estar permitida»: pagar una factura cobrada."""
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=fac.saldo, cuenta_bancaria_id=cta.id), "prueba")
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.agregar_pago(
            fac.id, PagoVentaEntrada(monto=1, cuenta_bancaria_id=cta.id), db=db, usuario=_Admin())
    assert exc.value.status_code == 409 and "saldo" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_pdf_de_la_factura(db, empresa_sembrada):
    import pdfplumber

    fac = await _factura_emitida(db, empresa_sembrada, notas="Gracias por su compra",
                                 notas_adicionales="NO-IMPRIMIR")
    respuesta = await ventas_endpoints.descargar_pdf(fac.id, db=db, usuario=_Admin())
    assert respuesta.media_type == "application/pdf"
    with pdfplumber.open(io.BytesIO(respuesta.body)) as doc:
        texto = "\n".join(p.extract_text() or "" for p in doc.pages)
    plano = texto.replace("\n", "")
    for esperado in (fac.numero, "Cliente SAS", "Servicio", "321.300,00", "Pruebas SAS",
                     "Resolución DIAN R-FV", "Gracias por su compra"):
        assert esperado in texto, esperado
    assert fac.cufe in plano
    assert "NO-IMPRIMIR" not in texto


# ─── Grupo 6: ajustes ─────────────────────────────────────────────────────────

from app.core.erp_ventas import NotaEntrada, RetencionesEntrada  # noqa: E402
from app.infrastructure.models.erp import EstadoComprobante  # noqa: E402


async def _pagada_parcial(db, empresa, monto=1000):
    fac = await _factura_emitida(db, empresa)
    cta = await _cuenta_bancaria(db, empresa)
    await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=monto, cuenta_bancaria_id=cta.id), "prueba")
    return fac, cta


@pytest.mark.asyncio(loop_scope="session")
async def test_factura_libre_vuelve_a_borrador_y_se_reemite(db, empresa_sembrada):
    """«Factura libre»: asiento reversado, saldo fuera de cartera, borrador editable;
    al emitir de nuevo conserva el número y genera un asiento nuevo."""
    fac = await _factura_emitida(db, empresa_sembrada)
    original = fac.comprobante_id
    await erp_ventas.convertir_borrador(db, fac, "Se digitó mal el precio", "prueba")
    assert fac.estado == EstadoFactura.BORRADOR and fac.saldo == 0 and fac.comprobante_id is None
    assert (await db.get(ERPComprobante, original)).estado == EstadoComprobante.ANULADO

    await erp_ventas.editar_factura(db, fac, await _datos(db, lineas=[LineaVentaEntrada(
        descripcion="Servicio corregido", cantidad=1, precio_unitario=200000)]), "prueba")
    assert fac.estado == EstadoFactura.BORRADOR and fac.total == Decimal("200000.00")

    numero = fac.numero
    await erp_ventas.emitir_borrador(db, fac, "prueba")
    assert fac.numero == numero and fac.estado == EstadoFactura.EMITIDA
    assert fac.comprobante_id not in (None, original) and fac.saldo == Decimal("200000.00")
    assert await _lineas_por_cuenta(db, fac.comprobante_id) == {
        "130505": (Decimal("200000"), 0), "413500": (0, Decimal("200000"))}


@pytest.mark.asyncio(loop_scope="session")
async def test_editar_emitida_libre_reversa_y_recontabiliza(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    original, cufe = fac.comprobante_id, fac.cufe
    await erp_ventas.editar_factura(db, fac, await _datos(db, cliente_nombre="Otro nombre"), "prueba")
    assert fac.estado == EstadoFactura.EMITIDA and fac.cliente_nombre == "Otro nombre"
    assert (await db.get(ERPComprobante, original)).estado == EstadoComprobante.ANULADO
    # El CUFE no depende del nombre del cliente: con los mismos valores no cambia.
    assert fac.comprobante_id != original and fac.cufe == cufe


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("accion", ["editar", "borrador", "anular", "retenciones"])
async def test_con_pagos_se_corrige_con_nota(db, empresa_sembrada, accion):
    """«Con pagos aplicados»: editar, volver a borrador, anular y retenciones se rechazan."""
    fac, _ = await _pagada_parcial(db, empresa_sembrada)
    llamada = {
        "editar": lambda: erp_ventas.editar_factura(db, fac, _datos_editados, "prueba"),
        "borrador": lambda: erp_ventas.convertir_borrador(db, fac, "x", "prueba"),
        "anular": lambda: erp_ventas.anular(db, fac, "x", "prueba"),
        "retenciones": lambda: erp_ventas.editar_retenciones(db, fac, RetencionesEntrada(), "prueba"),
    }
    _datos_editados = await _datos(db)  # noqa: F841 (lo usa la lambda)
    with pytest.raises(HTTPException) as exc:
        await llamada[accion]()
    assert exc.value.status_code == 409 and "nota crédito o débito" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_transmitida_a_la_dian_no_se_edita(db, empresa_sembrada):
    """«Ya transmitida a la DIAN»."""
    fac = await _factura_emitida(db, empresa_sembrada)
    fac.estado_dian = "ACEPTADA"
    for operacion in (erp_ventas.editar_factura(db, fac, await _datos(db), "prueba"),
                      erp_ventas.anular(db, fac, "x", "prueba")):
        with pytest.raises(HTTPException) as exc:
            await operacion
        assert "nota" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_anulacion_libre(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    original = fac.comprobante_id
    with pytest.raises(HTTPException):
        await erp_ventas.anular(db, fac, "  ", "prueba")       # sin motivo
    await erp_ventas.anular(db, fac, "Cliente desistió", "prueba")
    assert fac.estado == EstadoFactura.ANULADA and fac.saldo == 0
    assert (await db.get(ERPComprobante, original)).estado == EstadoComprobante.ANULADO
    with pytest.raises(HTTPException):
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
            monto=1, cuenta_bancaria_id=cta.id), "prueba")
    acciones = erp_ventas.acciones_permitidas(fac, False)
    assert not acciones["editar"]["permitida"]


async def _resoluciones_de_notas(db, empresa):
    await _resolucion(db, empresa, tipo="NOTA_CREDITO", prefijo="NC")
    await _resolucion(db, empresa, tipo="NOTA_DEBITO", prefijo="ND")


@pytest.mark.asyncio(loop_scope="session")
async def test_nota_credito_parcial_y_nota_debito(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    await _resoluciones_de_notas(db, empresa_sembrada)
    nc = await erp_ventas.aplicar_nota(db, fac, "CREDITO", NotaEntrada(
        valor=100000, motivo="Descuento posterior"), "prueba")
    assert nc.numero == "NC1" and fac.saldo == Decimal("221300.00")
    assert await _lineas_por_cuenta(db, nc.comprobante_id) == {
        "130505": (0, Decimal("100000")), "427500": (Decimal("100000"), 0)}

    nd = await erp_ventas.aplicar_nota(db, fac, "DEBITO", NotaEntrada(
        valor=50000, motivo="Intereses de mora"), "prueba")
    assert nd.numero == "ND1" and fac.saldo == Decimal("271300.00")
    det = await ventas_endpoints.ver_factura(fac.id, db=db, usuario=_Admin())
    assert [n.tipo for n in det.notas] == ["CREDITO", "DEBITO"]


@pytest.mark.asyncio(loop_scope="session")
async def test_nota_credito_sobre_factura_cobrada_deja_saldo_a_favor(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=fac.saldo, cuenta_bancaria_id=cta.id), "prueba")
    await _resoluciones_de_notas(db, empresa_sembrada)
    await erp_ventas.aplicar_nota(db, fac, "CREDITO", NotaEntrada(
        valor=10000, tarifa_iva=19, motivo="Devolución"), "prueba")
    assert fac.estado == EstadoFactura.PAGADA and fac.saldo == 0
    evento = (await db.execute(select(ERPFacturaClienteEvento).where(
        ERPFacturaClienteEvento.factura_id == fac.id,
        ERPFacturaClienteEvento.tipo == "NOTA_CREDITO"))).scalar_one()
    assert evento.datos["saldo_a_favor"] == "11900.00"


@pytest.mark.asyncio(loop_scope="session")
async def test_nota_sin_motivo_o_mayor_que_lo_facturado(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    await _resoluciones_de_notas(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.aplicar_nota(db, fac, "CREDITO", NotaEntrada(valor=1000), "prueba")
    assert "motivo" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.aplicar_nota(db, fac, "CREDITO", NotaEntrada(
            valor=400000, motivo="Excesiva"), "prueba")
    assert "supera lo facturado" in exc.value.detail


async def _cliente_agente(db, empresa):
    # Las bases mínimas van en UVT, y la semilla no inventa la del año en curso.
    from app.infrastructure.models.erp_nucleo import ERPParametroFiscal
    if not (await db.execute(select(ERPParametroFiscal).where(
            ERPParametroFiscal.anio == HOY.year, ERPParametroFiscal.clave == "UVT"))).scalar():
        db.add(ERPParametroFiscal(anio=HOY.year, clave="UVT", valor=50000,
                                  descripcion="UVT de pruebas"))
    t = await _tercero(db, empresa, "800111222", "Cliente SAS", es_cliente=True)
    t.agente_retencion = True
    await db.flush()
    return t


@pytest.mark.asyncio(loop_scope="session")
async def test_retenciones_prellenadas_y_editadas(db, empresa_sembrada):
    """«Prellenado» y «Valor distinto al calculado»."""
    await _cliente_agente(db, empresa_sembrada)
    fac = await _factura_emitida(db, empresa_sembrada)
    sug = await erp_ventas.sugerir_retenciones(db, fac)
    # Servicios generales 4 % sobre 270.000 (supera 4 UVT), ReteICA 0,414 %,
    # ReteIVA 15 % del IVA.
    assert Decimal(sug["fuente"]["valor"]) == Decimal("10800.00")
    assert Decimal(sug["iva"]["valor"]) == Decimal("7695.00")
    assert all("motivo" in v for v in sug.values())

    await erp_ventas.editar_retenciones(db, fac, RetencionesEntrada(
        retencion_fuente=Decimal("6750"), retencion_ica=Decimal(sug["ica"]["valor"]),
        retencion_iva=Decimal(sug["iva"]["valor"])), "prueba")
    assert fac.retenciones_editadas is True
    retenido = Decimal("6750") + Decimal(sug["ica"]["valor"]) + Decimal("7695")
    assert fac.saldo == Decimal("321300.00") - retenido
    lineas = await _lineas_por_cuenta(db, fac.comprobante_id)
    assert lineas["130505"] == (Decimal("321300") - retenido, 0)
    assert lineas["135515"] == (Decimal("6750"), 0)
    tipos = [e.tipo for e in (await db.execute(select(ERPFacturaClienteEvento).where(
        ERPFacturaClienteEvento.factura_id == fac.id))).scalars()]
    assert "RETENCIONES" in tipos


@pytest.mark.asyncio(loop_scope="session")
async def test_cliente_no_agente_no_tiene_retenciones(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    sug = await erp_ventas.sugerir_retenciones(db, fac)
    assert {v["valor"] for v in sug.values()} == {"0.00"}
    assert "no es agente de retención" in sug["fuente"]["motivo"]


@pytest.mark.asyncio(loop_scope="session")
async def test_castigo_con_permiso(db, empresa_sembrada):
    """«Castigo con permiso»: saldo a cero contra el gasto, con motivo y autor."""
    fac, _ = await _pagada_parcial(db, empresa_sembrada, monto=21300)
    await db.commit()
    det = await ventas_endpoints.castigar(
        fac.id, ventas_endpoints.MotivoEntrada(motivo="Cliente liquidado"), db=db, usuario=_Admin())
    assert det.estado == EstadoFactura.CASTIGADA and det.saldo == 0
    assert det.cerrada_motivo == "Cliente liquidado" and det.cerrada_por == "Prueba"
    castigo = next(e for e in det.historial if e.tipo == "CASTIGO")
    assert await _lineas_por_cuenta(db, castigo.datos["comprobante_id"]) == CASTIGO_300K


@pytest.mark.asyncio(loop_scope="session")
async def test_castigo_sin_motivo_o_sobre_cobrada(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.castigar(db, fac, "", "prueba")
    assert "motivo" in exc.value.detail
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=fac.saldo, cuenta_bancaria_id=cta.id), "prueba")
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.castigar(db, fac, "x", "prueba")
    assert "no tiene saldo" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_clonar(db, empresa_sembrada):
    fac, _ = await _pagada_parcial(db, empresa_sembrada)
    clon = await erp_ventas.clonar(db, fac, "prueba")
    assert clon.estado == EstadoFactura.BORRADOR and clon.fecha == date.today()
    assert clon.numero.startswith("BORRADOR-") and clon.resolucion_id is None
    assert clon.total == fac.total and clon.saldo == 0
    assert not await erp_ventas.hay_pagos(db, clon.id)
    lineas = (await db.execute(select(ERPLineaFacturaCliente).where(
        ERPLineaFacturaCliente.factura_id == clon.id))).scalars().all()
    assert [l.referencia for l in lineas] == ["SRV-1"]
    # Al emitirse toma el siguiente número de la resolución.
    await erp_ventas.emitir_borrador(db, clon, "prueba")
    assert clon.numero == "FV2" and clon.estado == EstadoFactura.EMITIDA


@pytest.mark.asyncio(loop_scope="session")
async def test_adjuntar_a_una_anulada_y_descargar(db, empresa_sembrada, tmp_path, monkeypatch):
    from starlette.datastructures import Headers, UploadFile as UF
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    fac = await _factura_emitida(db, empresa_sembrada)
    await erp_ventas.anular(db, fac, "Error", "prueba")
    await db.commit()
    archivo = UF(io.BytesIO(b"soporte de la anulacion"), filename="../../soporte.txt",
                 headers=Headers({"content-type": "text/plain"}))
    adj = await ventas_endpoints.adjuntar(fac.id, archivo, db=db, usuario=_Admin())
    assert adj.nombre == "soporte.txt"
    guardado = await db.get(ventas_endpoints.ERPFacturaClienteAdjunto, adj.id)
    assert guardado.ruta.startswith(str(tmp_path)) and ".." not in guardado.ruta
    respuesta = await ventas_endpoints.descargar_adjunto(fac.id, adj.id, db=db, usuario=_Admin())
    assert open(respuesta.path, "rb").read() == b"soporte de la anulacion"
    with pytest.raises(HTTPException):
        await ventas_endpoints.descargar_adjunto(fac.id + 999, adj.id, db=db, usuario=_Admin())


@pytest.mark.asyncio(loop_scope="session")
async def test_cartera_del_mayor_cuadra_con_los_saldos(db, empresa_sembrada):
    """Tras pagos, notas y un castigo, la cuenta de clientes del mayor dice lo
    mismo que la suma de los saldos de las facturas."""
    await _resolucion(db, empresa_sembrada)
    await _resoluciones_de_notas(db, empresa_sembrada)
    cta = await _cuenta_bancaria(db, empresa_sembrada)
    a = await _factura_emitida(db, empresa_sembrada)
    b = await _factura_emitida(db, empresa_sembrada)
    c = await _factura_emitida(db, empresa_sembrada)
    await erp_ventas.registrar_recaudo(db, a, PagoVentaEntrada(monto=100000, cuenta_bancaria_id=cta.id), "p")
    await erp_ventas.aplicar_nota(db, a, "CREDITO", NotaEntrada(valor=20000, motivo="Rebaja"), "p")
    await erp_ventas.aplicar_nota(db, b, "DEBITO", NotaEntrada(valor=5000, motivo="Intereses"), "p")
    await erp_ventas.registrar_recaudo(db, c, PagoVentaEntrada(monto=1300, cuenta_bancaria_id=cta.id), "p")
    await erp_ventas.castigar(db, c, "Incobrable", "p")
    await db.flush()

    mayor = (await db.execute(text(
        "SELECT coalesce(sum(l.debito - l.credito), 0) FROM erp_comprobante_lineas l "
        "JOIN erp_comprobantes x ON x.id = l.comprobante_id "
        "JOIN erp_plan_cuentas pc ON pc.id = l.cuenta_id "
        "WHERE pc.codigo = '130505' AND x.empresa_id = :e"), {"e": empresa_sembrada.id})).scalar()
    saldos = sum(f.saldo for f in (a, b, c))
    assert Decimal(mayor) == saldos == Decimal("201300.00") + Decimal("326300.00")


# ─── Grupo 7: factura asistida por IA ─────────────────────────────────────────

from app.core import erp_ventas_ia  # noqa: E402

PDF = b"%PDF-1.7 factura de prueba"

LEIDA = {
    "legible": True, "cliente_tipo_identificacion": "nit", "cliente_identificacion": "800.111.222-3",
    "cliente_nombre": "Cliente Leído SAS", "cliente_email": "compras@leido.co",
    "fecha": "2026-11-10", "fecha_vencimiento": "2026-12-10", "moneda": "cop",
    "forma_pago": "contado", "medio_pago": "Bitcoin", "notas": "Gracias",
    "lineas": [
        {"referencia": "A-1", "descripcion": "Llanta 295/80", "cantidad": 4,
         "precio_unitario": 1500000, "descuento_pct": 5, "tarifa_impuesto": 19},
        {"referencia": None, "descripcion": "Montaje", "cantidad": None,
         "precio_unitario": 50000, "descuento_pct": None, "tarifa_impuesto": 8},
    ],
}


@pytest.fixture
def extractor():
    llamadas = []

    def instalar(respuesta):
        async def falso(contenido, tipo):
            llamadas.append(tipo)
            return respuesta
        erp_ventas_ia.usar_extractor_de_pruebas(falso)
        return llamadas
    yield instalar
    erp_ventas_ia.usar_extractor_de_pruebas(None)


def _archivo(nombre, contenido):
    from starlette.datastructures import Headers, UploadFile as UF
    return UF(io.BytesIO(contenido), filename=nombre, headers=Headers({"content-type": "x/x"}))


@pytest.mark.asyncio(loop_scope="session")
async def test_extraccion_exitosa(db, empresa_sembrada, extractor):
    """«Extracción exitosa» y «Datos que no detecta»."""
    llamadas = extractor(LEIDA)
    existente = await _tercero(db, empresa_sembrada, "8001112223", "Cliente Leído SAS", es_cliente=True)
    iva = await _iva19(db)
    await _catalogo(db, "FORMA_PAGO", "Contado")
    r = await ventas_endpoints.extraer_con_ia(_archivo("factura.pdf", PDF), db=db, usuario=_Admin())
    b = r["borrador"]
    assert llamadas == ["application/pdf"]
    assert (b["tercero_id"], b["cliente_identificacion"], b["cliente_tipo_identificacion"]) == (
        existente.id, "8001112223", "NIT")
    assert (b["fecha"], b["fecha_vencimiento"], b["moneda"]) == ("2026-11-10", "2026-12-10", "COP")
    assert (b["forma_pago"], b["medio_pago"], b["notas"]) == ("Contado", None, "Gracias")
    assert b["lineas"][0] == {"referencia": "A-1", "descripcion": "Llanta 295/80", "cantidad": 4,
                              "precio_unitario": 1500000, "descuento_pct": 5, "impuesto_id": iva.id}
    assert b["lineas"][1]["cantidad"] == 1 and b["lineas"][1]["impuesto_id"] is None
    # Lo interno queda para la persona.
    assert b["vendedor_id"] is None and b["centro_costo_id"] is None
    assert any("Bitcoin" in a for a in r["advertencias"])
    assert any("tarifa 8" in a for a in r["advertencias"])
    # El borrador cabe tal cual en el formulario.
    assert erp_ventas.FacturaVentaEntrada(**b).cliente_nombre == "Cliente Leído SAS"


@pytest.mark.asyncio(loop_scope="session")
async def test_ia_no_crea_terceros_ni_guarda(db, empresa_sembrada, extractor):
    """«Sin guardado automático»: ni factura ni tercero nuevos."""
    extractor(LEIDA)
    r = await ventas_endpoints.extraer_con_ia(_archivo("f.png", b"\x89PNG..."), db=db, usuario=_Admin())
    assert r["borrador"]["tercero_id"] is None
    assert any("se creará al guardar" in a for a in r["advertencias"])
    assert await _contar(db, ERPFacturaCliente) == 0 and await _contar(db, ERPTercero) == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("nombre,contenido,codigo", [
    ("factura.docx", b"PK..", 422), ("factura.pdf", b"%PDF" + b"0" * (11 * 1024 * 1024), 413)])
async def test_archivos_rechazados_sin_llamar_al_agente(db, empresa_sembrada, extractor,
                                                        nombre, contenido, codigo):
    """«Formato no permitido» y «Archivo demasiado grande»."""
    llamadas = extractor(LEIDA)
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.extraer_con_ia(_archivo(nombre, contenido), db=db, usuario=_Admin())
    assert exc.value.status_code == codigo and llamadas == []


@pytest.mark.asyncio(loop_scope="session")
async def test_archivo_ilegible(db, empresa_sembrada, extractor):
    extractor({"legible": False, "lineas": []})
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.extraer_con_ia(_archivo("f.pdf", PDF), db=db, usuario=_Admin())
    assert exc.value.status_code == 422 and "No se pudieron extraer" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_ia_no_configurada(db, empresa_sembrada, monkeypatch):
    from app.core.config import settings
    # Sin clave de ningún proveedor, aunque el .env del equipo tenga alguna:
    # si no, la prueba sale a la red y gasta una llamada real.
    monkeypatch.setattr(settings, "TITTANBOT_API_KEY", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    erp_ventas_ia.usar_extractor_de_pruebas(None)
    with pytest.raises(HTTPException) as exc:
        await ventas_endpoints.extraer_con_ia(_archivo("f.pdf", PDF), db=db, usuario=_Admin())
    assert exc.value.status_code == 503
    estado = await ventas_endpoints.estado_ia(db=db, usuario=_Admin())
    assert estado["visible"] is True and estado["disponible"] is False and estado["motivo"]


@pytest.mark.asyncio(loop_scope="session")
async def test_sin_permiso_de_asistente(db, empresa_sembrada, extractor):
    """«Sin permiso»: el botón no se muestra y la extracción se rechaza."""
    extractor(LEIDA)
    perfil = await _perfil(db, "PRUEBA_SIN_IA", {"erp": True, "fin_comprobantes": True})
    sin_ia = _Usuario("FACTURADOR", perfil.id)
    assert (await ventas_endpoints.estado_ia(db=db, usuario=sin_ia))["visible"] is False
    with pytest.raises(HTTPException) as exc:
        await erp_permisos.usar_asistente(sin_ia, db)
    assert exc.value.status_code == 403
    con = await _perfil(db, "PRUEBA_CON_IA", {"erp": True, "asistente": True})
    assert await erp_permisos.usar_asistente(_Usuario("FACTURADOR", con.id), db) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_guardar_lo_extraido_aplica_las_validaciones(db, empresa_sembrada, extractor):
    """«Guardar aplica las validaciones normales»: sin líneas válidas no se emite."""
    extractor({**LEIDA, "lineas": [{"referencia": None, "descripcion": "Sin precio", "cantidad": 1,
                                     "precio_unitario": None, "descuento_pct": None,
                                     "tarifa_impuesto": None}]})
    await _resolucion(db, empresa_sembrada)
    r = await ventas_endpoints.extraer_con_ia(_archivo("f.pdf", PDF), db=db, usuario=_Admin())
    with pytest.raises(HTTPException) as exc:
        await erp_ventas.emitir_factura(db, erp_ventas.FacturaVentaEntrada(**r["borrador"]), "prueba")
    assert "al menos un ítem" in exc.value.detail



@pytest.mark.asyncio(loop_scope="session")
async def test_firma_se_guarda_y_sale_en_el_pdf(db, empresa_sembrada, tmp_path, monkeypatch):
    import pdfplumber
    from PIL import Image
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    fac = await _factura_emitida(db, empresa_sembrada)
    await db.commit()
    imagen = io.BytesIO()
    Image.new("RGB", (120, 40), "white").save(imagen, format="PNG")
    with pytest.raises(HTTPException):
        await ventas_endpoints.firmar(fac.id, _archivo("firma.gif", b"GIF89a"), db=db, usuario=_Admin())
    det = await ventas_endpoints.firmar(fac.id, _archivo("firma.png", imagen.getvalue()),
                                        db=db, usuario=_Admin())
    assert det.tiene_firma and "FIRMA" in [e.tipo for e in det.historial]
    respuesta = await ventas_endpoints.descargar_pdf(fac.id, db=db, usuario=_Admin())
    with pdfplumber.open(io.BytesIO(respuesta.body)) as doc:
        assert sum(len(p.images) for p in doc.pages) == 1


# ─── Grupo 9: facturas que emiten otros módulos ───────────────────────────────
#
# POS, WMS 3PL y reencauche crean `ERPFacturaCliente` con sus propios
# constructores, que no conocen las columnas nuevas. Se replican sus argumentos
# tal cual (pos_servicio.py, wms_3pl.py y reencauche_logistica.py) para
# comprobar que siguen insertando y que la pantalla nueva las trata bien.

@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("origen", ["POS", "WMS_3PL", "REENCAUCHE"])
async def test_facturas_de_otros_modulos_conviven(db, empresa_sembrada, origen):
    res = await _resolucion(db, empresa_sembrada, prefijo="OT")
    numero, res = await dian.tomar_numero(db, res.id)
    estado = EstadoFactura.PAGADA if origen == "POS" else EstadoFactura.EMITIDA
    saldo = 0 if origen == "POS" else 119000
    fac = ERPFacturaCliente(
        empresa_id=empresa_sembrada.id, numero=numero, cliente_nombre="Cliente " + origen,
        cliente_nit="900111000", fecha=HOY, fecha_vencimiento=HOY + timedelta(days=30),
        subtotal=100000, total_impuestos=19000, total=119000, saldo=saldo, estado=estado,
        moneda="COP", concepto="Factura de " + origen, cufe="x" * 96, numero_electronico=numero,
        resolucion_id=res.id, origen=origen, estado_dian="POR_TRANSMITIR")
    db.add(fac)
    await db.flush()
    await db.refresh(fac)
    assert fac.tipo_documento == "FACTURA_VENTA" and fac.retencion_fuente == 0

    filas, _ = await _listar_facturas(db)
    fila = next(f for f in filas if f.id == fac.id)
    assert fila.estado_visible == ("Cobrada" if origen == "POS" else "Por cobrar")
    det = await ventas_endpoints.ver_factura(fac.id, db=db, usuario=_Admin())
    assert det.acciones["descargar_pdf"]["permitida"]
    if origen != "POS":
        cta = await _cuenta_bancaria(db, empresa_sembrada)
        await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
            monto=19000, cuenta_bancaria_id=cta.id), "prueba")
        assert fac.saldo == Decimal("100000.00")

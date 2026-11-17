"""Pruebas de Pagos recibidos (recibos de caja, anticipos y otros ingresos).

Corren contra un PostgreSQL de verdad, como `test_erp_ventas.py`, del que se
reutilizan el montaje y los ayudantes: los bloqueos `FOR UPDATE`, los
consecutivos y la migración no se comportan igual en SQLite.

Necesitan `URL_PRUEBAS` apuntando a una base DESECHABLE.
"""
import asyncio
import io
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.endpoints import erp_recibos as recibos_endpoints
from app.api.v1.endpoints import erp_ventas as ventas_endpoints
from app.core import erp_permisos, erp_recibos, erp_semilla, erp_ventas
from app.core.erp_recibos import (
    AplicacionEntrada, AplicarAnticipoEntrada, LineaOtroEntrada, ReciboEntrada, RetencionEntrada,
)
from app.core.erp_ventas import PagoVentaEntrada
from app.infrastructure.models.erp import (
    ERPComprobante, ERPCuentaBancaria, ERPFacturaCliente, ERPMovimientoBancario, ERPPago,
    EstadoComprobante, EstadoFactura, EstadoPago, TipoMovimientoBancario, TipoPago,
)
from app.infrastructure.models.erp_nucleo import ERPPeriodo, ERPReglaContable
from app.infrastructure.models.erp_recibos import ERPReciboCaja

# Montaje y ayudantes de la factura de venta. Se importan por nombre (no con
# `*`) para no volver a recolectar sus pruebas aquí.
from tests.integration.test_erp_ventas import (  # noqa: F401
    HOY, _Admin, _catalogo, _columnas, _contar, _cuenta, _cuenta_bancaria, _factura_emitida,
    _lineas_por_cuenta, _perfil, _tercero, _Usuario, db, empresa, empresa_sembrada,
    migracion_en_pruebas, motor,
)

ESQUEMA = "pruebas_pagos_recibidos"
CLIENTE_NIT = "800111222"     # el que usa `_datos` en las facturas de prueba


async def _cliente(db, empresa):
    """El tercero que `_datos` crea al emitir, o uno nuevo con ese NIT."""
    from app.infrastructure.models.erp_nucleo import ERPTercero
    t = (await db.execute(select(ERPTercero).where(
        ERPTercero.empresa_id == empresa.id,
        ERPTercero.numero_identificacion == CLIENTE_NIT))).scalar_one_or_none()
    return t or await _tercero(db, empresa, CLIENTE_NIT, "Cliente SAS", es_cliente=True)


async def _cuenta_con_banco(db, empresa):
    return await _cuenta_bancaria(db, empresa, cuenta_contable=(await _cuenta(db, empresa.id, "111005")).id)


async def _pago_a_facturas(db, empresa, cuenta, *aplicaciones, **extra) -> ERPReciboCaja:
    t = await _cliente(db, empresa)
    return await erp_recibos.crear(db, ReciboEntrada(
        tipo_ingreso="PAGO_FACTURA", tercero_id=t.id, cuenta_bancaria_id=cuenta.id, fecha=HOY,
        facturas=[AplicacionEntrada(factura_id=f.id, monto=m) for f, m in aplicaciones], **extra),
        "prueba")


async def _anticipo(db, empresa, cuenta, valor, retenciones=()) -> ERPReciboCaja:
    t = await _cliente(db, empresa)
    return await erp_recibos.crear(db, ReciboEntrada(
        tipo_ingreso="ANTICIPO", tercero_id=t.id, cuenta_bancaria_id=cuenta.id, fecha=HOY,
        valor=valor, retenciones=list(retenciones)), "prueba")


# ─── 1. Esquema, consecutivo y siembra ────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_migracion_idempotente_deja_recibos_y_columnas(motor, migracion_en_pruebas):
    await migracion_en_pruebas(ESQUEMA)
    await migracion_en_pruebas(ESQUEMA)
    assert {"recibo_id", "origen"} <= await _columnas(motor, ESQUEMA, "erp_pagos")
    for tabla in ("erp_recibos_caja", "erp_recibo_lineas_otros", "erp_recibo_retenciones",
                  "erp_recibo_comentarios"):
        assert await _columnas(motor, ESQUEMA, tabla), tabla


@pytest.mark.asyncio(loop_scope="session")
async def test_consecutivo_de_recibos_sin_repetir_y_aparte_de_comprobantes(motor, db, empresa_sembrada):
    await db.commit()
    fabrica = async_sessionmaker(motor, class_=AsyncSession, expire_on_commit=False)

    async def tomar():
        async with fabrica() as s:
            n = await erp_recibos.numero_recibo(s, empresa_sembrada.id, 2026)
            await s.commit()
            return n

    numeros = await asyncio.gather(*(tomar() for _ in range(5)))
    assert sorted(numeros) == [f"RCJ-2026-{i:06d}" for i in range(1, 6)]
    from app.core import erp_motor
    from app.infrastructure.models.erp import TipoComprobante
    assert (await erp_motor.siguiente_numero(db, empresa_sembrada.id, TipoComprobante.INGRESO, 2026)
            ).startswith("RC-2026-")


@pytest.mark.asyncio(loop_scope="session")
async def test_siembra_de_anticipos_idempotente_y_respeta_lo_editado(db, empresa_sembrada):
    regla = (await db.execute(select(ERPReglaContable).where(
        ERPReglaContable.empresa_id == empresa_sembrada.id,
        ERPReglaContable.evento == "ANTICIPO_CLIENTE",
        ERPReglaContable.papel == "anticipo"))).scalar_one()
    assert regla.cuenta_id == (await _cuenta(db, empresa_sembrada.id, "280505")).id
    otra = await _cuenta(db, empresa_sembrada.id, "238095")
    regla.cuenta_id = otra.id
    await db.flush()
    await erp_semilla.sembrar_empresa(db, empresa_sembrada.id)
    await db.flush()
    reglas = (await db.execute(select(ERPReglaContable).where(
        ERPReglaContable.empresa_id == empresa_sembrada.id,
        ERPReglaContable.evento.in_(("ANTICIPO_CLIENTE", "OTRO_INGRESO", "APLICACION_ANTICIPO"))
    ))).scalars().all()
    assert len(reglas) == len({(r.evento, r.papel) for r in reglas}) == 13
    await db.refresh(regla)
    assert regla.cuenta_id == otra.id


# ─── 2. Pago a factura ────────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_pago_total_y_abono_en_el_mismo_recibo(db, empresa_sembrada):
    a = await _factura_emitida(db, empresa_sembrada)
    b = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _pago_a_facturas(db, empresa_sembrada, cta, (a, a.saldo), (b, Decimal("21300")))
    assert rec.valor_bruto == rec.valor_neto == Decimal("342600.00")
    assert rec.estado == "APLICADO" and rec.numero.startswith(f"RCJ-{HOY.year}-")
    assert a.estado == EstadoFactura.PAGADA and a.saldo == 0
    assert b.estado == EstadoFactura.PARCIALMENTE_PAGADA and b.saldo == Decimal("300000.00")
    assert await _lineas_por_cuenta(db, rec.comprobante_id) == {
        "111005": (Decimal("342600.00"), 0), "130505": (0, Decimal("342600.00"))}
    pagos = (await db.execute(select(ERPPago).where(ERPPago.recibo_id == rec.id))).scalars().all()
    assert {p.factura_cliente_id for p in pagos} == {a.id, b.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_sobrepago_se_rechaza_sin_dejar_nada(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    antes = await _contar(db, ERPComprobante)
    with pytest.raises(HTTPException) as exc:
        await _pago_a_facturas(db, empresa_sembrada, cta, (fac, Decimal("321300.01")))
    assert "supera el saldo" in exc.value.detail and "321300.00" in exc.value.detail
    assert await _contar(db, ERPReciboCaja) == 0 and await _contar(db, ERPComprobante) == antes


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_a_factura_sin_montos_ni_cliente_se_rechaza(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await _pago_a_facturas(db, empresa_sembrada, cta, (fac, 0))
    assert "al menos una factura" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.crear(db, ReciboEntrada(
            tipo_ingreso="PAGO_FACTURA", cuenta_bancaria_id=cta.id,
            facturas=[AplicacionEntrada(factura_id=fac.id, monto=1000)]), "prueba")
    assert "tercero registrado" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_recibo_en_periodo_cerrado_no_cambia_nada(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    t = await _cliente(db, empresa_sembrada)
    db.add(ERPPeriodo(empresa_id=empresa_sembrada.id, anio=2024, mes=2, estado="CERRADO"))
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.crear(db, ReciboEntrada(
            tipo_ingreso="PAGO_FACTURA", tercero_id=t.id, cuenta_bancaria_id=cta.id,
            fecha=date(2024, 2, 10), facturas=[AplicacionEntrada(factura_id=fac.id, monto=1000)]),
            "prueba")
    assert exc.value.status_code == 422
    await db.rollback()
    await db.refresh(fac)
    await db.refresh(cta)
    assert fac.saldo == Decimal("321300.00") and Decimal(cta.saldo_contable) == 0
    assert await _contar(db, ERPReciboCaja) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_recibo_mueve_la_cuenta_bancaria(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    cta.saldo_contable = Decimal("5000000")
    await db.flush()
    rec = await _pago_a_facturas(db, empresa_sembrada, cta, (fac, Decimal("100000")))
    assert Decimal(cta.saldo_contable) == Decimal("5100000.00")
    mov = await db.get(ERPMovimientoBancario, rec.movimiento_bancario_id)
    assert (mov.tipo, mov.monto, mov.conciliado, mov.comprobante_id) == (
        TipoMovimientoBancario.CREDITO, Decimal("100000.00"), False, rec.comprobante_id)


# ─── 3. Otros ingresos y anticipos ────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_otro_ingreso_con_retencion(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    t = await _cliente(db, empresa_sembrada)
    intereses = await _cuenta(db, empresa_sembrada.id, "421005")
    rec = await erp_recibos.crear(db, ReciboEntrada(
        tipo_ingreso="OTROS", tercero_id=t.id, cuenta_bancaria_id=cta.id, fecha=HOY,
        lineas=[LineaOtroEntrada(cuenta_id=intereses.id, descripcion="Rendimientos", valor=1000000)],
        retenciones=[RetencionEntrada(tipo="RETEFUENTE", concepto="Servicios generales",
                                      porcentaje=4)]), "prueba")
    assert (rec.valor_bruto, rec.total_retenciones, rec.valor_neto) == (
        Decimal("1000000.00"), Decimal("40000.00"), Decimal("960000.00"))
    assert await _lineas_por_cuenta(db, rec.comprobante_id) == {
        "111005": (Decimal("960000.00"), 0), "135515": (Decimal("40000.00"), 0),
        "421005": (0, Decimal("1000000.00"))}


@pytest.mark.asyncio(loop_scope="session")
async def test_otro_ingreso_rechaza_cuenta_agrupadora(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    t = await _cliente(db, empresa_sembrada)
    grupo = await _cuenta(db, empresa_sembrada.id, "4210")
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.crear(db, ReciboEntrada(
            tipo_ingreso="OTROS", tercero_id=t.id, cuenta_bancaria_id=cta.id,
            lineas=[LineaOtroEntrada(cuenta_id=grupo.id, valor=1000)]), "prueba")
    assert "agrupadora" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_a_factura_no_acepta_retenciones(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    with pytest.raises(HTTPException) as exc:
        await _pago_a_facturas(db, empresa_sembrada, cta, (fac, 1000),
                               retenciones=[RetencionEntrada(tipo="RETEFUENTE", porcentaje=4)])
    assert "se registran en la factura" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_anticipo_es_pasivo_y_deja_saldo_a_favor(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _anticipo(db, empresa_sembrada, cta, 1000000)
    assert rec.estado == "POR_APLICAR" and rec.saldo_por_aplicar == Decimal("1000000.00")
    assert await _lineas_por_cuenta(db, rec.comprobante_id) == {
        "111005": (Decimal("1000000.00"), 0), "280505": (0, Decimal("1000000.00"))}
    assert await erp_recibos.saldo_a_favor(db, rec.tercero_id) == {"COP": Decimal("1000000.00")}


# ─── 4. Aplicar y desaplicar anticipos ────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_aplicacion_parcial_consumo_y_limites(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _anticipo(db, empresa_sembrada, cta, 400000)
    fac = await _factura_emitida(db, empresa_sembrada)            # saldo 321.300
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.aplicar_anticipo(db, fac, AplicarAnticipoEntrada(
            recibo_id=rec.id, monto=Decimal("321300.01")), "prueba")
    assert "hasta 321300.00" in exc.value.detail
    pago = await erp_recibos.aplicar_anticipo(db, fac, AplicarAnticipoEntrada(
        recibo_id=rec.id, monto=Decimal("321300")), "prueba")
    assert fac.estado == EstadoFactura.PAGADA and fac.saldo == 0
    assert rec.estado == "POR_APLICAR" and rec.saldo_por_aplicar == Decimal("78700.00")
    assert pago.cuenta_bancaria_id is None and pago.origen == "ANTICIPO"
    assert await _lineas_por_cuenta(db, pago.comprobante_id) == {
        "280505": (Decimal("321300.00"), 0), "130505": (0, Decimal("321300.00"))}
    # El resto, a otra factura: el anticipo queda consumido.
    otra = await _factura_emitida(db, empresa_sembrada)
    await erp_recibos.aplicar_anticipo(db, otra, AplicarAnticipoEntrada(
        recibo_id=rec.id, monto=Decimal("78700")), "prueba")
    assert rec.estado == "APLICADO" and rec.saldo_por_aplicar == 0
    # Una factura con anticipo aplicado tiene pagos: ya no se edita ni se anula.
    r = erp_ventas.acciones_permitidas(otra, await erp_ventas.hay_pagos(db, otra.id))
    assert not r["editar"]["permitida"] and not r["anular"]["permitida"]


@pytest.mark.asyncio(loop_scope="session")
async def test_desaplicar_devuelve_saldos_y_castigada_no(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _anticipo(db, empresa_sembrada, cta, 1000000)
    fac = await _factura_emitida(db, empresa_sembrada)
    pago = await erp_recibos.aplicar_anticipo(db, fac, AplicarAnticipoEntrada(
        recibo_id=rec.id, monto=fac.saldo), "prueba")
    with pytest.raises(HTTPException):
        await erp_recibos.desaplicar_anticipo(db, fac, pago.id, "", "prueba")
    await erp_recibos.desaplicar_anticipo(db, fac, pago.id, "Error de digitación", "prueba")
    assert fac.estado == EstadoFactura.EMITIDA and fac.saldo == Decimal("321300.00")
    assert rec.saldo_por_aplicar == Decimal("1000000.00") and pago.estado == EstadoPago.ANULADO
    comp = await db.get(ERPComprobante, pago.comprobante_id)
    assert comp.estado == EstadoComprobante.ANULADO
    # Sobre una factura castigada no se desaplica.
    pago2 = await erp_recibos.aplicar_anticipo(db, fac, AplicarAnticipoEntrada(
        recibo_id=rec.id, monto=Decimal("1000")), "prueba")
    await erp_ventas.castigar(db, fac, "Incobrable", "prueba")
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.desaplicar_anticipo(db, fac, pago2.id, "Revertir", "prueba")
    assert "castigada" in exc.value.detail


@pytest.mark.asyncio(loop_scope="session")
async def test_retenciones_del_anticipo_en_la_factura(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _anticipo(db, empresa_sembrada, cta, 642600, retenciones=[
        RetencionEntrada(tipo="RETEFUENTE", concepto="Servicios generales", porcentaje=4,
                         valor=40000)])
    fac = await _factura_emitida(db, empresa_sembrada)
    await erp_recibos.aplicar_anticipo(db, fac, AplicarAnticipoEntrada(
        recibo_id=rec.id, monto=Decimal("321300")), "prueba")
    rets = await erp_recibos.retenciones_anticipo(db, fac.id)
    assert [(r["tipo"], r["valor"]) for r in rets] == [("RETEFUENTE", Decimal("20000.00"))]
    assert fac.retencion_fuente == 0          # no se vuelve a contabilizar en la factura
    # Si la factura ya registra retenciones propias, pide confirmación.
    otra = await _factura_emitida(db, empresa_sembrada)
    otra.retencion_fuente = Decimal("1000")
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.aplicar_anticipo(db, otra, AplicarAnticipoEntrada(
            recibo_id=rec.id, monto=Decimal("1000")), "prueba")
    assert exc.value.status_code == 409 and "confirme" in exc.value.detail
    await erp_recibos.aplicar_anticipo(db, otra, AplicarAnticipoEntrada(
        recibo_id=rec.id, monto=Decimal("1000"), confirmar_retenciones=True), "prueba")


@pytest.mark.asyncio(loop_scope="session")
async def test_aplicar_anticipo_por_la_ruta_de_la_factura(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    await db.commit()
    detalle = await ventas_endpoints.ver_factura(fac.id, db=db, usuario=_Admin())
    assert not detalle.acciones["aplicar_anticipo"]["permitida"]
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _anticipo(db, empresa_sembrada, cta, 500000)
    await db.commit()
    detalle = await ventas_endpoints.ver_factura(fac.id, db=db, usuario=_Admin())
    assert detalle.acciones["aplicar_anticipo"]["permitida"]
    disponibles = await ventas_endpoints.anticipos_de_la_factura(fac.id, db=db, usuario=_Admin())
    assert [a.id for a in disponibles] == [rec.id]
    detalle = await ventas_endpoints.aplicar_anticipo(
        fac.id, AplicarAnticipoEntrada(recibo_id=rec.id, monto=Decimal("100000")),
        db=db, usuario=_Admin())
    assert detalle.saldo == Decimal("221300.00")
    assert [(p.origen, p.recibo_id) for p in detalle.pagos] == [("ANTICIPO", rec.id)]


# ─── 5. Anulación ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_anular_pago_a_factura_devuelve_todo(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _pago_a_facturas(db, empresa_sembrada, cta, (fac, fac.saldo))
    assert fac.estado == EstadoFactura.PAGADA
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.anular(db, rec.id, "  ", "prueba")
    assert "motivo" in exc.value.detail
    await erp_recibos.anular(db, rec.id, "Consignación rechazada", "prueba")
    assert rec.estado == "ANULADO"
    assert fac.estado == EstadoFactura.EMITIDA and fac.saldo == Decimal("321300.00")
    assert Decimal(cta.saldo_contable) == 0
    movs = (await db.execute(select(ERPMovimientoBancario).where(
        ERPMovimientoBancario.cuenta_id == cta.id))).scalars().all()
    assert sorted(m.tipo.value for m in movs) == ["CREDITO", "DEBITO"]
    comp = await db.get(ERPComprobante, rec.comprobante_id)
    assert comp.estado == EstadoComprobante.ANULADO
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.anular(db, rec.id, "Otra vez", "prueba")
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_anular_rechaza_factura_castigada_y_anticipo_aplicado(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    rec = await _pago_a_facturas(db, empresa_sembrada, cta, (fac, Decimal("1000")))
    await erp_ventas.castigar(db, fac, "Incobrable", "prueba")
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.anular(db, rec.id, "Error", "prueba")
    assert "castigada" in exc.value.detail

    ant = await _anticipo(db, empresa_sembrada, cta, 500000)
    otra = await _factura_emitida(db, empresa_sembrada)
    await erp_recibos.aplicar_anticipo(db, otra, AplicarAnticipoEntrada(
        recibo_id=ant.id, monto=Decimal("1000")), "prueba")
    with pytest.raises(HTTPException) as exc:
        await erp_recibos.anular(db, ant.id, "Error", "prueba")
    assert "desaplíquelo" in exc.value.detail


# ─── 6. Facturas por cobrar, pago rápido e históricos ─────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_facturas_por_cobrar_incluye_antiguas_por_nit_y_filtra_moneda(db, empresa_sembrada):
    t = await _cliente(db, empresa_sembrada)
    t.digito_verificacion = "7"
    actual = await _factura_emitida(db, empresa_sembrada)
    antigua = await _factura_emitida(db, empresa_sembrada)
    antigua.tercero_id, antigua.cliente_nit = None, "800.111.222-7"
    en_usd = await _factura_emitida(db, empresa_sembrada)
    en_usd.moneda = "USD"
    await db.flush()
    ids = {f.id for f in await erp_recibos.facturas_por_cobrar(db, empresa_sembrada.id, t, "COP")}
    assert {actual.id, antigua.id} <= ids and en_usd.id not in ids
    # Al pagarle, la antigua queda vinculada al tercero.
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    await _pago_a_facturas(db, empresa_sembrada, cta, (antigua, Decimal("1000")))
    assert antigua.tercero_id == t.id


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_rapido_crea_recibo_y_aparece_en_el_listado(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    await db.commit()
    pago = await ventas_endpoints.agregar_pago(
        fac.id, PagoVentaEntrada(monto=200000, cuenta_bancaria_id=cta.id), db=db, usuario=_Admin())
    rec = await db.get(ERPReciboCaja, pago.recibo_id)
    assert rec.numero == pago.numero and rec.numero.startswith("RCJ-")
    filas = await recibos_endpoints.listar(_Respuesta(), db=db, usuario=_Admin(), **_SIN_FILTROS)
    assert [f.numero for f in filas] == [rec.numero] and filas[0].detalle == f"Factura {fac.numero}"


@pytest.mark.asyncio(loop_scope="session")
async def test_pago_de_factura_de_consumidor_final(db, empresa_sembrada):
    """Una factura sin tercero (consumidor final) se sigue pudiendo cobrar desde
    la factura; el recibo toma el nombre de la factura."""
    fac = await _factura_emitida(db, empresa_sembrada, cliente_identificacion=None,
                                 cliente_nombre="Consumidor final")
    assert fac.tercero_id is None
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    pago = await erp_ventas.registrar_recaudo(db, fac, PagoVentaEntrada(
        monto=fac.saldo, cuenta_bancaria_id=cta.id), "prueba")
    rec = await db.get(ERPReciboCaja, pago.recibo_id)
    assert rec.tercero_id is None and rec.cliente_nombre == "Consumidor final"
    assert fac.estado == EstadoFactura.PAGADA


@pytest.mark.asyncio(loop_scope="session")
async def test_cobros_anteriores_quedan_con_su_recibo(db, empresa_sembrada):
    fac = await _factura_emitida(db, empresa_sembrada)
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    for i, estado in enumerate((EstadoPago.PROCESADO, EstadoPago.ANULADO), start=1):
        db.add(ERPPago(empresa_id=empresa_sembrada.id, tipo=TipoPago.COBRO, numero=f"RC-{fac.numero}-{i}",
                       fecha=HOY, factura_cliente_id=fac.id, cuenta_bancaria_id=cta.id,
                       monto=1000, estado=estado))
    await db.flush()
    consecutivos = (await db.execute(text("SELECT count(*) FROM erp_consecutivos"))).scalar()
    assert await erp_recibos.agrupar_historicos(db) == 2
    assert await erp_recibos.agrupar_historicos(db) == 0
    recibos = (await db.execute(select(ERPReciboCaja).order_by(ERPReciboCaja.numero))).scalars().all()
    assert [(r.numero, r.estado, r.historico, r.tercero_id is not None) for r in recibos] == [
        (f"RC-{fac.numero}-1", "APLICADO", True, True), (f"RC-{fac.numero}-2", "ANULADO", True, True)]
    assert (await db.execute(text("SELECT count(*) FROM erp_consecutivos"))).scalar() == consecutivos


# ─── 7. Rutas: listado, permisos y PDF ────────────────────────────────────────

class _Respuesta:
    def __init__(self):
        self.headers = {}


_SIN_FILTROS = dict(identificacion=None, cliente=None, desde=None, hasta=None, cuenta_bancaria_id=None,
                    estado=None, tipo_ingreso=None, monto_min=None, monto_max=None, limite=50,
                    desplazamiento=0)


@pytest.mark.asyncio(loop_scope="session")
async def test_listado_con_filtros_combinados_y_vacio(db, empresa_sembrada):
    cta = await _cuenta_con_banco(db, empresa_sembrada)
    otra = await _cuenta_bancaria(db, empresa_sembrada)
    a = await _anticipo(db, empresa_sembrada, cta, 300000)
    await _anticipo(db, empresa_sembrada, otra, 300000)
    fac = await _factura_emitida(db, empresa_sembrada)
    await _pago_a_facturas(db, empresa_sembrada, cta, (fac, Decimal("50000")))
    await db.commit()

    async def listar(**filtros):
        r = _Respuesta()
        filas = await recibos_endpoints.listar(r, db=db, usuario=_Admin(), **{**_SIN_FILTROS, **filtros})
        return [f.id for f in filas], r.headers.get("X-Total-Count")

    ids, total = await listar(cuenta_bancaria_id=cta.id, estado="POR_APLICAR")
    assert ids == [a.id] and total == "1"
    ids, _ = await listar(monto_min=Decimal("100000"), monto_max=Decimal("300000"))
    assert len(ids) == 2
    ids, _ = await listar(identificacion="800.111.222", cliente="cliente")
    assert len(ids) == 3
    ids, total = await listar(cliente="No existe")
    assert ids == [] and total == "0"


@pytest.mark.asyncio(loop_scope="session")
async def test_crear_recibo_exige_permiso_de_pagos(db):
    con = await _perfil(db, "PRUEBA_CON_PAGOS", {"erp": True, "fin_pagos": True})
    sin = await _perfil(db, "PRUEBA_SOLO_CARTERA", {"erp": True, "fin_cartera": True})
    assert await erp_permisos.registrar_pagos(_Usuario("CARTERA", con.id), db) is not None
    with pytest.raises(HTTPException) as exc:
        await erp_permisos.registrar_pagos(_Usuario("CARTERA", sin.id), db)
    assert exc.value.status_code == 403
    assert await erp_permisos.ver_cartera(_Usuario("CARTERA", sin.id), db) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_pdf_lleva_notas_y_no_comentarios(db, empresa_sembrada):
    import pdfplumber

    cta = await _cuenta_con_banco(db, empresa_sembrada)
    t = await _cliente(db, empresa_sembrada)
    rec = await erp_recibos.crear(db, ReciboEntrada(
        tipo_ingreso="ANTICIPO", tercero_id=t.id, cuenta_bancaria_id=cta.id, fecha=HOY, valor=250000,
        notas_adicionales="Pago por transferencia ref. 8891",
        comentario="Llamar al cliente el lunes"), "prueba")
    await db.commit()
    await recibos_endpoints.comentar(rec.id, recibos_endpoints.ComentarioEntrada(texto="Otro interno"),
                                     db=db, usuario=_Admin())
    respuesta = await recibos_endpoints.descargar_pdf(rec.id, db=db, usuario=_Admin())
    with pdfplumber.open(io.BytesIO(respuesta.body)) as doc:
        contenido = "\n".join(p.extract_text() or "" for p in doc.pages)
    assert rec.numero in contenido and "ref. 8891" in contenido
    assert "Llamar al cliente" not in contenido and "Otro interno" not in contenido
    detalle = await recibos_endpoints.ver(rec.id, db=db, usuario=_Admin())
    assert [c.texto for c in detalle.comentarios] == ["Llamar al cliente el lunes", "Otro interno"]

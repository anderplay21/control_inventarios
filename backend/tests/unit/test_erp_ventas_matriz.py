"""Matriz de acciones por estado de la factura de venta (core/erp_ventas.py)."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.erp_ventas import ACCIONES, acciones_permitidas, estado_visible
from app.infrastructure.models.erp import EstadoFactura as E

HOY = date(2026, 11, 15)


def _fac(estado, saldo="1000", dian="POR_TRANSMITIR", vence=HOY + timedelta(days=10)):
    return SimpleNamespace(estado=estado, saldo=Decimal(saldo), estado_dian=dian,
                           fecha_vencimiento=vence)


SIEMPRE = {"imprimir", "descargar_pdf", "compartir", "clonar", "adjuntar"}
DIFERIDAS = {"volver_recurrente"}

# Columnas de la tabla del diseño: (factura, hay_pagos) → acciones permitidas,
# además de SIEMPRE.
CASOS = {
    "borrador": (_fac(E.BORRADOR), False, {"editar"}),
    "emitida libre": (_fac(E.EMITIDA), False,
                      {"agregar_pago", "cerrar_sin_pago", "nota_credito", "nota_debito",
                       "editar_retenciones", "editar", "convertir_borrador", "anular"}),
    "emitida con pagos": (_fac(E.PARCIALMENTE_PAGADA), True,
                          {"agregar_pago", "cerrar_sin_pago", "nota_credito", "nota_debito"}),
    "emitida transmitida": (_fac(E.EMITIDA, dian="ACEPTADA"), False,
                            {"agregar_pago", "cerrar_sin_pago", "nota_credito", "nota_debito"}),
    "cobrada": (_fac(E.PAGADA, saldo="0"), True, {"nota_credito", "nota_debito"}),
    "anulada": (_fac(E.ANULADA), False, set()),
    "castigada": (_fac(E.CASTIGADA, saldo="0"), False, set()),
}


@pytest.mark.parametrize("nombre", list(CASOS))
def test_cada_columna_de_la_matriz(nombre):
    fac, pagos, propias = CASOS[nombre]
    r = acciones_permitidas(fac, pagos)
    assert set(r) == set(ACCIONES)
    permitidas = {a for a, v in r.items() if v["permitida"]}
    assert permitidas == SIEMPRE | propias
    # Toda acción negada explica por qué.
    assert all(v["motivo"] for a, v in r.items() if not v["permitida"])


def test_factura_cobrada_segun_el_escenario():
    r = acciones_permitidas(_fac(E.PAGADA, saldo="0"), True)
    for a in ("agregar_pago", "aplicar_anticipo", "cerrar_sin_pago", "editar", "anular"):
        assert not r[a]["permitida"], a
    for a in ("imprimir", "descargar_pdf", "clonar", "adjuntar"):
        assert r[a]["permitida"], a


def test_diferidas_siempre_deshabilitadas_con_proximamente():
    for nombre, (fac, pagos, _) in CASOS.items():
        r = acciones_permitidas(fac, pagos)
        for a in DIFERIDAS:
            assert not r[a]["permitida"] and "Próximamente" in r[a]["motivo"], (nombre, a)


def test_aplicar_anticipo_segun_saldo_a_favor():
    """Habilitada solo si la factura es cobrable y el cliente tiene anticipos en
    su moneda; si no, deshabilitada con el motivo."""
    con = acciones_permitidas(_fac(E.EMITIDA), False, hay_anticipos=True)["aplicar_anticipo"]
    sin = acciones_permitidas(_fac(E.EMITIDA), False, hay_anticipos=False)["aplicar_anticipo"]
    cobrada = acciones_permitidas(_fac(E.PAGADA, saldo="0"), True, hay_anticipos=True)["aplicar_anticipo"]
    anulada = acciones_permitidas(_fac(E.ANULADA), False, hay_anticipos=True)["aplicar_anticipo"]
    assert con["permitida"] and con["motivo"] is None
    assert not sin["permitida"] and "anticipos" in sin["motivo"]
    assert not cobrada["permitida"] and "saldo" in cobrada["motivo"]
    assert not anulada["permitida"] and "anulada" in anulada["motivo"]


def test_anticipo_aplicado_cuenta_como_pago():
    """Con un anticipo aplicado (hay pagos) la factura ya no se edita ni se anula."""
    r = acciones_permitidas(_fac(E.PARCIALMENTE_PAGADA), True, hay_anticipos=True)
    for a in ("editar", "convertir_borrador", "anular", "editar_retenciones"):
        assert not r[a]["permitida"] and "pagos" in r[a]["motivo"], a


@pytest.mark.parametrize("estado,saldo,vence,texto,vencida", [
    (E.EMITIDA, "1000", HOY + timedelta(days=1), "Por cobrar", False),
    (E.PARCIALMENTE_PAGADA, "10", HOY - timedelta(days=1), "Por cobrar", True),
    (E.PAGADA, "0", HOY - timedelta(days=30), "Cobrada", False),
    (E.ANULADA, "1000", HOY - timedelta(days=30), "Anulada", False),
    (E.BORRADOR, "1000", HOY - timedelta(days=30), "Borrador", False),
    (E.CASTIGADA, "0", HOY - timedelta(days=30), "Castigada", False),
])
def test_estado_visible(estado, saldo, vence, texto, vencida):
    assert estado_visible(_fac(estado, saldo=saldo, vence=vence), HOY) == {
        "estado_visible": texto, "vencida": vencida}

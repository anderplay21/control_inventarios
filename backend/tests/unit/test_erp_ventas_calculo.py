"""Cálculo de líneas y totales de la factura de venta (core/erp_ventas.py)."""
from decimal import Decimal

import pytest

from app.core.erp_ventas import calcular_linea, calcular_totales, linea_valida, saldo_de


def test_linea_del_escenario():
    """«Línea calculada»: 3 × 100.000, 10 % de descuento, IVA 19 %."""
    ln = calcular_linea(3, 100000, 10, 19)
    assert ln.bruto == Decimal("300000.00")
    assert ln.descuento == Decimal("30000.00")
    assert ln.subtotal == Decimal("270000.00")
    assert ln.impuesto == Decimal("51300.00")
    assert ln.total == Decimal("321300.00")


def test_totales_suman_exactamente_las_lineas():
    """«Totales coinciden»: el documento es la suma de lo que se mostró por línea."""
    lineas = [calcular_linea(3, 100000, 10, 19), calcular_linea(1, 33333.33, 0, 5),
              calcular_linea(7, 1234.567, 2.5, 19), calcular_linea(2, 50000, 0, 0)]
    t = calcular_totales(lineas)
    assert t.subtotal == sum(l.subtotal for l in lineas)
    assert t.impuestos == sum(l.impuesto for l in lineas)
    assert t.total == sum(l.total for l in lineas)
    assert t.bruto - t.descuento == t.subtotal
    assert t.subtotal + t.impuestos == t.total
    # Todo queda en centavos.
    assert all(v == v.quantize(Decimal("0.01")) for v in (t.subtotal, t.impuestos, t.total))


def test_redondeo_mitad_hacia_arriba():
    # 0.005 de impuesto se redondea a 0.01, no a 0.00.
    assert calcular_linea(1, Decimal("0.05"), 0, 10).impuesto == Decimal("0.01")


@pytest.mark.parametrize("cantidad,precio,pct,tarifa", [
    (-1, 100, 0, 19), (1, -100, 0, 19), (1, 100, 101, 19), (1, 100, -1, 19), (1, 100, 0, -5),
])
def test_valores_imposibles_se_rechazan(cantidad, precio, pct, tarifa):
    with pytest.raises(ValueError):
        calcular_linea(cantidad, precio, pct, tarifa)


@pytest.mark.parametrize("cantidad,precio,valida", [
    (1, 100, True), (0, 100, False), (1, 0, False), (None, 100, False), (1, None, False),
])
def test_linea_valida(cantidad, precio, valida):
    assert linea_valida(cantidad, precio) is valida


def test_saldo_descuenta_retenciones_pagos_y_notas():
    assert saldo_de(1190000, retenciones=25000, pagos=500000,
                    notas_credito=100000, notas_debito=50000) == Decimal("615000.00")

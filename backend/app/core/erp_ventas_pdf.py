"""
PDF de la factura de venta del ERP.

Lleva lo que el cliente y la DIAN esperan ver: emisor, número, cliente, líneas,
totales, retenciones, la resolución que autoriza la numeración y el CUFE. Las
notas internas no se imprimen; las notas del documento sí.
"""
from decimal import Decimal

from fpdf import FPDF


def _t(texto) -> str:
    """Helvetica solo trae Latin-1: lo que no quepa se reemplaza en vez de fallar."""
    return str(texto if texto is not None else "").encode("latin-1", "replace").decode("latin-1")


def _plata(valor, moneda="COP") -> str:
    v = Decimal(str(valor or 0)).quantize(Decimal("0.01"))
    entero, dec = f"{v:,.2f}".split(".")
    return f"{moneda} {entero.replace(',', '.')},{dec}"


def pdf_factura(det, empresa, firma: str = None) -> bytes:
    """`det` es el detalle de la factura (FacturaDetalle); `empresa`, el emisor;
    `firma`, la ruta de la imagen de la firma si la hay."""
    mon = det.moneda or "COP"
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    titulo = ("DOCUMENTO EQUIVALENTE POS" if det.tipo_documento == "TICKET"
              else "FACTURA ELECTRÓNICA DE VENTA")
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, _t(empresa.razon_social), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _t(f"NIT {empresa.nit}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, _t(f"{titulo} N.º {det.numero}"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    for etiqueta, valor in (
        ("Fecha", det.fecha.isoformat()), ("Vence", det.fecha_vencimiento.isoformat()),
        ("Cliente", det.cliente_nombre), ("Identificación", det.cliente_nit or "Consumidor final"),
        ("Correo", det.cliente_email or ""), ("Forma de pago", det.forma_pago or ""),
        ("Medio de pago", det.medio_pago or ""), ("Remisión", det.remision or ""),
    ):
        if valor:
            pdf.cell(35, 5, _t(etiqueta + ":"))
            pdf.cell(0, 5, _t(valor), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Líneas
    anchos = (25, 70, 15, 25, 15, 40)
    pdf.set_font("Helvetica", "B", 8)
    for ancho, titulo_col in zip(anchos, ("Referencia", "Descripción", "Cant.", "Precio", "Desc.%", "Total")):
        pdf.cell(ancho, 6, _t(titulo_col), border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for ln in det.lineas:
        valores = (ln.referencia or "", ln.descripcion[:60], f"{Decimal(ln.cantidad):g}",
                   _plata(ln.precio_unitario, ""), f"{Decimal(ln.descuento_pct or 0):g}",
                   _plata(ln.total, mon))
        for ancho, v in zip(anchos, valores):
            pdf.cell(ancho, 6, _t(v), border=1)
        pdf.ln()
    pdf.ln(2)

    # Totales
    retenciones = Decimal(det.retencion_fuente) + Decimal(det.retencion_ica) + Decimal(det.retencion_iva)
    filas = [("Subtotal", det.subtotal + det.descuento), ("Descuentos", det.descuento),
             ("Impuestos", det.total_impuestos), ("Total", det.total)]
    if retenciones:
        filas += [("Retenciones", retenciones), ("Neto a pagar", det.total - retenciones)]
    pdf.set_font("Helvetica", "", 9)
    for etiqueta, valor in filas:
        pdf.cell(150, 5, _t(etiqueta), align="R")
        pdf.cell(40, 5, _t(_plata(valor, mon)), align="R", new_x="LMARGIN", new_y="NEXT")

    if det.observaciones:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "Notas", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, _t(det.observaciones), new_x="LMARGIN", new_y="NEXT")

    if firma:
        import os
        if os.path.isfile(firma):
            pdf.ln(3)
            pdf.image(firma, w=45)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(0, 4, "Firma", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "", 7)
    if det.resolucion:
        r = det.resolucion
        pdf.multi_cell(0, 4, _t(
            f"Resolución DIAN {r.numero_resolucion} del {r.fecha_resolucion.isoformat()}, "
            f"numeración {r.prefijo}{r.desde} a {r.prefijo}{r.hasta}, vigente del "
            f"{r.vigencia_desde.isoformat()} al {r.vigencia_hasta.isoformat()}."),
            new_x="LMARGIN", new_y="NEXT")
    if det.cufe:
        pdf.multi_cell(0, 4, _t(f"{'CUDE' if det.tipo_documento == 'TICKET' else 'CUFE'}: {det.cufe}"),
                       new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())

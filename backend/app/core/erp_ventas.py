"""
Factura de venta del ERP (Ingresos · Factura de venta).

Un solo lugar para las reglas de la factura de venta que crea el usuario de
Finanzas: cómo se calcula, cómo se numera, qué se puede hacer con ella según su
estado y cómo se cobra o se corrige. Los endpoints solo traducen HTTP a estas
funciones; así las mismas reglas valen para la pantalla, para el `POST` viejo de
cartera y para cualquier otro llamador.

No importa nada del WMS, del POS ni de reencauche: el ERP tiene que funcionar
solo. Esos módulos siguen facturando con sus propios flujos sobre la misma
resolución.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Sequence

CENTAVO = Decimal("0.01")


def r2(valor) -> Decimal:
    """Redondeo a centavos, mitad hacia arriba, como lo exige la DIAN."""
    return Decimal(str(valor or 0)).quantize(CENTAVO, ROUND_HALF_UP)


# ─── Cálculo ──────────────────────────────────────────────────────────────────

@dataclass
class LineaCalculada:
    bruto: Decimal          # cantidad × precio
    descuento: Decimal      # valor del descuento
    subtotal: Decimal       # base gravable: bruto − descuento
    impuesto: Decimal
    total: Decimal          # subtotal + impuesto


@dataclass
class TotalesDocumento:
    """Los totales del documento.

    `subtotal` es la base, ya sin descuentos: es lo que se guarda en la factura y
    lo que va al ingreso. Para mostrar «Subtotal − Descuentos + Impuestos =
    Total» la pantalla usa `bruto`.
    """
    bruto: Decimal = Decimal(0)
    descuento: Decimal = Decimal(0)
    subtotal: Decimal = Decimal(0)
    impuestos: Decimal = Decimal(0)
    total: Decimal = Decimal(0)
    lineas: List[LineaCalculada] = field(default_factory=list)


def calcular_linea(cantidad, precio_unitario, descuento_pct=0, tarifa_impuesto=0) -> LineaCalculada:
    """Una línea: cada valor se redondea a centavos al calcularlo, para que la
    suma de las líneas sea exactamente el total del documento."""
    cantidad, precio = Decimal(str(cantidad or 0)), Decimal(str(precio_unitario or 0))
    pct, tarifa = Decimal(str(descuento_pct or 0)), Decimal(str(tarifa_impuesto or 0))
    if cantidad < 0 or precio < 0:
        raise ValueError("La cantidad y el precio no pueden ser negativos.")
    if not (0 <= pct <= 100):
        raise ValueError("El descuento debe estar entre 0 % y 100 %.")
    if tarifa < 0:
        raise ValueError("La tarifa del impuesto no puede ser negativa.")
    bruto = r2(cantidad * precio)
    descuento = r2(bruto * pct / 100)
    subtotal = bruto - descuento
    impuesto = r2(subtotal * tarifa / 100)
    return LineaCalculada(bruto=bruto, descuento=descuento, subtotal=subtotal,
                          impuesto=impuesto, total=subtotal + impuesto)


def calcular_totales(lineas: Sequence[LineaCalculada]) -> TotalesDocumento:
    t = TotalesDocumento(lineas=list(lineas))
    for ln in lineas:
        t.bruto += ln.bruto
        t.descuento += ln.descuento
        t.subtotal += ln.subtotal
        t.impuestos += ln.impuesto
        t.total += ln.total
    return t


def linea_valida(cantidad, precio_unitario) -> bool:
    """Una línea cuenta para «al menos un ítem válido» si vende algo con precio."""
    try:
        return Decimal(str(cantidad or 0)) > 0 and Decimal(str(precio_unitario or 0)) > 0
    except ArithmeticError:
        return False


def saldo_de(total, retenciones=0, pagos=0, notas_credito=0, notas_debito=0) -> Decimal:
    """Lo que el cliente todavía debe. Se calcula en un solo lugar (decisión 4)."""
    return r2(Decimal(str(total or 0)) - Decimal(str(retenciones or 0))
              - Decimal(str(pagos or 0)) - Decimal(str(notas_credito or 0))
              + Decimal(str(notas_debito or 0)))


# ─── Emisión ──────────────────────────────────────────────────────────────────

import re  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402
from typing import Literal  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core import erp_motor  # noqa: E402
from app.core import facturacion_dian as dian  # noqa: E402
from app.infrastructure.models.erp import (  # noqa: E402
    ERPCentroCosto, ERPConfigGeneral, ERPEmpresa, ERPFacturaCliente,
    ERPLineaFacturaCliente, ERPTipoImpuesto, EstadoFactura, TipoComprobante,
)
from app.infrastructure.models.erp_facturacion import ERPFacturaClienteEvento  # noqa: E402
from app.infrastructure.models.erp_nucleo import ERPTercero  # noqa: E402

# Tipo de documento de la factura → tipo de la resolución que lo numera.
RESOLUCION_DE = {"FACTURA_VENTA": "FACTURA_VENTA", "TICKET": "POS"}

_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LineaVentaEntrada(BaseModel):
    referencia: Optional[str] = Field(None, max_length=100)
    descripcion: str = Field("", max_length=500)
    cantidad: Decimal = Decimal(1)
    precio_unitario: Decimal = Decimal(0)
    descuento_pct: Decimal = Decimal(0)
    impuesto_id: Optional[int] = None


class FacturaVentaEntrada(BaseModel):
    """Lo que manda el formulario de nueva factura (y lo que se arma desde el
    `POST` viejo de cartera)."""
    empresa_id: Optional[int] = None
    tipo_documento: Literal["FACTURA_VENTA", "TICKET"] = "FACTURA_VENTA"
    fecha: date = Field(default_factory=date.today)
    fecha_vencimiento: Optional[date] = None
    moneda: str = "COP"
    centro_costo_id: Optional[int] = None
    proyecto_id: Optional[int] = None
    vendedor_id: Optional[int] = None
    forma_pago: Optional[str] = None
    medio_pago: Optional[str] = None
    # Cliente
    tercero_id: Optional[int] = None
    cliente_tipo_identificacion: str = "NIT"
    cliente_identificacion: Optional[str] = Field(None, max_length=30)
    cliente_nombre: str = Field("", max_length=300)
    cliente_email: Optional[str] = Field(None, max_length=200)
    # Contenido
    lineas: List[LineaVentaEntrada] = []
    remision: Optional[str] = Field(None, max_length=100)
    concepto: Optional[str] = Field(None, max_length=500)
    notas: Optional[str] = None                 # se imprimen en el documento
    notas_adicionales: Optional[str] = None     # internas
    firma: Optional[str] = Field(None, max_length=500)
    comentario: Optional[str] = None            # comentario interno inicial
    # Pago registrado al crear (factura de contado). Factura y recaudo van
    # juntos: si el pago es inválido, la factura tampoco se guarda.
    pago: Optional["PagoVentaEntrada"] = None

    @field_validator("moneda")
    @classmethod
    def _moneda(cls, v: str) -> str:
        v = (v or "COP").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("La moneda va con su código ISO de tres letras, por ejemplo COP.")
        return v


def _rechazo(mensaje: str, codigo: int = 422) -> HTTPException:
    return HTTPException(codigo, mensaje)


async def empresa_de(db: AsyncSession, empresa_id: Optional[int]) -> ERPEmpresa:
    """La empresa del documento, o la primera si la pantalla no la manda (las
    pantallas de cartera no tienen selector de empresa)."""
    emp = (await db.get(ERPEmpresa, empresa_id) if empresa_id else
           (await db.execute(select(ERPEmpresa).order_by(ERPEmpresa.id).limit(1))).scalar())
    if emp is None:
        raise _rechazo("Primero hay que crear la empresa en Finanzas → Empresas.")
    return emp


async def historial(db: AsyncSession, factura_id: int, tipo: str, usuario: str,
                    detalle: Optional[str] = None, datos: Optional[dict] = None) -> None:
    db.add(ERPFacturaClienteEvento(factura_id=factura_id, tipo=tipo, usuario=usuario,
                                   detalle=detalle, datos=datos))


async def valor_catalogo(db, tipo: str, valor: Optional[str], etiqueta: str) -> Optional[str]:
    """Valida un valor contra el catálogo ERP/<tipo> y lo devuelve normalizado."""
    if not valor:
        return None
    # Import tardío: el resolvedor vive con los endpoints de catálogos.
    from app.api.v1.endpoints.catalogos import resolver_valor_catalogo
    try:
        return await resolver_valor_catalogo(db, "ERP", tipo, valor, etiqueta=etiqueta)
    except ValueError as e:
        raise _rechazo(str(e))


async def _tarifas(db, lineas: Sequence[LineaVentaEntrada]) -> dict:
    ids = {l.impuesto_id for l in lineas if l.impuesto_id}
    if not ids:
        return {}
    filas = (await db.execute(select(ERPTipoImpuesto).where(ERPTipoImpuesto.id.in_(ids)))).scalars().all()
    tarifas = {t.id: t.porcentaje for t in filas}
    faltan = ids - set(tarifas)
    if faltan:
        raise _rechazo(f"No existe el impuesto {sorted(faltan)[0]}.")
    return tarifas


async def _cliente(db, emp: ERPEmpresa, datos: FacturaVentaEntrada) -> Optional[ERPTercero]:
    """El tercero del cliente: el indicado, el que ya tiene esa identificación o
    uno nuevo. Sin identificación es consumidor final y no se crea tercero."""
    if datos.tercero_id:
        t = await db.get(ERPTercero, datos.tercero_id)
        if t is None or t.empresa_id != emp.id:
            raise _rechazo("Ese cliente no existe en la empresa.")
    else:
        numero = "".join(c for c in (datos.cliente_identificacion or "") if c.isalnum())
        if not numero:
            return None
        t = (await db.execute(select(ERPTercero).where(
            ERPTercero.empresa_id == emp.id,
            ERPTercero.numero_identificacion == numero))).scalar_one_or_none()
        if t is None:
            es_nit = datos.cliente_tipo_identificacion.upper() == "NIT"
            nuevo = ERPTercero(
                empresa_id=emp.id, tipo_identificacion=datos.cliente_tipo_identificacion.upper(),
                numero_identificacion=numero, razon_social=datos.cliente_nombre.strip(),
                email=(datos.cliente_email or "").strip() or None,
                es_persona_natural=not es_nit, es_cliente=True,
                digito_verificacion=erp_motor.digito_verificacion(numero) if es_nit else None,
                activo=True)
            # Dos facturas a la vez para el mismo cliente nuevo: la segunda choca
            # con la llave única. Se deshace solo el savepoint y se usa el que
            # creó la otra transacción, en vez de tumbar la factura.
            try:
                async with db.begin_nested():
                    db.add(nuevo)
                    await db.flush()
                return nuevo
            except IntegrityError:
                t = (await db.execute(select(ERPTercero).where(
                    ERPTercero.empresa_id == emp.id,
                    ERPTercero.numero_identificacion == numero))).scalar_one()
    if not t.es_cliente:
        t.es_cliente = True
    return t


async def _validar_referencias(db, emp: ERPEmpresa, datos: FacturaVentaEntrada) -> None:
    if datos.centro_costo_id:
        cc = await db.get(ERPCentroCosto, datos.centro_costo_id)
        if cc is None or not cc.activo or (cc.empresa_id not in (None, emp.id)):
            raise _rechazo("El centro de costo no existe o no pertenece a la empresa.")
    if datos.vendedor_id:
        v = await db.get(ERPTercero, datos.vendedor_id)
        if v is None or not v.activo or not v.es_vendedor or v.empresa_id != emp.id:
            raise _rechazo("El vendedor debe ser un tercero activo de la empresa marcado como vendedor.")


async def emitir_factura(db: AsyncSession, datos: FacturaVentaEntrada, usuario: str) -> ERPFacturaCliente:
    """Valida, numera, contabiliza y deja emitida una factura de venta o un ticket.

    No hace commit: quien llama lo hace, y si algo falla después (el asiento, un
    pago incluido) la transacción entera se deshace, incluido el consecutivo
    tomado, así que no quedan huecos ni facturas sin asiento.
    """
    emp = await empresa_de(db, datos.empresa_id)
    p = await _preparar(db, emp, datos)
    tercero, totales = p.tercero, p.totales

    # 2. Número de la resolución, dentro de la misma transacción.
    try:
        res = await dian.resolucion_vigente(db, emp.id, RESOLUCION_DE[datos.tipo_documento])
        numero, res = await dian.tomar_numero(db, res.id)
    except dian.ResolucionInvalida as e:
        raise _rechazo(str(e))
    if (await db.execute(select(ERPFacturaCliente.id).where(
            ERPFacturaCliente.numero == numero))).first():
        raise _rechazo(f"El número {numero} ya está usado por otra factura. Revise el "
                       f"consecutivo de la resolución {res.numero_resolucion}.", 409)

    # 3. El documento.
    fac = ERPFacturaCliente(
        empresa_id=emp.id, numero=numero, numero_electronico=numero,
        tipo_documento=datos.tipo_documento, estado=EstadoFactura.EMITIDA,
        resolucion_id=res.id, origen="ERP", estado_dian="POR_TRANSMITIR",
        retencion_fuente=0, retencion_ica=0, retencion_iva=0)
    _volcar(fac, datos, p)
    fac.saldo = totales.total
    fac.cufe = _cufe(emp, fac, res)
    db.add(fac)
    await db.flush()
    _crear_lineas(db, fac, p)

    # 4. El asiento. Si el período está cerrado o falta una regla, el motor
    #    levanta ErrorContable (422) y la transacción entera se deshace.
    comp = await contabilizar_factura(db, fac, usuario)
    fac.comprobante_id = comp.id

    nombre = "Ticket" if datos.tipo_documento == "TICKET" else "Factura"
    await historial(db, fac.id, "EMISION", usuario, f"{nombre} {numero} emitida",
                    {"total": str(fac.total), "resolucion": res.numero_resolucion})
    if datos.comentario and datos.comentario.strip():
        await historial(db, fac.id, "COMENTARIO", usuario, datos.comentario.strip())
    if datos.pago is not None:
        await registrar_recaudo(db, fac, datos.pago, usuario)
    return fac


@dataclass
class _Preparado:
    tercero: Optional[ERPTercero]
    forma: Optional[str]
    medio: Optional[str]
    vence: date
    lineas: list
    calculadas: list
    totales: TotalesDocumento


async def _preparar(db: AsyncSession, emp: ERPEmpresa, datos: FacturaVentaEntrada) -> _Preparado:
    """Valida y calcula todo lo que se deriva del formulario, sin escribir el
    documento. Lo usan la emisión y la edición."""
    # 1. Cliente y líneas: lo que se valida sin tocar nada.
    if not datos.cliente_nombre.strip():
        raise _rechazo("Falta el nombre o la razón social del cliente.")
    if datos.cliente_email and not _CORREO.match(datos.cliente_email.strip()):
        raise _rechazo("El correo electrónico del cliente no tiene un formato válido.")
    lineas = [l for l in datos.lineas if linea_valida(l.cantidad, l.precio_unitario)]
    if not lineas:
        raise _rechazo("La factura necesita al menos un ítem con cantidad y precio.")
    if any(not l.descripcion.strip() for l in lineas):
        raise _rechazo("Cada ítem necesita una descripción.")
    if datos.fecha_vencimiento and datos.fecha_vencimiento < datos.fecha:
        raise _rechazo("La fecha de vencimiento no puede ser anterior a la de la factura.")

    tarifas = await _tarifas(db, lineas)
    try:
        calculadas = [calcular_linea(l.cantidad, l.precio_unitario, l.descuento_pct,
                                     tarifas.get(l.impuesto_id, 0)) for l in lineas]
    except ValueError as e:
        raise _rechazo(str(e))
    totales = calcular_totales(calculadas)

    await _validar_referencias(db, emp, datos)
    forma = await valor_catalogo(db, "FORMA_PAGO", datos.forma_pago, "forma de pago")
    medio = await valor_catalogo(db, "MEDIO_PAGO", datos.medio_pago, "medio de pago")
    tercero = await _cliente(db, emp, datos)

    vence = datos.fecha_vencimiento
    if vence is None:
        cfg = await db.get(ERPConfigGeneral, 1)
        vence = datos.fecha + timedelta(days=cfg.dias_vencimiento_cxc if cfg else 30)

    return _Preparado(tercero=tercero, forma=forma, medio=medio, vence=vence,
                      lineas=lineas, calculadas=calculadas, totales=totales)


def _volcar(fac: ERPFacturaCliente, datos: FacturaVentaEntrada, p: _Preparado) -> None:
    """Copia al documento lo validado. No toca número, estado ni retenciones."""
    t = p.totales
    fac.tercero_id = p.tercero.id if p.tercero else None
    fac.vendedor_id = datos.vendedor_id
    fac.cliente_nombre = datos.cliente_nombre.strip()
    fac.cliente_nit = p.tercero.numero_identificacion if p.tercero else None
    fac.cliente_email = (datos.cliente_email or "").strip() or None
    fac.fecha, fac.fecha_vencimiento = datos.fecha, p.vence
    fac.subtotal, fac.descuento = t.subtotal, t.descuento
    fac.total_impuestos, fac.total = t.impuestos, t.total
    fac.moneda = datos.moneda
    fac.centro_costo_id, fac.proyecto_id = datos.centro_costo_id, datos.proyecto_id
    fac.concepto, fac.observaciones = datos.concepto, datos.notas
    fac.notas_adicionales, fac.remision, fac.firma = datos.notas_adicionales, datos.remision, datos.firma
    fac.forma_pago, fac.medio_pago = p.forma, p.medio


def _crear_lineas(db: AsyncSession, fac: ERPFacturaCliente, p: _Preparado) -> None:
    for entrada, calc in zip(p.lineas, p.calculadas):
        db.add(ERPLineaFacturaCliente(
            factura_id=fac.id, referencia=entrada.referencia, descripcion=entrada.descripcion.strip(),
            cantidad=entrada.cantidad, precio_unitario=entrada.precio_unitario,
            descuento_pct=entrada.descuento_pct, impuesto_id=entrada.impuesto_id,
            subtotal=calc.subtotal, total_impuesto=calc.impuesto, total=calc.total,
            centro_costo_id=fac.centro_costo_id))


def _cufe(emp: ERPEmpresa, fac: ERPFacturaCliente, res) -> str:
    """CUFE (factura) o CUDE (ticket) con los valores actuales del documento."""
    return dian.codigo_unico(fac.numero, fac.fecha, dian.hora_colombia(datetime.now(timezone.utc)),
                             fac.subtotal, fac.total_impuestos, 0, 0, fac.total, emp.nit,
                             fac.cliente_nit, res.clave_tecnica, res.ambiente)


# ─── Recaudo ──────────────────────────────────────────────────────────────────

from app.infrastructure.models.erp import (  # noqa: E402
    ERPCuentaBancaria, ERPPago, EstadoPago, TipoPago,
)

# Estados en los que una factura todavía se puede cobrar.
COBRABLES = (EstadoFactura.EMITIDA, EstadoFactura.PARCIALMENTE_PAGADA, EstadoFactura.VENCIDA)


class PagoVentaEntrada(BaseModel):
    monto: Decimal
    cuenta_bancaria_id: Optional[int] = None
    fecha: date = Field(default_factory=date.today)
    metodo_pago: Optional[str] = None       # catálogo ERP/MEDIO_PAGO
    referencia: Optional[str] = Field(None, max_length=200)
    numero: Optional[str] = Field(None, max_length=50)


async def registrar_recaudo(db: AsyncSession, fac: ERPFacturaCliente, datos: PagoVentaEntrada,
                            usuario: str) -> ERPPago:
    """Registra el cobro total o parcial de una factura de venta.

    Bloquea la fila de la factura mientras decide, para que dos pagos a la vez no
    se apliquen sobre el mismo saldo. Un rechazo no deja pago, asiento ni cambio
    de saldo; tampoco hace commit (lo hace quien llama).
    """
    # `populate_existing`: con el candado tomado se relee el saldo de la base; el
    # objeto en memoria podría traer el de antes de otro pago.
    fac = (await db.execute(select(ERPFacturaCliente).where(
        ERPFacturaCliente.id == fac.id).with_for_update()
        .execution_options(populate_existing=True))).scalar_one()
    monto = r2(datos.monto)
    if monto <= 0:
        raise _rechazo("El valor del pago debe ser mayor que cero.")
    if fac.estado not in COBRABLES:
        raise _rechazo(f"La factura {fac.numero} está {fac.estado.value.lower().replace('_', ' ')}: "
                       "no admite pagos.")
    saldo = r2(fac.saldo)
    if monto > saldo:
        raise _rechazo(f"El pago supera el saldo de la factura {fac.numero}. "
                       f"Se pueden recibir hasta {saldo}.")
    if not datos.cuenta_bancaria_id:
        raise _rechazo("Elija la cuenta bancaria donde entra el dinero.")
    cuenta = await db.get(ERPCuentaBancaria, datos.cuenta_bancaria_id)
    if cuenta is None or not cuenta.activo or cuenta.empresa_id not in (None, fac.empresa_id):
        raise _rechazo("La cuenta bancaria no existe o no pertenece a la empresa.")
    metodo = await valor_catalogo(db, "MEDIO_PAGO", datos.metodo_pago, "medio de pago")

    previos = len((await db.execute(select(ERPPago.id).where(
        ERPPago.factura_cliente_id == fac.id))).all())
    pago = ERPPago(
        empresa_id=fac.empresa_id, tipo=TipoPago.COBRO,
        numero=datos.numero or f"RC-{fac.numero}-{previos + 1}", fecha=datos.fecha,
        factura_cliente_id=fac.id, cuenta_bancaria_id=cuenta.id, monto=monto,
        moneda=fac.moneda or "COP", metodo_pago=metodo, referencia=datos.referencia,
        estado=EstadoPago.PROCESADO, concepto=f"Recaudo factura {fac.numero}")
    db.add(pago)
    await db.flush()

    # El dinero entra a la cuenta contable de esa cuenta bancaria si la tiene;
    # si no, a la del papel «banco» de las reglas.
    comp = await erp_motor.asentar(
        db, empresa_id=fac.empresa_id, evento="RECAUDO_CLIENTE", tipo=TipoComprobante.INGRESO,
        fecha=datos.fecha, concepto=f"Recaudo factura {fac.numero} - {fac.cliente_nombre}",
        lineas=[erp_motor.Linea("banco", debito=monto, tercero_id=fac.tercero_id,
                                cuenta_id=cuenta.cuenta_contable_id),
                erp_motor.Linea("cartera", credito=monto, tercero_id=fac.tercero_id)],
        usuario=usuario, documento_tipo="pago", documento_id=pago.id,
        documento_numero=pago.numero, moneda=fac.moneda or "COP")
    pago.comprobante_id = comp.id

    fac.saldo = saldo - monto
    fac.estado = EstadoFactura.PAGADA if fac.saldo == 0 else EstadoFactura.PARCIALMENTE_PAGADA
    await historial(db, fac.id, "PAGO", usuario, f"Pago {pago.numero} por {monto}",
                    {"pago_id": pago.id, "monto": str(monto), "saldo": str(fac.saldo)})
    return pago


FacturaVentaEntrada.model_rebuild()


# ─── Estado visible y matriz de acciones ──────────────────────────────────────
#
# Una sola regla para qué se puede hacer con una factura. El detalle la manda a
# la pantalla y cada endpoint de acción la vuelve a consultar antes de ejecutar:
# el botón oculto no es la defensa, el servidor sí.

ACCIONES = (
    "imprimir", "descargar_pdf", "compartir",
    "agregar_pago", "aplicar_anticipo", "cerrar_sin_pago",
    "nota_credito", "nota_debito", "editar_retenciones",
    "editar", "convertir_borrador", "anular",
    "clonar", "volver_recurrente", "adjuntar",
)

_DIFERIDAS = {
    "aplicar_anticipo": "Próximamente: los anticipos llegan con la sección de tesorería.",
    "volver_recurrente": "Próximamente: las facturas recurrentes llegan en una entrega posterior.",
}


def estado_visible(fac: ERPFacturaCliente, hoy: Optional[date] = None) -> dict:
    """Cómo se muestra el estado: «Por cobrar» / «Cobrada» y si está vencida.

    El vencimiento se calcula al consultar; no hay proceso que lo marque.
    """
    hoy = hoy or date.today()
    e = fac.estado
    if e == EstadoFactura.BORRADOR:
        texto = "Borrador"
    elif e == EstadoFactura.ANULADA:
        texto = "Anulada"
    elif e == EstadoFactura.CASTIGADA:
        texto = "Castigada"
    elif e == EstadoFactura.PAGADA or r2(fac.saldo) <= 0:
        texto = "Cobrada"
    else:
        texto = "Por cobrar"
    vencida = texto == "Por cobrar" and fac.fecha_vencimiento is not None and fac.fecha_vencimiento < hoy
    return {"estado_visible": texto, "vencida": vencida}


def es_libre(fac: ERPFacturaCliente, hay_pagos: bool) -> bool:
    """Emitida, sin pagos y sin transmitir: se puede editar, volver a borrador
    o anular reversando su asiento (decisión 7 del diseño)."""
    return (fac.estado in (EstadoFactura.EMITIDA, EstadoFactura.VENCIDA)
            and not hay_pagos and (fac.estado_dian or "POR_TRANSMITIR") == "POR_TRANSMITIR")


def acciones_permitidas(fac: ERPFacturaCliente, hay_pagos: bool) -> dict:
    """{acción: {"permitida": bool, "motivo": str | None}} para esta factura."""
    e = fac.estado
    cobrable = e in COBRABLES and r2(fac.saldo) > 0
    libre = es_libre(fac, hay_pagos)
    emitida = e in (EstadoFactura.EMITIDA, EstadoFactura.PARCIALMENTE_PAGADA,
                    EstadoFactura.VENCIDA, EstadoFactura.PAGADA)
    if e == EstadoFactura.ANULADA:
        sin_cambios = "La factura está anulada."
    elif e == EstadoFactura.CASTIGADA:
        sin_cambios = "La factura se cerró sin pago (castigada)."
    elif e == EstadoFactura.BORRADOR:
        sin_cambios = "La factura está en borrador: emítala primero."
    else:
        sin_cambios = None
    a_nota = ("Tiene pagos aplicados o ya se transmitió a la DIAN: corríjala con una "
              "nota crédito o débito.")

    def regla(ok: bool, motivo: Optional[str]) -> dict:
        return {"permitida": ok, "motivo": None if ok else motivo}

    r = {
        "imprimir": regla(True, None),
        "descargar_pdf": regla(True, None),
        "compartir": regla(True, None),
        "clonar": regla(True, None),
        "adjuntar": regla(True, None),
        "agregar_pago": regla(cobrable, sin_cambios or "La factura no tiene saldo por cobrar."),
        "cerrar_sin_pago": regla(cobrable, sin_cambios or "La factura no tiene saldo por cobrar."),
        "nota_credito": regla(emitida, sin_cambios),
        "nota_debito": regla(emitida, sin_cambios),
        "editar_retenciones": regla(libre, sin_cambios or a_nota),
        "editar": regla(libre or e == EstadoFactura.BORRADOR, sin_cambios or a_nota),
        "convertir_borrador": regla(libre, sin_cambios or a_nota),
        "anular": regla(libre, sin_cambios or a_nota),
    }
    for accion, motivo in _DIFERIDAS.items():
        r[accion] = regla(False, motivo)
    return r


async def hay_pagos(db: AsyncSession, factura_id: int) -> bool:
    return (await db.execute(select(ERPPago.id).where(
        ERPPago.factura_cliente_id == factura_id,
        ERPPago.estado == EstadoPago.PROCESADO).limit(1))).first() is not None


async def exigir_accion(db: AsyncSession, fac: ERPFacturaCliente, accion: str) -> None:
    """La guardia común: rechaza la acción si la matriz no la permite."""
    regla = acciones_permitidas(fac, await hay_pagos(db, fac.id))[accion]
    if not regla["permitida"]:
        raise _rechazo(regla["motivo"], 409)


def retenciones_de(fac: ERPFacturaCliente) -> Decimal:
    return r2(fac.retencion_fuente) + r2(fac.retencion_ica) + r2(fac.retencion_iva)


async def contabilizar_factura(db: AsyncSession, fac: ERPFacturaCliente, usuario: str):
    """El asiento de venta de una factura: la cartera por lo que el cliente va a
    pagar (total menos retenciones) y cada retención a su papel."""
    t = fac.tercero_id
    lineas = [
        erp_motor.Linea("cartera", debito=r2(fac.total) - retenciones_de(fac), tercero_id=t),
        erp_motor.Linea("ingreso", credito=r2(fac.subtotal), tercero_id=t),
        erp_motor.Linea("iva_generado", credito=r2(fac.total_impuestos), tercero_id=t),
        erp_motor.Linea("retefuente", debito=r2(fac.retencion_fuente), tercero_id=t),
        erp_motor.Linea("reteica", debito=r2(fac.retencion_ica), tercero_id=t),
        erp_motor.Linea("reteiva", debito=r2(fac.retencion_iva), tercero_id=t),
    ]
    return await erp_motor.asentar(
        db, empresa_id=fac.empresa_id, evento="VENTA_FACTURA", tipo=TipoComprobante.DIARIO,
        fecha=fac.fecha, concepto=f"Factura de venta {fac.numero} - {fac.cliente_nombre}",
        lineas=[l for l in lineas if l.debito or l.credito], usuario=usuario,
        documento_tipo="factura_cliente", documento_id=fac.id, documento_numero=fac.numero,
        centro_costo_id=fac.centro_costo_id, moneda=fac.moneda or "COP",
        tasa_cambio=fac.tasa_cambio or 1)


# ─── Ajustes ──────────────────────────────────────────────────────────────────

import uuid  # noqa: E402

from sqlalchemy import delete, func  # noqa: E402

from app.infrastructure.models.erp_facturacion import (  # noqa: E402
    ERPLineaNotaCredito, ERPNotaCreditoCliente, ERPNotaDebitoCliente, ERPResolucionFacturacion,
)


async def saldo_actual(db: AsyncSession, fac: ERPFacturaCliente) -> Decimal:
    """Total − retenciones − pagos − notas crédito + notas débito, desde la base."""
    async def suma(col, *cond):
        return (await db.execute(select(func.coalesce(func.sum(col), 0)).where(*cond))).scalar()
    pagos = await suma(ERPPago.monto, ERPPago.factura_cliente_id == fac.id,
                       ERPPago.estado == EstadoPago.PROCESADO)
    nc = await suma(ERPNotaCreditoCliente.total, ERPNotaCreditoCliente.factura_id == fac.id)
    nd = await suma(ERPNotaDebitoCliente.total, ERPNotaDebitoCliente.factura_id == fac.id)
    return saldo_de(fac.total, retenciones_de(fac), pagos, nc, nd)


def _motivo(motivo: Optional[str], que: str) -> str:
    if not motivo or not motivo.strip():
        raise _rechazo(f"Indique el motivo para {que}.")
    return motivo.strip()


async def _reversar(db: AsyncSession, fac: ERPFacturaCliente, usuario: str, motivo: str) -> None:
    """Reversa el asiento vigente de la factura. El contrario entra hoy; con el
    período de hoy cerrado el motor rechaza y nada cambia."""
    if fac.comprobante_id:
        await erp_motor.reversar(db, fac.comprobante_id, usuario,
                                 f"Factura {fac.numero}: {motivo}")
        fac.comprobante_id = None


async def _recontabilizar(db: AsyncSession, fac: ERPFacturaCliente, usuario: str) -> None:
    comp = await contabilizar_factura(db, fac, usuario)
    fac.comprobante_id = comp.id
    fac.saldo = await saldo_actual(db, fac)


async def convertir_borrador(db: AsyncSession, fac: ERPFacturaCliente, motivo: Optional[str],
                             usuario: str) -> ERPFacturaCliente:
    await exigir_accion(db, fac, "convertir_borrador")
    motivo = _motivo(motivo, "volver la factura a borrador")
    await _reversar(db, fac, usuario, motivo)
    fac.estado, fac.saldo = EstadoFactura.BORRADOR, 0
    await historial(db, fac.id, "BORRADOR", usuario, motivo)
    return fac


async def anular(db: AsyncSession, fac: ERPFacturaCliente, motivo: Optional[str],
                 usuario: str) -> ERPFacturaCliente:
    await exigir_accion(db, fac, "anular")
    motivo = _motivo(motivo, "anular la factura")
    await _reversar(db, fac, usuario, motivo)
    fac.estado, fac.saldo = EstadoFactura.ANULADA, 0
    await historial(db, fac.id, "ANULACION", usuario, motivo)
    return fac


async def _resolucion_de_factura(db, fac: ERPFacturaCliente):
    """La resolución que numeró la factura, o una vigente si todavía no tiene
    número (un borrador clonado)."""
    if fac.resolucion_id:
        return await db.get(ERPResolucionFacturacion, fac.resolucion_id)
    try:
        res = await dian.resolucion_vigente(db, fac.empresa_id, RESOLUCION_DE[fac.tipo_documento])
        numero, res = await dian.tomar_numero(db, res.id)
    except dian.ResolucionInvalida as e:
        raise _rechazo(str(e))
    fac.numero = fac.numero_electronico = numero
    fac.resolucion_id = res.id
    return res


async def emitir_borrador(db: AsyncSession, fac: ERPFacturaCliente, usuario: str) -> ERPFacturaCliente:
    """Emite un borrador: el que se devolvió a borrador conserva su número; el
    clonado toma uno nuevo de la resolución. En ambos casos, asiento nuevo."""
    if fac.estado != EstadoFactura.BORRADOR:
        raise _rechazo("Solo se emite una factura en borrador.", 409)
    emp = await empresa_de(db, fac.empresa_id)
    res = await _resolucion_de_factura(db, fac)
    fac.cufe = _cufe(emp, fac, res)
    fac.estado, fac.estado_dian = EstadoFactura.EMITIDA, "POR_TRANSMITIR"
    await _recontabilizar(db, fac, usuario)
    await historial(db, fac.id, "EMISION", usuario, f"Factura {fac.numero} emitida",
                    {"total": str(fac.total)})
    return fac


async def editar_factura(db: AsyncSession, fac: ERPFacturaCliente, datos: FacturaVentaEntrada,
                         usuario: str) -> ERPFacturaCliente:
    """Edita una factura en borrador o una emitida libre.

    La emitida se reversa, se cambia y se vuelve a contabilizar con su mismo
    número; el borrador solo se cambia y sigue en borrador hasta que se emita.
    """
    await exigir_accion(db, fac, "editar")
    if datos.tipo_documento != fac.tipo_documento:
        raise _rechazo("El tipo de documento no se puede cambiar: define la numeración.")
    emp = await empresa_de(db, fac.empresa_id)
    p = await _preparar(db, emp, datos)
    emitida = fac.estado != EstadoFactura.BORRADOR
    antes = {"total": str(fac.total), "cliente": fac.cliente_nombre}
    if emitida:
        await _reversar(db, fac, usuario, "edición")
    _volcar(fac, datos, p)
    await db.execute(delete(ERPLineaFacturaCliente).where(ERPLineaFacturaCliente.factura_id == fac.id))
    _crear_lineas(db, fac, p)
    await db.flush()
    if emitida:
        fac.cufe = _cufe(emp, fac, await db.get(ERPResolucionFacturacion, fac.resolucion_id))
        await _recontabilizar(db, fac, usuario)
    await historial(db, fac.id, "EDICION", usuario, "Factura editada",
                    {"antes": antes, "despues": {"total": str(fac.total), "cliente": fac.cliente_nombre}})
    return fac


class NotaEntrada(BaseModel):
    valor: Decimal = Field(gt=0)                # base, sin impuesto
    tarifa_iva: Decimal = Field(Decimal(0), ge=0, le=100)
    motivo: str = ""
    concepto: Optional[str] = None              # código DIAN de la corrección
    fecha: date = Field(default_factory=date.today)


async def _numero_nota(db, fac, tipo: str):
    try:
        res = await dian.resolucion_vigente(db, fac.empresa_id, tipo)
        return await dian.tomar_numero(db, res.id)
    except dian.ResolucionInvalida as e:
        raise _rechazo(str(e))


async def aplicar_nota(db: AsyncSession, fac: ERPFacturaCliente, tipo: str, datos: NotaEntrada,
                       usuario: str):
    """Nota crédito (`CREDITO`) o débito (`DEBITO`) sobre una factura emitida.

    La nota crédito no puede dejar la factura por debajo de cero en lo
    facturado; si ya estaba cobrada, lo que sobra queda como saldo a favor del
    cliente, registrado en el historial, y la factura no se reabre.
    """
    credito = tipo == "CREDITO"
    await exigir_accion(db, fac, "nota_credito" if credito else "nota_debito")
    motivo = _motivo(datos.motivo, f"la nota {'crédito' if credito else 'débito'}")
    base = r2(datos.valor)
    iva = r2(base * datos.tarifa_iva / 100)
    total = base + iva
    if credito:
        previas = (await db.execute(select(func.coalesce(func.sum(ERPNotaCreditoCliente.total), 0))
                                    .where(ERPNotaCreditoCliente.factura_id == fac.id))).scalar()
        if r2(previas) + total > r2(fac.total):
            raise _rechazo(f"La nota supera lo facturado: se pueden acreditar hasta "
                           f"{r2(fac.total) - r2(previas)}.")
    numero, res = await _numero_nota(db, fac, "NOTA_CREDITO" if credito else "NOTA_DEBITO")
    emp = await empresa_de(db, fac.empresa_id)
    cude = dian.codigo_unico(numero, datos.fecha, dian.hora_colombia(datetime.now(timezone.utc)),
                             base, iva, 0, 0, total, emp.nit, fac.cliente_nit,
                             res.clave_tecnica, res.ambiente)
    comun = dict(empresa_id=fac.empresa_id, numero=numero, resolucion_id=res.id, factura_id=fac.id,
                 fecha=datos.fecha, motivo=motivo, subtotal=base, total_impuestos=iva, total=total,
                 cude=cude, creado_por=usuario)
    if credito:
        nota = ERPNotaCreditoCliente(concepto=datos.concepto or "1", origen="ERP", **comun)
        nota.lineas = [ERPLineaNotaCredito(descripcion=motivo[:300], cantidad=1, precio_unitario=base,
                                           tarifa_iva=datos.tarifa_iva, subtotal=base,
                                           impuesto=iva, total=total)]
    else:
        nota = ERPNotaDebitoCliente(concepto=datos.concepto or "4", **comun)
    db.add(nota)
    await db.flush()

    lineas = [erp_motor.Linea("cartera", **({"credito": total} if credito else {"debito": total}),
                              tercero_id=fac.tercero_id),
              erp_motor.Linea("ingreso", **({"debito": base} if credito else {"credito": base}),
                              tercero_id=fac.tercero_id)]
    if iva:
        lineas.append(erp_motor.Linea("iva_generado", **({"debito": iva} if credito else {"credito": iva}),
                                      tercero_id=fac.tercero_id))
    comp = await erp_motor.asentar(
        db, empresa_id=fac.empresa_id, evento="VENTA_NOTA_CREDITO" if credito else "VENTA_NOTA_DEBITO",
        tipo=TipoComprobante.DIARIO, fecha=datos.fecha,
        concepto=f"Nota {'crédito' if credito else 'débito'} {numero} a la factura {fac.numero}",
        lineas=lineas, usuario=usuario, documento_tipo="nota_credito" if credito else "nota_debito",
        documento_id=nota.id, documento_numero=numero, centro_costo_id=fac.centro_costo_id)
    nota.comprobante_id = comp.id

    nuevo = await saldo_actual(db, fac)
    a_favor = -nuevo if nuevo < 0 else Decimal(0)
    fac.saldo = max(nuevo, Decimal(0))
    if not credito and fac.estado == EstadoFactura.PAGADA and fac.saldo > 0:
        fac.estado = EstadoFactura.PARCIALMENTE_PAGADA
    await historial(db, fac.id, "NOTA_CREDITO" if credito else "NOTA_DEBITO", usuario,
                    f"Nota {numero} por {total}: {motivo}",
                    {"nota_id": nota.id, "total": str(total), "saldo": str(fac.saldo),
                     "saldo_a_favor": str(a_favor)})
    return nota


# Conceptos con que se sugieren las retenciones de una venta.
CONCEPTO_RETEFUENTE = "Servicios generales"


async def sugerir_retenciones(db: AsyncSession, fac: ERPFacturaCliente,
                              concepto: str = CONCEPTO_RETEFUENTE) -> dict:
    """Lo que el sistema calcula que el cliente retendría, con el porqué de cada
    valor. Solo hay retención si el cliente es agente de retención."""
    from app.core import erp_impuestos

    tercero = await db.get(ERPTercero, fac.tercero_id) if fac.tercero_id else None
    if tercero is None or not (tercero.agente_retencion or tercero.gran_contribuyente):
        motivo = "El cliente no es agente de retención: no practica retenciones."
        return {k: {"valor": "0.00", "motivo": motivo} for k in ("fuente", "ica", "iva")}
    salida = {}
    for clave, impuesto, conc, base in (("fuente", "RETEFUENTE", concepto, fac.subtotal),
                                        ("ica", "RETEICA", "General", fac.subtotal),
                                        ("iva", "RETEIVA", "General", fac.total_impuestos)):
        r = await erp_impuestos.calcular(db, empresa_id=fac.empresa_id, impuesto=impuesto,
                                         concepto=conc, base=base, fecha=fac.fecha, tercero=tercero,
                                         codigo_municipio=tercero.codigo_municipio)
        salida[clave] = {"valor": str(r2(r.valor)), "motivo": r.motivo}
    return salida


class RetencionesEntrada(BaseModel):
    retencion_fuente: Decimal = Field(Decimal(0), ge=0)
    retencion_ica: Decimal = Field(Decimal(0), ge=0)
    retencion_iva: Decimal = Field(Decimal(0), ge=0)


async def editar_retenciones(db: AsyncSession, fac: ERPFacturaCliente, datos: RetencionesEntrada,
                             usuario: str) -> ERPFacturaCliente:
    """Registra lo que el cliente retuvo. Si difiere de lo sugerido queda marcado
    como editado a mano, con el antes y el después en el historial."""
    await exigir_accion(db, fac, "editar_retenciones")
    nuevas = {k: r2(getattr(datos, k)) for k in ("retencion_fuente", "retencion_ica", "retencion_iva")}
    if sum(nuevas.values()) > r2(fac.total):
        raise _rechazo("Las retenciones no pueden superar el total de la factura.")
    sugeridas = await sugerir_retenciones(db, fac)
    sugerido = {"retencion_fuente": r2(sugeridas["fuente"]["valor"]),
                "retencion_ica": r2(sugeridas["ica"]["valor"]),
                "retencion_iva": r2(sugeridas["iva"]["valor"])}
    antes = {k: str(r2(getattr(fac, k))) for k in nuevas}
    await _reversar(db, fac, usuario, "cambio de retenciones")
    for k, v in nuevas.items():
        setattr(fac, k, v)
    fac.retenciones_editadas = nuevas != sugerido
    await _recontabilizar(db, fac, usuario)
    await historial(db, fac.id, "RETENCIONES", usuario,
                    "Retenciones modificadas a mano" if fac.retenciones_editadas
                    else "Retenciones registradas según el cálculo",
                    {"antes": antes, "despues": {k: str(v) for k, v in nuevas.items()},
                     "sugerido": {k: str(v) for k, v in sugerido.items()}})
    return fac


async def castigar(db: AsyncSession, fac: ERPFacturaCliente, motivo: Optional[str],
                   usuario: str) -> ERPFacturaCliente:
    """«Cerrar sin pago»: el saldo se da por perdido contra el gasto de castigo."""
    await exigir_accion(db, fac, "cerrar_sin_pago")
    motivo = _motivo(motivo, "cerrar la factura sin pago")
    saldo = r2(fac.saldo)
    comp = await erp_motor.asentar(
        db, empresa_id=fac.empresa_id, evento="CASTIGO_CARTERA", tipo=TipoComprobante.DIARIO,
        fecha=date.today(), concepto=f"Castigo de cartera factura {fac.numero}: {motivo}",
        lineas=[erp_motor.Linea("gasto_castigo", debito=saldo, tercero_id=fac.tercero_id),
                erp_motor.Linea("cartera", credito=saldo, tercero_id=fac.tercero_id),
                erp_motor.Linea("control_castigo", debito=saldo, tercero_id=fac.tercero_id),
                erp_motor.Linea("contra_castigo", credito=saldo, tercero_id=fac.tercero_id)],
        usuario=usuario, documento_tipo="castigo", documento_id=fac.id,
        documento_numero=fac.numero, centro_costo_id=fac.centro_costo_id)
    fac.estado, fac.saldo = EstadoFactura.CASTIGADA, 0
    fac.cerrada_motivo, fac.cerrada_por = motivo, usuario
    fac.cerrada_en = datetime.now(timezone.utc)
    await historial(db, fac.id, "CASTIGO", usuario, f"Castigo por {saldo}: {motivo}",
                    {"monto": str(saldo), "comprobante_id": comp.id})
    return fac


async def clonar(db: AsyncSession, fac: ERPFacturaCliente, usuario: str) -> ERPFacturaCliente:
    """Un borrador nuevo con los mismos datos y líneas: sin número (toma uno al
    emitirse), sin pagos, sin retenciones y con la fecha de hoy."""
    hoy = date.today()
    plazo = (fac.fecha_vencimiento - fac.fecha) if fac.fecha_vencimiento and fac.fecha else timedelta(0)
    nueva = ERPFacturaCliente(
        empresa_id=fac.empresa_id, numero=f"BORRADOR-{uuid.uuid4().hex[:12]}",
        tipo_documento=fac.tipo_documento, estado=EstadoFactura.BORRADOR,
        tercero_id=fac.tercero_id, vendedor_id=fac.vendedor_id, cliente_nombre=fac.cliente_nombre,
        cliente_nit=fac.cliente_nit, cliente_email=fac.cliente_email, fecha=hoy,
        fecha_vencimiento=hoy + plazo, subtotal=fac.subtotal, descuento=fac.descuento,
        total_impuestos=fac.total_impuestos, total=fac.total, saldo=0, moneda=fac.moneda,
        centro_costo_id=fac.centro_costo_id, proyecto_id=fac.proyecto_id, concepto=fac.concepto,
        observaciones=fac.observaciones, notas_adicionales=fac.notas_adicionales,
        remision=fac.remision, forma_pago=fac.forma_pago, medio_pago=fac.medio_pago,
        retencion_fuente=0, retencion_ica=0, retencion_iva=0, origen="ERP")
    db.add(nueva)
    await db.flush()
    for l in (await db.execute(select(ERPLineaFacturaCliente).where(
            ERPLineaFacturaCliente.factura_id == fac.id).order_by(ERPLineaFacturaCliente.id))).scalars():
        db.add(ERPLineaFacturaCliente(
            factura_id=nueva.id, referencia=l.referencia, item_id=l.item_id, descripcion=l.descripcion,
            cantidad=l.cantidad, precio_unitario=l.precio_unitario, descuento_pct=l.descuento_pct,
            impuesto_id=l.impuesto_id, subtotal=l.subtotal, total_impuesto=l.total_impuesto,
            total=l.total, cuenta_id=l.cuenta_id, centro_costo_id=l.centro_costo_id))
    await historial(db, nueva.id, "CLONACION", usuario, f"Clonada de la factura {fac.numero}",
                    {"origen_id": fac.id})
    return nueva

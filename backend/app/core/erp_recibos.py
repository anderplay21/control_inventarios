"""
Recibos de caja del ERP (Ingresos · Pagos recibidos).

Un solo lugar para todo el dinero que la empresa recibe de sus clientes:

  * **Pago a factura**: el dinero se reparte entre una o varias facturas por
    cobrar del cliente (pagos totales o abonos).
  * **Anticipo**: dinero recibido antes de facturar. Es un PASIVO (280505), no
    un ingreso; deja un saldo a favor del cliente que después se aplica a sus
    facturas.
  * **Otros ingresos**: dinero que no viene de una venta; la cuenta del crédito
    la elige quien registra.

El pago rápido de la factura de venta y `POST /erp/pagos` también terminan
aquí, así que todo cobro tiene su recibo, su consecutivo, su asiento y su
movimiento bancario.

Cada factura cruzada es un `ERPPago` con `recibo_id`: la factura sigue leyendo
sus pagos para su saldo, su historial y su matriz, sin saber de recibos.

Nada de aquí hace commit: lo hace quien llama, y un rechazo deshace todo
(recibo, asiento, movimiento, consecutivo y saldos).

No importa nada del WMS, del POS ni de reencauche.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import erp_motor
from app.core.erp_ventas import (
    COBRABLES, _rechazo, empresa_de, historial, r2, retenciones_de, saldo_actual,
    valor_catalogo,
)
from app.infrastructure.models.erp import (
    ERPCentroCosto, ERPCuentaBancaria, ERPFacturaCliente, ERPMovimientoBancario,
    ERPPlanCuenta, ERPPago, EstadoFactura, EstadoPago, TipoComprobante,
    TipoMovimientoBancario, TipoPago,
)
from app.infrastructure.models.erp_nucleo import ERPTercero
from app.infrastructure.models.erp_recibos import (
    ERPReciboCaja, ERPReciboComentario, ERPReciboLineaOtro, ERPReciboRetencion,
)

# Talonario propio. No es el «RC» de los comprobantes contables de ingreso:
# ese lo consumen también el POS, los movimientos bancarios y los reversos, y
# los recibos quedarían con huecos.
PREFIJO_RECIBO = "RCJ"

# Tipo de retención del recibo → papel del motor contable.
PAPEL_RETENCION = {"RETEFUENTE": "retefuente", "RETEIVA": "reteiva", "RETEICA": "reteica"}
NOMBRE_RETENCION = {"RETEFUENTE": "Retención en la fuente", "RETEIVA": "Retención de IVA",
                    "RETEICA": "Retención de ICA"}

EVENTO_DE = {"PAGO_FACTURA": "RECAUDO_CLIENTE", "ANTICIPO": "ANTICIPO_CLIENTE",
             "OTROS": "OTRO_INGRESO"}
NOMBRE_TIPO = {"PAGO_FACTURA": "Pago a factura", "ANTICIPO": "Anticipo", "OTROS": "Otros ingresos"}


async def numero_recibo(db: AsyncSession, empresa_id: int, anio: int) -> str:
    """El siguiente número del talonario de recibos de caja (RCJ-AAAA-NNNNNN)."""
    return await erp_motor.siguiente_numero(db, empresa_id, None, anio, prefijo=PREFIJO_RECIBO)


def identificacion_normalizada(valor: Optional[str]) -> str:
    """Solo letras y dígitos: «900.123.456-7» → «9001234567»."""
    return "".join(c for c in (valor or "") if c.isalnum())


def _candidatos_nit(tercero: ERPTercero) -> List[str]:
    """Cómo puede venir escrito el NIT del tercero en una factura antigua: con o
    sin el dígito de verificación."""
    base = identificacion_normalizada(tercero.numero_identificacion)
    salida = [base] if base else []
    if base and tercero.digito_verificacion not in (None, ""):
        salida.append(f"{base}{tercero.digito_verificacion}")
    return salida


def _mismo_cliente(fac: ERPFacturaCliente, tercero: Optional[ERPTercero]) -> bool:
    if tercero is None:
        return False
    if fac.tercero_id is not None:
        return fac.tercero_id == tercero.id
    return identificacion_normalizada(fac.cliente_nit) in _candidatos_nit(tercero)


# ─── Entradas ─────────────────────────────────────────────────────────────────

class AplicacionEntrada(BaseModel):
    factura_id: int
    monto: Optional[Decimal] = None          # vacío o cero: no se aplica nada


class LineaOtroEntrada(BaseModel):
    cuenta_id: int
    descripcion: Optional[str] = Field(None, max_length=300)
    valor: Decimal


class RetencionEntrada(BaseModel):
    tipo: Literal["RETEFUENTE", "RETEIVA", "RETEICA"]
    concepto: Optional[str] = Field(None, max_length=150)
    base: Optional[Decimal] = None           # por omisión, el valor bruto
    porcentaje: Optional[Decimal] = None     # por omisión, el de la configuración fiscal
    valor: Optional[Decimal] = None          # por omisión, base × porcentaje


class ReciboEntrada(BaseModel):
    """Lo que manda el formulario «+ Nuevo ingreso»."""
    empresa_id: Optional[int] = None
    tipo_ingreso: Literal["PAGO_FACTURA", "ANTICIPO", "OTROS"]
    tercero_id: Optional[int] = None
    cuenta_bancaria_id: Optional[int] = None
    fecha: date = Field(default_factory=date.today)
    forma_pago: Optional[str] = None
    medio_pago: Optional[str] = None
    moneda: str = "COP"
    centro_costo_id: Optional[int] = None
    referencia: Optional[str] = Field(None, max_length=200)
    notas_adicionales: Optional[str] = None      # se imprimen en el PDF
    comentario: Optional[str] = None             # interno, no se imprime
    facturas: List[AplicacionEntrada] = []       # PAGO_FACTURA
    valor: Optional[Decimal] = None              # ANTICIPO: valor bruto recibido
    lineas: List[LineaOtroEntrada] = []          # OTROS
    retenciones: List[RetencionEntrada] = []     # ANTICIPO y OTROS

    @field_validator("moneda")
    @classmethod
    def _moneda(cls, v: str) -> str:
        v = (v or "COP").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("La moneda va con su código ISO de tres letras, por ejemplo COP.")
        return v


# ─── Validaciones comunes ─────────────────────────────────────────────────────

async def _tercero(db: AsyncSession, empresa_id: int, tercero_id: Optional[int]) -> ERPTercero:
    if not tercero_id:
        raise _rechazo("Elija el cliente: todo pago recibido pertenece a un tercero registrado.")
    t = await db.get(ERPTercero, tercero_id)
    if t is None or t.empresa_id != empresa_id or not t.activo:
        raise _rechazo("Ese cliente no existe en la empresa o está inactivo.")
    return t


async def _cuenta_bloqueada(db: AsyncSession, cuenta_id: Optional[int],
                            empresa_id: int) -> ERPCuentaBancaria:
    if not cuenta_id:
        raise _rechazo("Elija la cuenta bancaria o caja donde entra el dinero.")
    cuenta = (await db.execute(select(ERPCuentaBancaria).where(
        ERPCuentaBancaria.id == cuenta_id).with_for_update()
        .execution_options(populate_existing=True))).scalar_one_or_none()
    if cuenta is None or not cuenta.activo or cuenta.empresa_id not in (None, empresa_id):
        raise _rechazo("La cuenta bancaria no existe o no pertenece a la empresa.")
    return cuenta


async def _centro_costo(db: AsyncSession, centro_id: Optional[int], empresa_id: int) -> None:
    if centro_id:
        cc = await db.get(ERPCentroCosto, centro_id)
        if cc is None or not cc.activo or cc.empresa_id not in (None, empresa_id):
            raise _rechazo("El centro de costo no existe o no pertenece a la empresa.")


async def _facturas_bloqueadas(db: AsyncSession, ids: Sequence[int]) -> Dict[int, ERPFacturaCliente]:
    """Bloquea las facturas en orden ascendente de id: dos recibos que cruzan las
    mismas facturas las toman en el mismo orden y no se bloquean mutuamente."""
    salida: Dict[int, ERPFacturaCliente] = {}
    for fid in sorted(set(ids)):
        fac = (await db.execute(select(ERPFacturaCliente).where(
            ERPFacturaCliente.id == fid).with_for_update()
            .execution_options(populate_existing=True))).scalar_one_or_none()
        if fac is None or not fac.activo:
            raise _rechazo(f"La factura {fid} no existe.")
        salida[fid] = fac
    return salida


def _texto_estado(fac: ERPFacturaCliente) -> str:
    return fac.estado.value.lower().replace("_", " ")


async def _actualizar_factura(db: AsyncSession, fac: ERPFacturaCliente) -> None:
    """Saldo y estado de cobro de la factura según sus pagos vigentes.

    Solo toca facturas cobrables o cobradas: una anulada o castigada no cambia
    de estado por un pago (las acciones que llegan aquí ya las rechazaron).
    """
    if fac.estado not in COBRABLES + (EstadoFactura.PAGADA,):
        return
    nuevo = await saldo_actual(db, fac)
    fac.saldo = max(nuevo, Decimal(0))
    if fac.saldo <= 0:
        fac.estado = EstadoFactura.PAGADA
    else:
        con_pagos = (await db.execute(select(ERPPago.id).where(
            ERPPago.factura_cliente_id == fac.id,
            ERPPago.estado == EstadoPago.PROCESADO).limit(1))).first() is not None
        fac.estado = EstadoFactura.PARCIALMENTE_PAGADA if con_pagos else EstadoFactura.EMITIDA


# ─── Retenciones ──────────────────────────────────────────────────────────────

async def sugerir_retencion(db: AsyncSession, empresa_id: int, tercero: Optional[ERPTercero],
                            tipo: str, concepto: Optional[str], base, fecha: date) -> dict:
    """Lo que la configuración fiscal dice que el cliente retendría sobre esa
    base, con el porqué. Solo retiene un cliente agente de retención."""
    from app.core import erp_impuestos

    base = r2(base)
    if tercero is None or not (tercero.agente_retencion or tercero.gran_contribuyente):
        return {"porcentaje": Decimal(0), "valor": Decimal(0),
                "motivo": "El cliente no es agente de retención: no practica retenciones."}
    conc = concepto or ("Servicios generales" if tipo == "RETEFUENTE" else "General")
    r = await erp_impuestos.calcular(db, empresa_id=empresa_id, impuesto=tipo, concepto=conc,
                                     base=base, fecha=fecha, tercero=tercero,
                                     codigo_municipio=tercero.codigo_municipio)
    return {"porcentaje": Decimal(str(r.tarifa or 0)), "valor": r2(r.valor), "motivo": r.motivo}


async def conceptos_retencion(db: AsyncSession, empresa_id: int) -> Dict[str, List[str]]:
    """Los conceptos configurados de cada tipo de retención, para el desplegable."""
    from app.infrastructure.models.erp_nucleo import ERPReglaImpuesto

    filas = (await db.execute(select(ERPReglaImpuesto.impuesto, ERPReglaImpuesto.concepto).where(
        ERPReglaImpuesto.empresa_id == empresa_id, ERPReglaImpuesto.activa.is_(True),
        ERPReglaImpuesto.impuesto.in_(tuple(PAPEL_RETENCION))).distinct()
        .order_by(ERPReglaImpuesto.concepto))).all()
    salida: Dict[str, List[str]] = {t: [] for t in PAPEL_RETENCION}
    for impuesto, concepto in filas:
        salida[impuesto].append(concepto)
    return salida


async def _calcular_retenciones(db: AsyncSession, empresa_id: int, tercero: ERPTercero,
                                entradas: Sequence[RetencionEntrada], bruto: Decimal,
                                fecha: date) -> List[ERPReciboRetencion]:
    salida = []
    for e in entradas:
        base = r2(e.base if e.base is not None else bruto)
        if base <= 0:
            raise _rechazo(f"La base de la {NOMBRE_RETENCION[e.tipo].lower()} debe ser mayor que cero.")
        sugerida = await sugerir_retencion(db, empresa_id, tercero, e.tipo, e.concepto, base, fecha)
        pct = Decimal(str(e.porcentaje)) if e.porcentaje is not None else sugerida["porcentaje"]
        if pct < 0 or pct > 100:
            raise _rechazo("El porcentaje de una retención debe estar entre 0 % y 100 %.")
        valor = r2(e.valor) if e.valor is not None else r2(base * pct / 100)
        if valor < 0:
            raise _rechazo("Una retención no puede ser negativa.")
        if valor == 0:
            continue
        editada = (pct != sugerida["porcentaje"]) or (valor != r2(base * sugerida["porcentaje"] / 100))
        salida.append(ERPReciboRetencion(tipo=e.tipo, concepto=e.concepto, base=base,
                                         porcentaje=pct, valor=valor, editada=editada))
    return salida


# ─── Crear ────────────────────────────────────────────────────────────────────

async def crear(db: AsyncSession, datos: ReciboEntrada, usuario: str, *,
                permitir_sin_tercero: bool = False) -> ERPReciboCaja:
    """Valida, numera, contabiliza y guarda un recibo de caja.

    `permitir_sin_tercero` solo lo usa el pago que nace de una factura de venta
    sin tercero (consumidor final): el formulario «+ Nuevo ingreso» siempre exige
    cliente registrado.
    """
    emp = await empresa_de(db, datos.empresa_id)
    tipo = datos.tipo_ingreso

    # 1. Cliente.
    tercero: Optional[ERPTercero] = None
    if datos.tercero_id or not permitir_sin_tercero:
        tercero = await _tercero(db, emp.id, datos.tercero_id)
    if tipo != "PAGO_FACTURA" and tercero is None:
        raise _rechazo("Elija el cliente: todo pago recibido pertenece a un tercero registrado.")

    # 2. Lo que depende del tipo. Las facturas se bloquean antes que la cuenta.
    aplicaciones: List[tuple] = []          # (factura, monto)
    lineas_otros: List[ERPReciboLineaOtro] = []
    if tipo == "PAGO_FACTURA":
        if datos.lineas or datos.valor:
            raise _rechazo("Un pago a factura solo lleva facturas con su monto a recibir.")
        if datos.retenciones:
            raise _rechazo("En un pago a factura las retenciones se registran en la factura, "
                           "no en el recibo.")
        con_monto = [a for a in datos.facturas if a.monto is not None and r2(a.monto) != 0]
        if not con_monto:
            raise _rechazo("Indique el monto a recibir de al menos una factura.")
        ids = [a.factura_id for a in con_monto]
        if len(ids) != len(set(ids)):
            raise _rechazo("Una factura aparece dos veces en el recibo.")
        facturas = await _facturas_bloqueadas(db, ids)
        for a in con_monto:
            fac, monto = facturas[a.factura_id], r2(a.monto)
            if monto <= 0:
                raise _rechazo(f"El monto a recibir de la factura {fac.numero} debe ser mayor que cero.")
            if fac.empresa_id != emp.id:
                raise _rechazo(f"La factura {fac.numero} no es de esta empresa.")
            if fac.estado not in COBRABLES:
                raise _rechazo(f"La factura {fac.numero} está {_texto_estado(fac)}: no admite pagos.")
            if tercero is not None and not _mismo_cliente(fac, tercero):
                raise _rechazo(f"La factura {fac.numero} no es de {tercero.razon_social}.")
            if (fac.moneda or "COP") != datos.moneda:
                raise _rechazo(f"La factura {fac.numero} está en {fac.moneda}; el recibo es en "
                               f"{datos.moneda}.")
            saldo = r2(fac.saldo)
            if monto > saldo:
                raise _rechazo(f"El pago supera el saldo de la factura {fac.numero}. "
                               f"Se pueden recibir hasta {saldo}.")
            aplicaciones.append((fac, monto))
        bruto = sum((m for _, m in aplicaciones), Decimal(0))
    elif tipo == "ANTICIPO":
        if datos.facturas or datos.lineas:
            raise _rechazo("Un anticipo no se cruza con facturas al recibirlo: se aplica después "
                           "desde la factura.")
        bruto = r2(datos.valor)
        if bruto <= 0:
            raise _rechazo("El valor del anticipo debe ser mayor que cero.")
    else:   # OTROS
        if datos.facturas or datos.valor:
            raise _rechazo("Un otro ingreso se registra con sus cuentas contables, no con facturas.")
        if not datos.lineas:
            raise _rechazo("Agregue al menos una cuenta contable con su valor.")
        for ln in datos.lineas:
            valor = r2(ln.valor)
            if valor <= 0:
                raise _rechazo("El valor de cada línea debe ser mayor que cero.")
            cta = await db.get(ERPPlanCuenta, ln.cuenta_id)
            if cta is None or cta.empresa_id not in (None, emp.id) or cta.activo is False:
                raise _rechazo("Esa cuenta contable no existe en la empresa.")
            if not cta.acepta_movimientos:
                raise _rechazo(f"La cuenta {cta.codigo} es agrupadora y no recibe movimiento. "
                               f"Elija una subcuenta.")
            lineas_otros.append(ERPReciboLineaOtro(cuenta_id=cta.id, descripcion=ln.descripcion,
                                                   valor=valor))
        bruto = sum((l.valor for l in lineas_otros), Decimal(0))

    # 3. Retenciones, datos generales y cuenta.
    retenciones = (await _calcular_retenciones(db, emp.id, tercero, datos.retenciones, bruto, datos.fecha)
                   if tipo != "PAGO_FACTURA" else [])
    total_ret = sum((r.valor for r in retenciones), Decimal(0))
    neto = bruto - total_ret
    if neto <= 0:
        raise _rechazo("Las retenciones no pueden igualar ni superar el valor recibido.")
    forma = await valor_catalogo(db, "FORMA_PAGO", datos.forma_pago, "forma de pago")
    medio = await valor_catalogo(db, "MEDIO_PAGO", datos.medio_pago, "medio de pago")
    await _centro_costo(db, datos.centro_costo_id, emp.id)
    cuenta = await _cuenta_bloqueada(db, datos.cuenta_bancaria_id, emp.id)
    if (cuenta.moneda or "COP") != datos.moneda:
        raise _rechazo(f"La cuenta {cuenta.numero} maneja {cuenta.moneda}; el recibo es en {datos.moneda}.")

    # 4. El documento.
    if tercero is not None:
        nombre, nit = tercero.razon_social, tercero.numero_identificacion
    else:
        fac0 = aplicaciones[0][0]
        nombre, nit = fac0.cliente_nombre, fac0.cliente_nit
    rec = ERPReciboCaja(
        empresa_id=emp.id, numero=await numero_recibo(db, emp.id, datos.fecha.year),
        tipo_ingreso=tipo, tercero_id=tercero.id if tercero else None,
        cliente_nombre=nombre or "", cliente_nit=nit, cuenta_bancaria_id=cuenta.id,
        fecha=datos.fecha, forma_pago=forma, medio_pago=medio, moneda=datos.moneda,
        centro_costo_id=datos.centro_costo_id, valor_bruto=bruto, total_retenciones=total_ret,
        valor_neto=neto, saldo_por_aplicar=bruto if tipo == "ANTICIPO" else Decimal(0),
        estado="POR_APLICAR" if tipo == "ANTICIPO" else "APLICADO",
        notas_adicionales=(datos.notas_adicionales or "").strip() or None,
        referencia=datos.referencia, creado_por=usuario, historico=False)
    db.add(rec)
    await db.flush()
    for x in (*lineas_otros, *retenciones):
        x.recibo_id = rec.id
        db.add(x)

    # 5. El asiento: un solo comprobante por recibo.
    t_id = tercero.id if tercero else None
    lineas = [erp_motor.Linea("banco", debito=neto, tercero_id=t_id, cuenta_id=cuenta.cuenta_contable_id)]
    lineas += [erp_motor.Linea(PAPEL_RETENCION[r.tipo], debito=r.valor, tercero_id=t_id)
               for r in retenciones]
    if tipo == "PAGO_FACTURA":
        lineas += [erp_motor.Linea("cartera", credito=m, tercero_id=f.tercero_id or t_id,
                                   concepto=f"Factura {f.numero}") for f, m in aplicaciones]
    elif tipo == "ANTICIPO":
        lineas.append(erp_motor.Linea("anticipo", credito=bruto, tercero_id=t_id))
    else:
        lineas += [erp_motor.Linea("contrapartida", credito=l.valor, tercero_id=t_id,
                                   cuenta_id=l.cuenta_id, concepto=l.descripcion)
                   for l in lineas_otros]
    comp = await erp_motor.asentar(
        db, empresa_id=emp.id, evento=EVENTO_DE[tipo], tipo=TipoComprobante.INGRESO,
        fecha=datos.fecha, concepto=f"Recibo de caja {rec.numero} - {NOMBRE_TIPO[tipo]} - {rec.cliente_nombre}",
        lineas=lineas, usuario=usuario, documento_tipo="recibo_caja", documento_id=rec.id,
        documento_numero=rec.numero, centro_costo_id=datos.centro_costo_id, moneda=datos.moneda)
    rec.comprobante_id = comp.id

    # 6. El banco: movimiento de entrada por lo que realmente entró.
    cuenta.saldo_contable = r2(cuenta.saldo_contable) + neto
    mov = ERPMovimientoBancario(
        cuenta_id=cuenta.id, fecha=datos.fecha, tipo=TipoMovimientoBancario.CREDITO, monto=neto,
        concepto=f"Recibo de caja {rec.numero} - {rec.cliente_nombre}"[:500],
        referencia=rec.numero, conciliado=False, comprobante_id=comp.id,
        saldo_acumulado=cuenta.saldo_contable)
    db.add(mov)
    await db.flush()
    rec.movimiento_bancario_id = mov.id

    # 7. Las facturas.
    for fac, monto in aplicaciones:
        pago = ERPPago(
            empresa_id=emp.id, tipo=TipoPago.COBRO, numero=rec.numero, fecha=datos.fecha,
            factura_cliente_id=fac.id, cuenta_bancaria_id=cuenta.id, monto=monto,
            moneda=datos.moneda, metodo_pago=medio, referencia=datos.referencia,
            estado=EstadoPago.PROCESADO, comprobante_id=comp.id,
            concepto=f"Recaudo factura {fac.numero}", recibo_id=rec.id, origen="RECIBO")
        db.add(pago)
        await db.flush()
        if fac.tercero_id is None and tercero is not None:
            fac.tercero_id = tercero.id
        await _actualizar_factura(db, fac)
        await historial(db, fac.id, "PAGO", usuario, f"Pago {rec.numero} por {monto}",
                        {"pago_id": pago.id, "recibo_id": rec.id, "monto": str(monto),
                         "saldo": str(fac.saldo)})

    if datos.comentario and datos.comentario.strip():
        db.add(ERPReciboComentario(recibo_id=rec.id, usuario=usuario, texto=datos.comentario.strip()))
    return rec


# ─── Anular ───────────────────────────────────────────────────────────────────

async def _recibo_bloqueado(db: AsyncSession, recibo_id: int) -> ERPReciboCaja:
    rec = (await db.execute(select(ERPReciboCaja).where(ERPReciboCaja.id == recibo_id)
                            .with_for_update().execution_options(populate_existing=True))
           ).scalar_one_or_none()
    if rec is None:
        raise _rechazo("Ese recibo no existe.", 404)
    return rec


def _motivo(motivo: Optional[str], que: str) -> str:
    if not motivo or not motivo.strip():
        raise _rechazo(f"Indique el motivo para {que}.")
    return motivo.strip()


async def anular(db: AsyncSession, recibo_id: int, motivo: Optional[str], usuario: str) -> ERPReciboCaja:
    """Anula el recibo: reversa su asiento y su movimiento bancario y devuelve a
    cada factura lo que se le había aplicado. El recibo queda, anulado."""
    motivo = _motivo(motivo, "anular el recibo")
    rec = await _recibo_bloqueado(db, recibo_id)
    if rec.estado == "ANULADO":
        raise _rechazo(f"El recibo {rec.numero} ya está anulado.", 409)

    pagos = (await db.execute(select(ERPPago).where(
        ERPPago.recibo_id == rec.id, ERPPago.estado == EstadoPago.PROCESADO))).scalars().all()
    if rec.tipo_ingreso == "ANTICIPO" and any(p.origen == "ANTICIPO" for p in pagos):
        raise _rechazo(f"El anticipo {rec.numero} ya se aplicó a facturas: desaplíquelo primero "
                       "desde cada factura.", 409)
    propios = [p for p in pagos if p.origen == "RECIBO"]
    facturas = await _facturas_bloqueadas(db, [p.factura_cliente_id for p in propios
                                               if p.factura_cliente_id])
    for fac in facturas.values():
        if fac.estado in (EstadoFactura.CASTIGADA, EstadoFactura.ANULADA):
            raise _rechazo(f"La factura {fac.numero} está {_texto_estado(fac)}: el recibo no se "
                           "puede anular.", 409)

    from app.infrastructure.models.erp import ERPComprobante, EstadoComprobante
    reverso = None
    if rec.comprobante_id:
        comp = await db.get(ERPComprobante, rec.comprobante_id)
        if comp is not None and comp.estado != EstadoComprobante.ANULADO:
            reverso = await erp_motor.reversar(db, comp.id, usuario, f"Recibo {rec.numero}: {motivo}")

    for p in propios:
        p.estado = EstadoPago.ANULADO
    await db.flush()
    for fac in facturas.values():
        await _actualizar_factura(db, fac)
        await historial(db, fac.id, "PAGO_ANULADO", usuario, f"Recibo {rec.numero} anulado: {motivo}",
                        {"recibo_id": rec.id, "saldo": str(fac.saldo)})

    if rec.movimiento_bancario_id and rec.cuenta_bancaria_id:
        cuenta = await _cuenta_bloqueada(db, rec.cuenta_bancaria_id, rec.empresa_id)
        cuenta.saldo_contable = r2(cuenta.saldo_contable) - r2(rec.valor_neto)
        db.add(ERPMovimientoBancario(
            cuenta_id=cuenta.id, fecha=reverso.fecha if reverso else erp_motor.hoy_colombia(),
            tipo=TipoMovimientoBancario.DEBITO, monto=r2(rec.valor_neto),
            concepto=f"Anulación del recibo de caja {rec.numero}: {motivo}"[:500],
            referencia=rec.numero, conciliado=False,
            comprobante_id=reverso.id if reverso else None,
            saldo_acumulado=cuenta.saldo_contable))

    rec.estado, rec.saldo_por_aplicar = "ANULADO", Decimal(0)
    rec.anulado_motivo, rec.anulado_por = motivo, usuario
    rec.anulado_en = datetime.now(timezone.utc)
    return rec


async def comentar(db: AsyncSession, recibo_id: int, texto: Optional[str], usuario: str) -> ERPReciboComentario:
    if not texto or not texto.strip():
        raise _rechazo("El comentario está vacío.")
    rec = await db.get(ERPReciboCaja, recibo_id)
    if rec is None:
        raise _rechazo("Ese recibo no existe.", 404)
    com = ERPReciboComentario(recibo_id=rec.id, usuario=usuario, texto=texto.strip())
    db.add(com)
    await db.flush()
    return com


# ─── Anticipos ────────────────────────────────────────────────────────────────

async def saldo_a_favor(db: AsyncSession, tercero_id: Optional[int]) -> Dict[str, Decimal]:
    """Lo que el cliente tiene a favor por anticipos sin aplicar, por moneda."""
    if not tercero_id:
        return {}
    filas = (await db.execute(select(ERPReciboCaja.moneda, func.sum(ERPReciboCaja.saldo_por_aplicar))
                              .where(ERPReciboCaja.tercero_id == tercero_id,
                                     ERPReciboCaja.tipo_ingreso == "ANTICIPO",
                                     ERPReciboCaja.estado == "POR_APLICAR")
                              .group_by(ERPReciboCaja.moneda))).all()
    return {m: r2(v) for m, v in filas if v and r2(v) > 0}


async def anticipos_por_aplicar(db: AsyncSession, tercero_id: Optional[int],
                                moneda: str) -> List[ERPReciboCaja]:
    if not tercero_id:
        return []
    return list((await db.execute(select(ERPReciboCaja).where(
        ERPReciboCaja.tercero_id == tercero_id, ERPReciboCaja.tipo_ingreso == "ANTICIPO",
        ERPReciboCaja.estado == "POR_APLICAR", ERPReciboCaja.moneda == moneda,
        ERPReciboCaja.saldo_por_aplicar > 0).order_by(ERPReciboCaja.fecha, ERPReciboCaja.id))
    ).scalars().all())


async def tercero_de_factura(db: AsyncSession, fac: ERPFacturaCliente) -> Optional[ERPTercero]:
    """El tercero de la factura o, si es antigua y no lo tiene, el de su NIT."""
    if fac.tercero_id:
        return await db.get(ERPTercero, fac.tercero_id)
    nit = identificacion_normalizada(fac.cliente_nit)
    if not nit or fac.empresa_id is None:
        return None
    # El NIT en la factura puede traer el dígito de verificación pegado.
    return (await db.execute(select(ERPTercero).where(
        ERPTercero.empresa_id == fac.empresa_id, ERPTercero.activo.is_(True),
        or_(ERPTercero.numero_identificacion == nit,
            func.concat(ERPTercero.numero_identificacion,
                        func.coalesce(ERPTercero.digito_verificacion, "")) == nit))
        .order_by(ERPTercero.id).limit(1))).scalar_one_or_none()


class AplicarAnticipoEntrada(BaseModel):
    recibo_id: int
    monto: Decimal
    fecha: date = Field(default_factory=date.today)
    confirmar_retenciones: bool = False


async def aplicar_anticipo(db: AsyncSession, fac: ERPFacturaCliente, datos: AplicarAnticipoEntrada,
                           usuario: str) -> ERPPago:
    """Cruza parte o todo un anticipo contra la cartera de una factura.

    Debita anticipos de clientes y acredita cartera: el banco no se mueve, el
    dinero ya había entrado. Bloquea primero el anticipo y luego la factura.
    """
    rec = await _recibo_bloqueado(db, datos.recibo_id)
    fac = (await _facturas_bloqueadas(db, [fac.id]))[fac.id]
    monto = r2(datos.monto)
    if rec.tipo_ingreso != "ANTICIPO" or rec.estado != "POR_APLICAR":
        raise _rechazo(f"El recibo {rec.numero} no es un anticipo con saldo por aplicar.", 409)
    if rec.empresa_id != fac.empresa_id:
        raise _rechazo("El anticipo y la factura son de empresas distintas.")
    tercero = await db.get(ERPTercero, rec.tercero_id) if rec.tercero_id else None
    if not _mismo_cliente(fac, tercero):
        raise _rechazo(f"El anticipo {rec.numero} es de otro cliente.")
    if (fac.moneda or "COP") != rec.moneda:
        raise _rechazo(f"El anticipo está en {rec.moneda} y la factura en {fac.moneda}.")
    if fac.estado not in COBRABLES:
        raise _rechazo(f"La factura {fac.numero} está {_texto_estado(fac)}: no admite pagos.", 409)
    if monto <= 0:
        raise _rechazo("El valor a aplicar debe ser mayor que cero.")
    maximo = min(r2(fac.saldo), r2(rec.saldo_por_aplicar))
    if monto > maximo:
        raise _rechazo(f"Se pueden aplicar hasta {maximo}: el saldo de la factura es "
                       f"{r2(fac.saldo)} y el del anticipo {r2(rec.saldo_por_aplicar)}.")
    tiene_ret = (await db.execute(select(ERPReciboRetencion.id).where(
        ERPReciboRetencion.recibo_id == rec.id).limit(1))).first() is not None
    if tiene_ret and retenciones_de(fac) > 0 and not datos.confirmar_retenciones:
        raise _rechazo("La factura ya registra retenciones y el anticipo también tuvo retenciones: "
                       "revise que no se registre dos veces la misma retención y confirme para "
                       "aplicar.", 409)

    pago = ERPPago(
        empresa_id=fac.empresa_id, tipo=TipoPago.COBRO, numero=rec.numero, fecha=datos.fecha,
        factura_cliente_id=fac.id, cuenta_bancaria_id=None, monto=monto, moneda=rec.moneda,
        metodo_pago="Anticipo", estado=EstadoPago.PROCESADO,
        concepto=f"Aplicación del anticipo {rec.numero} a la factura {fac.numero}",
        recibo_id=rec.id, origen="ANTICIPO")
    db.add(pago)
    await db.flush()
    comp = await erp_motor.asentar(
        db, empresa_id=fac.empresa_id, evento="APLICACION_ANTICIPO", tipo=TipoComprobante.DIARIO,
        fecha=datos.fecha, concepto=f"Aplicación del anticipo {rec.numero} a la factura {fac.numero}",
        lineas=[erp_motor.Linea("anticipo", debito=monto, tercero_id=rec.tercero_id),
                erp_motor.Linea("cartera", credito=monto, tercero_id=fac.tercero_id or rec.tercero_id)],
        usuario=usuario, documento_tipo="aplicacion_anticipo", documento_id=pago.id,
        documento_numero=rec.numero, centro_costo_id=fac.centro_costo_id, moneda=rec.moneda)
    pago.comprobante_id = comp.id

    rec.saldo_por_aplicar = r2(rec.saldo_por_aplicar) - monto
    rec.estado = "POR_APLICAR" if rec.saldo_por_aplicar > 0 else "APLICADO"
    if fac.tercero_id is None:
        fac.tercero_id = rec.tercero_id
    await _actualizar_factura(db, fac)
    await historial(db, fac.id, "ANTICIPO", usuario, f"Anticipo {rec.numero} aplicado por {monto}",
                    {"pago_id": pago.id, "recibo_id": rec.id, "monto": str(monto),
                     "saldo": str(fac.saldo)})
    return pago


async def desaplicar_anticipo(db: AsyncSession, fac: ERPFacturaCliente, pago_id: int,
                              motivo: Optional[str], usuario: str) -> ERPPago:
    """Deshace una aplicación: reversa su asiento y devuelve el valor a la
    factura y al anticipo."""
    motivo = _motivo(motivo, "desaplicar el anticipo")
    pago = await db.get(ERPPago, pago_id)
    if (pago is None or pago.factura_cliente_id != fac.id or pago.origen != "ANTICIPO"
            or pago.estado != EstadoPago.PROCESADO):
        raise _rechazo("Esa aplicación de anticipo no existe en la factura.", 404)
    rec = await _recibo_bloqueado(db, pago.recibo_id)
    fac = (await _facturas_bloqueadas(db, [fac.id]))[fac.id]
    if fac.estado in (EstadoFactura.CASTIGADA, EstadoFactura.ANULADA):
        raise _rechazo(f"La factura {fac.numero} está {_texto_estado(fac)}: no se puede desaplicar.", 409)
    if pago.comprobante_id:
        await erp_motor.reversar(db, pago.comprobante_id, usuario,
                                 f"Desaplicación del anticipo {rec.numero}: {motivo}")
    pago.estado = EstadoPago.ANULADO
    await db.flush()
    rec.saldo_por_aplicar = r2(rec.saldo_por_aplicar) + r2(pago.monto)
    rec.estado = "POR_APLICAR"
    await _actualizar_factura(db, fac)
    await historial(db, fac.id, "ANTICIPO_DESAPLICADO", usuario,
                    f"Anticipo {rec.numero} desaplicado por {r2(pago.monto)}: {motivo}",
                    {"pago_id": pago.id, "recibo_id": rec.id, "saldo": str(fac.saldo)})
    return pago


async def retenciones_anticipo(db: AsyncSession, factura_id: int) -> List[dict]:
    """Las retenciones practicadas en los anticipos aplicados a la factura, en
    proporción a lo aplicado. Solo informativas: ya se contabilizaron al recibir
    el anticipo y no bajan otra vez el saldo."""
    filas = (await db.execute(select(ERPPago, ERPReciboCaja).join(
        ERPReciboCaja, ERPReciboCaja.id == ERPPago.recibo_id).where(
        ERPPago.factura_cliente_id == factura_id, ERPPago.origen == "ANTICIPO",
        ERPPago.estado == EstadoPago.PROCESADO))).all()
    salida = []
    for pago, rec in filas:
        if not r2(rec.valor_bruto):
            continue
        for ret in (await db.execute(select(ERPReciboRetencion).where(
                ERPReciboRetencion.recibo_id == rec.id))).scalars():
            salida.append({"recibo_id": rec.id, "recibo_numero": rec.numero, "tipo": ret.tipo,
                           "concepto": ret.concepto,
                           "valor": r2(r2(ret.valor) * r2(pago.monto) / r2(rec.valor_bruto))})
    return salida


# ─── Facturas por cobrar del cliente ──────────────────────────────────────────

async def facturas_por_cobrar(db: AsyncSession, empresa_id: int, tercero: ERPTercero,
                              moneda: str) -> List[ERPFacturaCliente]:
    """Las facturas cobrables con saldo del cliente en esa moneda, incluidas las
    antiguas que solo guardan su NIT."""
    nit_factura = func.regexp_replace(func.coalesce(ERPFacturaCliente.cliente_nit, ""),
                                      "[^0-9A-Za-z]", "", "g")
    del_cliente = or_(ERPFacturaCliente.tercero_id == tercero.id,
                      and_(ERPFacturaCliente.tercero_id.is_(None),
                           nit_factura.in_(_candidatos_nit(tercero) or ["-"])))
    return list((await db.execute(select(ERPFacturaCliente).where(
        ERPFacturaCliente.empresa_id == empresa_id, ERPFacturaCliente.activo.is_(True),
        ERPFacturaCliente.estado.in_(COBRABLES), ERPFacturaCliente.saldo > 0,
        func.coalesce(ERPFacturaCliente.moneda, "COP") == moneda, del_cliente)
        .order_by(ERPFacturaCliente.fecha_vencimiento, ERPFacturaCliente.id))).scalars().all())


# ─── Pagos anteriores a este submódulo ────────────────────────────────────────

async def agrupar_historicos(db: AsyncSession, lote: int = 500) -> int:
    """Le da un recibo a cada cobro que se registró antes de existir los recibos.

    Idempotente: solo toma los cobros sin `recibo_id`. Conserva el número, la
    fecha, la cuenta y el comprobante del pago; no consume consecutivo ni crea
    movimiento bancario (ya pasó, o no, cuando se registró).
    """
    hechos = 0
    while True:
        filas = (await db.execute(select(ERPPago, ERPFacturaCliente).join(
            ERPFacturaCliente, ERPFacturaCliente.id == ERPPago.factura_cliente_id).where(
            ERPPago.tipo == TipoPago.COBRO, ERPPago.recibo_id.is_(None),
            ERPPago.origen == "RECIBO",
            func.coalesce(ERPPago.empresa_id, ERPFacturaCliente.empresa_id).is_not(None))
            .order_by(ERPPago.id).limit(lote))).all()
        if not filas:
            return hechos
        for pago, fac in filas:
            empresa_id = pago.empresa_id or fac.empresa_id
            tercero = await tercero_de_factura(db, fac)
            numero = pago.numero or f"PAGO-{pago.id}"
            if (await db.execute(select(ERPReciboCaja.id).where(
                    ERPReciboCaja.empresa_id == empresa_id, ERPReciboCaja.numero == numero))).first():
                numero = f"{numero}-{pago.id}"[:50]
            anulado = pago.estado == EstadoPago.ANULADO
            rec = ERPReciboCaja(
                empresa_id=empresa_id, numero=numero, tipo_ingreso="PAGO_FACTURA",
                tercero_id=tercero.id if tercero else None,
                cliente_nombre=(tercero.razon_social if tercero else fac.cliente_nombre) or "",
                cliente_nit=tercero.numero_identificacion if tercero else fac.cliente_nit,
                cuenta_bancaria_id=pago.cuenta_bancaria_id, fecha=pago.fecha,
                medio_pago=pago.metodo_pago, moneda=pago.moneda or fac.moneda or "COP",
                valor_bruto=r2(pago.monto), total_retenciones=0, valor_neto=r2(pago.monto),
                saldo_por_aplicar=0, estado="ANULADO" if anulado else "APLICADO",
                referencia=pago.referencia, comprobante_id=pago.comprobante_id,
                creado_por="migración", historico=True)
            db.add(rec)
            await db.flush()
            pago.recibo_id = rec.id
            hechos += 1
        await db.flush()

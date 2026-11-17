"""
ERP · Ingresos · Pagos recibidos (recibos de caja).

Solo traduce HTTP a `core/erp_recibos.py`, donde viven las reglas. Cuelga del
prefijo /erp, así que lo cobra y lo protege el mismo módulo `erp`.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import erp_permisos, erp_recibos, erp_ventas
from app.core.database import get_db
from app.core.paginacion import pagina
from app.infrastructure.models.erp import (
    ERPBanco, ERPCentroCosto, ERPComprobante, ERPCuentaBancaria, ERPFacturaCliente,
    ERPPlanCuenta, ERPPago, EstadoPago,
)
from app.infrastructure.models.erp_nucleo import ERPTercero
from app.infrastructure.models.erp_recibos import (
    ERPReciboCaja, ERPReciboComentario, ERPReciboLineaOtro, ERPReciboRetencion,
)
from app.infrastructure.models.usuario import Usuario  # noqa: F401

router = APIRouter(prefix="/erp/ingresos/pagos-recibidos", tags=["ERP · Ingresos"])

TOPE_LISTA, TOPE_MAXIMO = 50, 500


def quien(usuario: Usuario) -> str:
    return getattr(usuario, "nombre", None) or getattr(usuario, "username", None) or "sistema"


async def _guardar(db: AsyncSession, operacion):
    """Ejecuta y confirma. Un rechazo deshace todo, consecutivo incluido."""
    try:
        resultado = await operacion
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    return resultado


# ─── Esquemas de respuesta ────────────────────────────────────────────────────

class ReciboFila(BaseModel):
    id: int
    numero: str
    tipo_ingreso: str
    tercero_id: Optional[int] = None
    cliente_nombre: str
    cliente_nit: Optional[str] = None
    detalle: str
    fecha: date
    cuenta_bancaria_id: Optional[int] = None
    cuenta_bancaria: Optional[str] = None
    estado: str
    moneda: str
    valor_bruto: Decimal
    valor_neto: Decimal
    saldo_por_aplicar: Decimal
    historico: bool = False


class FacturaAplicada(BaseModel):
    pago_id: int
    factura_id: int
    factura_numero: str
    factura_fecha: Optional[date] = None
    monto: Decimal
    estado: str


class LineaOtroResp(BaseModel):
    cuenta_id: int
    cuenta_codigo: str
    cuenta_nombre: str
    descripcion: Optional[str] = None
    valor: Decimal


class RetencionResp(BaseModel):
    tipo: str
    nombre: str
    concepto: Optional[str] = None
    base: Decimal
    porcentaje: Decimal
    valor: Decimal
    editada: bool


class AplicacionResp(BaseModel):
    pago_id: int
    factura_id: int
    factura_numero: str
    fecha: date
    monto: Decimal
    estado: str


class ComentarioResp(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    usuario: Optional[str] = None
    texto: str
    created_at: datetime


class ReciboDetalle(ReciboFila):
    forma_pago: Optional[str] = None
    medio_pago: Optional[str] = None
    centro_costo_id: Optional[int] = None
    centro_costo: Optional[str] = None
    referencia: Optional[str] = None
    notas_adicionales: Optional[str] = None
    total_retenciones: Decimal
    comprobante_id: Optional[int] = None
    comprobante_numero: Optional[str] = None
    creado_por: Optional[str] = None
    created_at: Optional[datetime] = None
    anulado_motivo: Optional[str] = None
    anulado_por: Optional[str] = None
    anulado_en: Optional[datetime] = None
    facturas: List[FacturaAplicada] = []
    lineas_otros: List[LineaOtroResp] = []
    retenciones: List[RetencionResp] = []
    aplicaciones: List[AplicacionResp] = []
    comentarios: List[ComentarioResp] = []


# ─── Armado de respuestas ─────────────────────────────────────────────────────

async def _nombres_cuentas(db: AsyncSession, ids) -> Dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    filas = (await db.execute(select(ERPCuentaBancaria, ERPBanco).join(
        ERPBanco, ERPBanco.id == ERPCuentaBancaria.banco_id, isouter=True).where(
        ERPCuentaBancaria.id.in_(ids)))).all()
    return {c.id: " ".join(x for x in ((b.nombre if b else ""), c.numero) if x) for c, b in filas}


async def _detalles(db: AsyncSession, recibos) -> Dict[int, str]:
    """El texto breve de la columna «Detalles»: las facturas cruzadas, «Anticipo»
    o las cuentas del otro ingreso."""
    ids = [r.id for r in recibos]
    if not ids:
        return {}
    facturas: Dict[int, List[str]] = {}
    for rid, numero in (await db.execute(select(ERPPago.recibo_id, ERPFacturaCliente.numero).join(
            ERPFacturaCliente, ERPFacturaCliente.id == ERPPago.factura_cliente_id).where(
            ERPPago.recibo_id.in_(ids), ERPPago.origen == "RECIBO").order_by(ERPPago.id))).all():
        facturas.setdefault(rid, []).append(numero)
    cuentas: Dict[int, List[str]] = {}
    for rid, codigo, nombre in (await db.execute(select(
            ERPReciboLineaOtro.recibo_id, ERPPlanCuenta.codigo, ERPPlanCuenta.nombre).join(
            ERPPlanCuenta, ERPPlanCuenta.id == ERPReciboLineaOtro.cuenta_id).where(
            ERPReciboLineaOtro.recibo_id.in_(ids)).order_by(ERPReciboLineaOtro.id))).all():
        cuentas.setdefault(rid, []).append(f"{codigo} {nombre}")
    salida = {}
    for r in recibos:
        if r.tipo_ingreso == "ANTICIPO":
            salida[r.id] = "Anticipo"
        elif r.tipo_ingreso == "OTROS":
            salida[r.id] = ", ".join(cuentas.get(r.id, [])) or "Otros ingresos"
        else:
            numeros = facturas.get(r.id, [])
            salida[r.id] = ("Facturas " if len(numeros) > 1 else "Factura ") + ", ".join(numeros) if numeros else ""
    return salida


def _fila(r: ERPReciboCaja, detalle: str, cuenta: Optional[str]) -> dict:
    return dict(id=r.id, numero=r.numero, tipo_ingreso=r.tipo_ingreso, tercero_id=r.tercero_id,
                cliente_nombre=r.cliente_nombre, cliente_nit=r.cliente_nit, detalle=detalle,
                fecha=r.fecha, cuenta_bancaria_id=r.cuenta_bancaria_id, cuenta_bancaria=cuenta,
                estado=r.estado, moneda=r.moneda, valor_bruto=r.valor_bruto, valor_neto=r.valor_neto,
                saldo_por_aplicar=r.saldo_por_aplicar, historico=bool(r.historico))


async def detalle_de(db: AsyncSession, rec: ERPReciboCaja) -> ReciboDetalle:
    cuentas = await _nombres_cuentas(db, [rec.cuenta_bancaria_id])
    detalles = await _detalles(db, [rec])
    pagos = (await db.execute(select(ERPPago, ERPFacturaCliente).join(
        ERPFacturaCliente, ERPFacturaCliente.id == ERPPago.factura_cliente_id).where(
        ERPPago.recibo_id == rec.id).order_by(ERPPago.id))).all()
    lineas = (await db.execute(select(ERPReciboLineaOtro, ERPPlanCuenta).join(
        ERPPlanCuenta, ERPPlanCuenta.id == ERPReciboLineaOtro.cuenta_id).where(
        ERPReciboLineaOtro.recibo_id == rec.id).order_by(ERPReciboLineaOtro.id))).all()
    rets = (await db.execute(select(ERPReciboRetencion).where(
        ERPReciboRetencion.recibo_id == rec.id).order_by(ERPReciboRetencion.id))).scalars().all()
    coms = (await db.execute(select(ERPReciboComentario).where(
        ERPReciboComentario.recibo_id == rec.id).order_by(ERPReciboComentario.id))).scalars().all()
    comp = await db.get(ERPComprobante, rec.comprobante_id) if rec.comprobante_id else None
    cc = await db.get(ERPCentroCosto, rec.centro_costo_id) if rec.centro_costo_id else None

    def estado_pago(p):
        return p.estado.value if hasattr(p.estado, "value") else str(p.estado)

    return ReciboDetalle(
        **_fila(rec, detalles.get(rec.id, ""), cuentas.get(rec.cuenta_bancaria_id)),
        forma_pago=rec.forma_pago, medio_pago=rec.medio_pago, centro_costo_id=rec.centro_costo_id,
        centro_costo=f"{cc.codigo} {cc.nombre}" if cc else None, referencia=rec.referencia,
        notas_adicionales=rec.notas_adicionales, total_retenciones=rec.total_retenciones,
        comprobante_id=rec.comprobante_id, comprobante_numero=comp.numero if comp else None,
        creado_por=rec.creado_por, created_at=rec.created_at, anulado_motivo=rec.anulado_motivo,
        anulado_por=rec.anulado_por, anulado_en=rec.anulado_en,
        facturas=[FacturaAplicada(pago_id=p.id, factura_id=f.id, factura_numero=f.numero,
                                  factura_fecha=f.fecha, monto=p.monto, estado=estado_pago(p))
                  for p, f in pagos if p.origen == "RECIBO"],
        aplicaciones=[AplicacionResp(pago_id=p.id, factura_id=f.id, factura_numero=f.numero,
                                     fecha=p.fecha, monto=p.monto, estado=estado_pago(p))
                      for p, f in pagos if p.origen == "ANTICIPO"],
        lineas_otros=[LineaOtroResp(cuenta_id=c.id, cuenta_codigo=c.codigo, cuenta_nombre=c.nombre,
                                    descripcion=l.descripcion, valor=l.valor) for l, c in lineas],
        retenciones=[RetencionResp(tipo=r.tipo, nombre=erp_recibos.NOMBRE_RETENCION.get(r.tipo, r.tipo),
                                   concepto=r.concepto, base=r.base, porcentaje=r.porcentaje,
                                   valor=r.valor, editada=r.editada) for r in rets],
        comentarios=[ComentarioResp.model_validate(c) for c in coms])


async def _recibo(db: AsyncSession, recibo_id: int) -> ERPReciboCaja:
    rec = await db.get(ERPReciboCaja, recibo_id)
    if rec is None:
        raise HTTPException(404, "Ese recibo no existe.")
    return rec


# ─── Apoyo del formulario ─────────────────────────────────────────────────────
# Van antes de `/{recibo_id}` para que la ruta con parámetro no las capture.

@router.get("/permisos")
async def mis_permisos(db: AsyncSession = Depends(get_db),
                       usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Qué botones mostrar: «+ Nuevo ingreso» y «Anular». Es solo para la
    pantalla; cada acción vuelve a exigir su permiso en el servidor."""
    if erp_permisos._es_administrador(usuario):
        return {"registrar": True, "anular": True}
    p = await erp_permisos.permisos_de(db, usuario)
    return {"registrar": bool(p.get("fin_pagos")), "anular": bool(p.get("fin_anular"))}


class ClienteResp(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tipo_identificacion: str
    numero_identificacion: str
    digito_verificacion: Optional[str] = None
    razon_social: str


@router.get("/clientes", response_model=List[ClienteResp])
async def buscar_clientes(buscar: str = "", empresa_id: Optional[int] = None,
                          db: AsyncSession = Depends(get_db),
                          usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Terceros activos de la empresa por identificación o nombre (máximo 20)."""
    emp = await erp_ventas.empresa_de(db, empresa_id)
    q = select(ERPTercero).where(ERPTercero.empresa_id == emp.id, ERPTercero.activo.is_(True))
    texto = buscar.strip()
    if texto:
        aguja = f"%{texto}%"
        numero = erp_recibos.identificacion_normalizada(texto)
        q = q.where(or_(ERPTercero.razon_social.ilike(aguja),
                        ERPTercero.numero_identificacion.ilike(f"%{numero or texto}%")))
    return (await db.execute(q.order_by(ERPTercero.razon_social).limit(20))).scalars().all()


class FacturaPorCobrar(BaseModel):
    id: int
    numero: str
    fecha: date
    fecha_vencimiento: date
    total: Decimal
    saldo: Decimal
    moneda: str
    retencion_fuente: Decimal
    retencion_ica: Decimal
    retencion_iva: Decimal
    vencida: bool


@router.get("/facturas-por-cobrar", response_model=List[FacturaPorCobrar])
async def facturas_por_cobrar(tercero_id: int, moneda: str = "COP",
                              db: AsyncSession = Depends(get_db),
                              usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Las facturas pendientes del cliente, para la tabla «Facturas por cobrar»."""
    t = await db.get(ERPTercero, tercero_id)
    if t is None:
        raise HTTPException(404, "Ese cliente no existe.")
    hoy = date.today()
    return [FacturaPorCobrar(
        id=f.id, numero=f.numero, fecha=f.fecha, fecha_vencimiento=f.fecha_vencimiento,
        total=f.total, saldo=f.saldo, moneda=f.moneda or "COP",
        retencion_fuente=f.retencion_fuente or 0, retencion_ica=f.retencion_ica or 0,
        retencion_iva=f.retencion_iva or 0,
        vencida=bool(f.fecha_vencimiento and f.fecha_vencimiento < hoy))
        for f in await erp_recibos.facturas_por_cobrar(db, t.empresa_id, t, moneda.upper())]


@router.get("/saldo-a-favor")
async def saldo_a_favor(tercero_id: int, db: AsyncSession = Depends(get_db),
                        usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Lo que el cliente tiene a favor por anticipos sin aplicar, por moneda."""
    return {m: str(v) for m, v in (await erp_recibos.saldo_a_favor(db, tercero_id)).items()}


@router.get("/retenciones/conceptos")
async def conceptos_retencion(empresa_id: Optional[int] = None, db: AsyncSession = Depends(get_db),
                              usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    emp = await erp_ventas.empresa_de(db, empresa_id)
    conceptos = await erp_recibos.conceptos_retencion(db, emp.id)
    return [{"tipo": t, "nombre": erp_recibos.NOMBRE_RETENCION[t], "conceptos": conceptos.get(t, [])}
            for t in erp_recibos.PAPEL_RETENCION]


@router.get("/retenciones/sugerida")
async def retencion_sugerida(tipo: Literal["RETEFUENTE", "RETEIVA", "RETEICA"], base: Decimal,
                             tercero_id: Optional[int] = None, concepto: Optional[str] = None,
                             fecha: Optional[date] = None, db: AsyncSession = Depends(get_db),
                             usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """El porcentaje y el valor que sugiere la configuración fiscal, con el porqué."""
    t = await db.get(ERPTercero, tercero_id) if tercero_id else None
    emp = await erp_ventas.empresa_de(db, t.empresa_id if t else None)
    r = await erp_recibos.sugerir_retencion(db, emp.id, t, tipo, concepto, base, fecha or date.today())
    return {"porcentaje": str(r["porcentaje"]), "valor": str(r["valor"]), "motivo": r["motivo"]}


@router.get("/cuentas-contables")
async def cuentas_contables(buscar: str = "", empresa_id: Optional[int] = None,
                            db: AsyncSession = Depends(get_db),
                            usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Cuentas de movimiento del plan de cuentas para «Otros ingresos»
    (máximo 50). Las agrupadoras no aparecen porque no reciben movimiento."""
    emp = await erp_ventas.empresa_de(db, empresa_id)
    q = select(ERPPlanCuenta).where(
        or_(ERPPlanCuenta.empresa_id == emp.id, ERPPlanCuenta.empresa_id.is_(None)),
        ERPPlanCuenta.acepta_movimientos.is_(True), ERPPlanCuenta.activo.is_(True))
    if buscar.strip():
        aguja = f"%{buscar.strip()}%"
        q = q.where(or_(ERPPlanCuenta.codigo.ilike(aguja), ERPPlanCuenta.nombre.ilike(aguja)))
    filas = (await db.execute(q.order_by(ERPPlanCuenta.codigo).limit(50))).scalars().all()
    return [{"id": c.id, "codigo": c.codigo, "nombre": c.nombre} for c in filas]


# ─── Listado ──────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ReciboFila])
async def listar(
    respuesta: Response,
    identificacion: Optional[str] = None,
    cliente: Optional[str] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    cuenta_bancaria_id: Optional[int] = None,
    estado: Optional[Literal["APLICADO", "POR_APLICAR", "ANULADO"]] = None,
    tipo_ingreso: Optional[Literal["PAGO_FACTURA", "ANTICIPO", "OTROS"]] = None,
    monto_min: Optional[Decimal] = None,
    monto_max: Optional[Decimal] = None,
    limite: int = Query(TOPE_LISTA, ge=1, le=TOPE_MAXIMO),
    desplazamiento: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.ver_cartera),
):
    """Los recibos de caja, del más reciente al más antiguo, por páginas.

    Todos los filtros se combinan (Y lógico). El total va en `X-Total-Count`.
    """
    q = select(ERPReciboCaja)
    condiciones = []
    if identificacion and identificacion.strip():
        numero = erp_recibos.identificacion_normalizada(identificacion)
        nit = func.regexp_replace(func.coalesce(ERPReciboCaja.cliente_nit, ""), "[^0-9A-Za-z]", "", "g")
        condiciones.append(nit.ilike(f"%{numero}%"))
    if cliente and cliente.strip():
        condiciones.append(ERPReciboCaja.cliente_nombre.ilike(f"%{cliente.strip()}%"))
    if desde:
        condiciones.append(ERPReciboCaja.fecha >= desde)
    if hasta:
        condiciones.append(ERPReciboCaja.fecha <= hasta)
    if cuenta_bancaria_id:
        condiciones.append(ERPReciboCaja.cuenta_bancaria_id == cuenta_bancaria_id)
    if estado:
        condiciones.append(ERPReciboCaja.estado == estado)
    if tipo_ingreso:
        condiciones.append(ERPReciboCaja.tipo_ingreso == tipo_ingreso)
    if monto_min is not None:
        condiciones.append(ERPReciboCaja.valor_bruto >= monto_min)
    if monto_max is not None:
        condiciones.append(ERPReciboCaja.valor_bruto <= monto_max)
    if condiciones:
        q = q.where(and_(*condiciones))
    q = q.order_by(ERPReciboCaja.fecha.desc(), ERPReciboCaja.numero.desc(), ERPReciboCaja.id.desc())
    recibos = await pagina(db, q, respuesta, limite, desplazamiento)
    cuentas = await _nombres_cuentas(db, [r.cuenta_bancaria_id for r in recibos])
    detalles = await _detalles(db, recibos)
    return [ReciboFila(**_fila(r, detalles.get(r.id, ""), cuentas.get(r.cuenta_bancaria_id)))
            for r in recibos]


# ─── Detalle y acciones ───────────────────────────────────────────────────────

@router.post("", response_model=ReciboDetalle, status_code=201)
async def crear(datos: erp_recibos.ReciboEntrada, db: AsyncSession = Depends(get_db),
                usuario: Usuario = Depends(erp_permisos.registrar_pagos)):
    """«+ Nuevo ingreso»: pago a factura, anticipo u otros ingresos."""
    rec = await _guardar(db, erp_recibos.crear(db, datos, quien(usuario)))
    return await detalle_de(db, await _recibo(db, rec.id))


@router.get("/{recibo_id}", response_model=ReciboDetalle)
async def ver(recibo_id: int, db: AsyncSession = Depends(get_db),
              usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    return await detalle_de(db, await _recibo(db, recibo_id))


class MotivoEntrada(BaseModel):
    motivo: str = ""


@router.post("/{recibo_id}/anular", response_model=ReciboDetalle)
async def anular(recibo_id: int, datos: MotivoEntrada, db: AsyncSession = Depends(get_db),
                 usuario: Usuario = Depends(erp_permisos.anular)):
    await _guardar(db, erp_recibos.anular(db, recibo_id, datos.motivo, quien(usuario)))
    return await detalle_de(db, await _recibo(db, recibo_id))


class ComentarioEntrada(BaseModel):
    texto: str = ""


@router.post("/{recibo_id}/comentarios", response_model=ComentarioResp, status_code=201)
async def comentar(recibo_id: int, datos: ComentarioEntrada, db: AsyncSession = Depends(get_db),
                   usuario: Usuario = Depends(erp_permisos.registrar_pagos)):
    return ComentarioResp.model_validate(
        await _guardar(db, erp_recibos.comentar(db, recibo_id, datos.texto, quien(usuario))))


@router.get("/{recibo_id}/pdf")
async def descargar_pdf(recibo_id: int, db: AsyncSession = Depends(get_db),
                        usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    from app.core.erp_ventas_pdf import pdf_recibo

    rec = await _recibo(db, recibo_id)
    contenido = pdf_recibo(await detalle_de(db, rec), await erp_ventas.empresa_de(db, rec.empresa_id))
    return Response(content=contenido, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{rec.numero}.pdf"'})

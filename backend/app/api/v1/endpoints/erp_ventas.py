"""
ERP · Ingresos · Factura de venta.

Solo traduce HTTP a `core/erp_ventas.py`, donde viven las reglas. Cuelga del
prefijo /erp, así que lo cobra y lo protege el mismo módulo `erp`.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import erp_permisos, erp_ventas
from app.core.erp_recibos import AplicarAnticipoEntrada
from app.core.database import get_db
from app.infrastructure.models.erp import EstadoFactura
from app.infrastructure.models.usuario import Usuario  # noqa: F401

router = APIRouter(prefix="/erp/ingresos/facturas", tags=["ERP · Ingresos"])


def quien(usuario: Usuario) -> str:
    return getattr(usuario, "nombre", None) or getattr(usuario, "username", None) or "sistema"


class LineaVentaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    referencia: Optional[str] = None
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    descuento_pct: Optional[Decimal] = None
    impuesto_id: Optional[int] = None
    subtotal: Decimal
    total_impuesto: Decimal
    total: Decimal


class FacturaVentaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    numero: str
    tipo_documento: str
    estado: EstadoFactura
    estado_dian: Optional[str] = None
    tercero_id: Optional[int] = None
    vendedor_id: Optional[int] = None
    cliente_nombre: str
    cliente_nit: Optional[str] = None
    cliente_email: Optional[str] = None
    fecha: date
    fecha_vencimiento: date
    moneda: Optional[str] = None
    centro_costo_id: Optional[int] = None
    forma_pago: Optional[str] = None
    medio_pago: Optional[str] = None
    subtotal: Decimal
    descuento: Decimal
    total_impuestos: Decimal
    retencion_fuente: Decimal
    retencion_ica: Decimal
    retencion_iva: Decimal
    total: Decimal
    saldo: Decimal
    cufe: Optional[str] = None
    resolucion_id: Optional[int] = None
    remision: Optional[str] = None
    concepto: Optional[str] = None
    observaciones: Optional[str] = None
    notas_adicionales: Optional[str] = None


async def emitir_y_guardar(db: AsyncSession, datos: erp_ventas.FacturaVentaEntrada, usuario: str):
    """Emite y confirma. Cualquier rechazo deshace la transacción completa,
    incluido el consecutivo tomado de la resolución."""
    try:
        fac = await erp_ventas.emitir_factura(db, datos, usuario)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    await db.refresh(fac)
    return fac


class ClienteEncontrado(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tipo_identificacion: str
    numero_identificacion: str
    razon_social: str
    email: Optional[str] = None


@router.get("/clientes/{identificacion}", response_model=ClienteEncontrado)
async def buscar_cliente(
    identificacion: str,
    empresa_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.crear_comprobantes),
):
    """El tercero con esa identificación, para autocompletar el formulario.

    Va aparte de /erp/terceros porque quien factura no necesita ver el maestro
    de terceros completo, solo el cliente que está digitando.
    """
    from sqlalchemy import select
    from app.infrastructure.models.erp_nucleo import ERPTercero

    emp = await erp_ventas.empresa_de(db, empresa_id)
    numero = "".join(c for c in identificacion if c.isalnum())
    t = (await db.execute(select(ERPTercero).where(
        ERPTercero.empresa_id == emp.id, ERPTercero.numero_identificacion == numero,
        ERPTercero.activo.is_(True)))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "No hay un tercero con esa identificación: se creará al guardar.")
    return t


@router.get("/ia/estado")
async def estado_ia(db: AsyncSession = Depends(get_db),
                    usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    """Si el botón del Agente de IA se muestra, y si funciona en este servidor."""
    from app.core import erp_ventas_ia
    permisos = await erp_permisos.permisos_de(db, usuario)
    con_permiso = erp_permisos._es_administrador(usuario) or bool(permisos.get("asistente"))
    if not con_permiso:
        return {"visible": False, "disponible": False, "motivo": None}
    if not erp_ventas_ia.configurada():
        return {"visible": True, "disponible": False,
                "motivo": "La factura asistida por IA no está disponible en este servidor."}
    return {"visible": True, "disponible": True, "motivo": None,
            "formatos": [".pdf", ".jpg", ".jpeg", ".png"], "max_mb": erp_ventas_ia.settings.ERP_IA_MAX_MB}


@router.post("/ia/extraer")
async def extraer_con_ia(archivo: UploadFile = File(...),
                         empresa_id: Optional[int] = None,
                         db: AsyncSession = Depends(get_db),
                         usuario: Usuario = Depends(erp_permisos.usar_asistente)):
    """«Completar usando un archivo o imagen». Devuelve un borrador del formulario;
    no guarda ni emite nada."""
    from app.core import erp_ventas_ia
    emp = await erp_ventas.empresa_de(db, empresa_id)
    contenido = await archivo.read()
    return await erp_ventas_ia.extraer_borrador(db, emp.id, archivo.filename, contenido)


class PagoVentaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    numero: str
    fecha: date
    factura_cliente_id: int
    cuenta_bancaria_id: Optional[int] = None
    monto: Decimal
    metodo_pago: Optional[str] = None
    referencia: Optional[str] = None
    comprobante_id: Optional[int] = None
    # Pagos recibidos: el recibo de caja del cobro y si es un pago (RECIBO) o
    # la aplicación de un anticipo (ANTICIPO).
    recibo_id: Optional[int] = None
    origen: str = "RECIBO"


async def _factura(db: AsyncSession, factura_id: int):
    from app.infrastructure.models.erp import ERPFacturaCliente
    fac = await db.get(ERPFacturaCliente, factura_id)
    if fac is None or not fac.activo:
        raise HTTPException(404, "Esa factura no existe.")
    return fac


@router.post("/{factura_id}/pagos", response_model=PagoVentaResponse, status_code=201)
async def agregar_pago(
    factura_id: int,
    datos: erp_ventas.PagoVentaEntrada,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.registrar_pagos),
):
    """«Agregar pago» del listado y del detalle: abono o pago total."""
    fac = await _factura(db, factura_id)
    await erp_ventas.exigir_accion(db, fac, "agregar_pago")
    try:
        pago = await erp_ventas.registrar_recaudo(db, fac, datos, quien(usuario))
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    await db.refresh(pago)
    return pago


@router.post("", response_model=FacturaVentaResponse, status_code=201)
async def crear_factura(
    datos: erp_ventas.FacturaVentaEntrada,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.crear_comprobantes),
):
    """Crea, numera, contabiliza y emite una factura de venta o un ticket."""
    return await emitir_y_guardar(db, datos, quien(usuario))


# ─── Consulta ─────────────────────────────────────────────────────────────────

from datetime import datetime  # noqa: E402
from typing import Literal  # noqa: E402

from fastapi import Query, Response  # noqa: E402
from sqlalchemy import and_, or_, select  # noqa: E402

from app.core.paginacion import pagina  # noqa: E402
from app.infrastructure.models.erp import ERPFacturaCliente, ERPPago, EstadoPago  # noqa: E402
from app.infrastructure.models.erp_facturacion import (  # noqa: E402
    ERPFacturaClienteAdjunto, ERPFacturaClienteEvento, ERPNotaCreditoCliente,
    ERPNotaDebitoCliente, ERPResolucionFacturacion,
)

TOPE_LISTA, TOPE_MAXIMO = 50, 500


class FacturaListaItem(FacturaVentaResponse):
    estado_visible: str
    vencida: bool


def _con_estado(fac) -> FacturaListaItem:
    return FacturaListaItem(**FacturaVentaResponse.model_validate(fac).model_dump(),
                            **erp_ventas.estado_visible(fac))


@router.get("", response_model=List[FacturaListaItem])
async def listar_facturas(
    respuesta: Response,
    estado: Optional[Literal["por_cobrar", "vencida", "cobrada", "anulada",
                             "borrador", "castigada"]] = None,
    tipo_documento: Optional[Literal["FACTURA_VENTA", "TICKET"]] = None,
    buscar: Optional[str] = None,
    vendedor_id: Optional[int] = None,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    limite: int = Query(TOPE_LISTA, ge=1, le=TOPE_MAXIMO),
    desplazamiento: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.ver_cartera),
):
    """Las facturas de venta, de la más reciente a la más antigua, por páginas.

    El total va en `X-Total-Count`: el listado nunca trae todas las filas. Lista
    también las que emiten POS, WMS y reencauche, porque son las mismas facturas.
    """
    q = select(ERPFacturaCliente).where(ERPFacturaCliente.activo.is_(True))
    por_cobrar = and_(ERPFacturaCliente.estado.in_(erp_ventas.COBRABLES), ERPFacturaCliente.saldo > 0)
    if estado == "por_cobrar":
        q = q.where(por_cobrar)
    elif estado == "vencida":
        q = q.where(por_cobrar, ERPFacturaCliente.fecha_vencimiento < date.today())
    elif estado == "cobrada":
        q = q.where(ERPFacturaCliente.estado == EstadoFactura.PAGADA)
    elif estado:
        q = q.where(ERPFacturaCliente.estado == EstadoFactura(estado.upper()))
    if tipo_documento:
        q = q.where(ERPFacturaCliente.tipo_documento == tipo_documento)
    if vendedor_id:
        q = q.where(ERPFacturaCliente.vendedor_id == vendedor_id)
    if desde:
        q = q.where(ERPFacturaCliente.fecha >= desde)
    if hasta:
        q = q.where(ERPFacturaCliente.fecha <= hasta)
    if buscar and buscar.strip():
        aguja = f"%{buscar.strip()}%"
        q = q.where(or_(ERPFacturaCliente.numero.ilike(aguja),
                        ERPFacturaCliente.cliente_nombre.ilike(aguja),
                        ERPFacturaCliente.cliente_nit.ilike(aguja)))
    q = q.order_by(ERPFacturaCliente.fecha.desc(), ERPFacturaCliente.id.desc())
    return [_con_estado(f) for f in await pagina(db, q, respuesta, limite, desplazamiento)]


class AccionRegla(BaseModel):
    permitida: bool
    motivo: Optional[str] = None


class NotaResumen(BaseModel):
    id: int
    tipo: str
    numero: str
    fecha: date
    motivo: str
    total: Decimal
    creado_por: Optional[str] = None


class AdjuntoResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre: str
    tipo_mime: Optional[str] = None
    tamano: Optional[int] = None
    subido_por: Optional[str] = None
    created_at: datetime


class EventoResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tipo: str
    detalle: Optional[str] = None
    datos: Optional[dict] = None
    usuario: Optional[str] = None
    created_at: datetime


class ResolucionResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    numero_resolucion: str
    fecha_resolucion: date
    prefijo: str
    desde: int
    hasta: int
    vigencia_desde: date
    vigencia_hasta: date


class FacturaDetalle(FacturaListaItem):
    lineas: List[LineaVentaResponse]
    pagos: List[PagoVentaResponse]
    notas: List[NotaResumen]
    adjuntos: List[AdjuntoResumen]
    historial: List[EventoResumen]
    resolucion: Optional[ResolucionResumen] = None
    acciones: dict
    comprobante_id: Optional[int] = None
    cerrada_motivo: Optional[str] = None
    cerrada_por: Optional[str] = None
    retenciones_editadas: bool = False
    tiene_firma: bool = False
    # Retenciones practicadas en los anticipos aplicados (solo informativas).
    retenciones_anticipo: List[dict] = []


async def detalle_de(db: AsyncSession, fac) -> FacturaDetalle:
    from app.infrastructure.models.erp import ERPLineaFacturaCliente

    lineas = (await db.execute(select(ERPLineaFacturaCliente).where(
        ERPLineaFacturaCliente.factura_id == fac.id).order_by(ERPLineaFacturaCliente.id))).scalars().all()
    pagos = (await db.execute(select(ERPPago).where(
        ERPPago.factura_cliente_id == fac.id, ERPPago.estado == EstadoPago.PROCESADO)
        .order_by(ERPPago.fecha, ERPPago.id))).scalars().all()
    notas = [NotaResumen(id=n.id, tipo=tipo, numero=n.numero, fecha=n.fecha, motivo=n.motivo,
                         total=n.total, creado_por=n.creado_por)
             for tipo, modelo in (("CREDITO", ERPNotaCreditoCliente), ("DEBITO", ERPNotaDebitoCliente))
             for n in (await db.execute(select(modelo).where(
                 modelo.factura_id == fac.id).order_by(modelo.id))).scalars().all()]
    adjuntos = (await db.execute(select(ERPFacturaClienteAdjunto).where(
        ERPFacturaClienteAdjunto.factura_id == fac.id).order_by(ERPFacturaClienteAdjunto.id))).scalars().all()
    historial = (await db.execute(select(ERPFacturaClienteEvento).where(
        ERPFacturaClienteEvento.factura_id == fac.id).order_by(ERPFacturaClienteEvento.id))).scalars().all()
    res = await db.get(ERPResolucionFacturacion, fac.resolucion_id) if fac.resolucion_id else None
    from app.core import erp_recibos
    cobrable = fac.estado in erp_ventas.COBRABLES
    return FacturaDetalle(
        **_con_estado(fac).model_dump(),
        lineas=[LineaVentaResponse.model_validate(l) for l in lineas],
        pagos=[PagoVentaResponse.model_validate(p) for p in pagos],
        notas=notas,
        adjuntos=[AdjuntoResumen.model_validate(a) for a in adjuntos],
        historial=[EventoResumen.model_validate(e) for e in historial],
        resolucion=ResolucionResumen.model_validate(res) if res else None,
        acciones=erp_ventas.acciones_permitidas(
            fac, bool(pagos), cobrable and await erp_ventas.cliente_con_anticipos(db, fac)),
        retenciones_anticipo=await erp_recibos.retenciones_anticipo(db, fac.id),
        comprobante_id=fac.comprobante_id, cerrada_motivo=fac.cerrada_motivo,
        cerrada_por=fac.cerrada_por, retenciones_editadas=fac.retenciones_editadas,
        tiene_firma=bool(fac.firma))


@router.get("/{factura_id}", response_model=FacturaDetalle)
async def ver_factura(
    factura_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.ver_cartera),
):
    """La factura completa, con lo que se le puede hacer según su estado."""
    return await detalle_de(db, await _factura(db, factura_id))


@router.get("/{factura_id}/pdf")
async def descargar_pdf(
    factura_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(erp_permisos.ver_cartera),
):
    from app.core.erp_ventas_pdf import pdf_factura

    fac = await _factura(db, factura_id)
    detalle = await detalle_de(db, fac)
    empresa = await erp_ventas.empresa_de(db, fac.empresa_id)
    contenido = pdf_factura(detalle, empresa, firma=fac.firma)
    return Response(content=contenido, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{fac.numero}.pdf"'})


# ─── Ajustes ──────────────────────────────────────────────────────────────────
#
# Cada acción: la factura, la guardia de la matriz (dentro del servicio), el
# cambio y el commit. Un rechazo deshace todo, asiento incluido.

class MotivoEntrada(BaseModel):
    motivo: str = ""


class ComentarioEntrada(BaseModel):
    texto: str


async def _guardar(db: AsyncSession, operacion):
    try:
        resultado = await operacion
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    return resultado


@router.put("/{factura_id}", response_model=FacturaDetalle)
async def editar(factura_id: int, datos: erp_ventas.FacturaVentaEntrada,
                 db: AsyncSession = Depends(get_db),
                 usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_ventas.editar_factura(db, fac, datos, quien(usuario)))
    return await detalle_de(db, await _factura(db, factura_id))


@router.post("/{factura_id}/borrador", response_model=FacturaDetalle)
async def a_borrador(factura_id: int, datos: MotivoEntrada, db: AsyncSession = Depends(get_db),
                     usuario: Usuario = Depends(erp_permisos.anular)):
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_ventas.convertir_borrador(db, fac, datos.motivo, quien(usuario)))
    return await detalle_de(db, fac)


@router.post("/{factura_id}/emitir", response_model=FacturaDetalle)
async def emitir(factura_id: int, db: AsyncSession = Depends(get_db),
                 usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_ventas.emitir_borrador(db, fac, quien(usuario)))
    return await detalle_de(db, fac)


@router.post("/{factura_id}/anular", response_model=FacturaDetalle)
async def anular(factura_id: int, datos: MotivoEntrada, db: AsyncSession = Depends(get_db),
                 usuario: Usuario = Depends(erp_permisos.anular)):
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_ventas.anular(db, fac, datos.motivo, quien(usuario)))
    return await detalle_de(db, fac)


@router.post("/{factura_id}/notas-credito", response_model=NotaResumen, status_code=201)
async def nota_credito(factura_id: int, datos: erp_ventas.NotaEntrada,
                       db: AsyncSession = Depends(get_db),
                       usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    fac = await _factura(db, factura_id)
    n = await _guardar(db, erp_ventas.aplicar_nota(db, fac, "CREDITO", datos, quien(usuario)))
    return NotaResumen(id=n.id, tipo="CREDITO", numero=n.numero, fecha=n.fecha, motivo=n.motivo,
                       total=n.total, creado_por=n.creado_por)


@router.post("/{factura_id}/notas-debito", response_model=NotaResumen, status_code=201)
async def nota_debito(factura_id: int, datos: erp_ventas.NotaEntrada,
                      db: AsyncSession = Depends(get_db),
                      usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    fac = await _factura(db, factura_id)
    n = await _guardar(db, erp_ventas.aplicar_nota(db, fac, "DEBITO", datos, quien(usuario)))
    return NotaResumen(id=n.id, tipo="DEBITO", numero=n.numero, fecha=n.fecha, motivo=n.motivo,
                       total=n.total, creado_por=n.creado_por)


@router.get("/{factura_id}/retenciones/sugeridas")
async def retenciones_sugeridas(factura_id: int, concepto: str = erp_ventas.CONCEPTO_RETEFUENTE,
                                db: AsyncSession = Depends(get_db),
                                usuario: Usuario = Depends(erp_permisos.ver_impuestos)):
    """Lo que el sistema calcula, con el porqué de cada retención."""
    return await erp_ventas.sugerir_retenciones(db, await _factura(db, factura_id), concepto)


@router.put("/{factura_id}/retenciones", response_model=FacturaDetalle)
async def retenciones(factura_id: int, datos: erp_ventas.RetencionesEntrada,
                      db: AsyncSession = Depends(get_db),
                      usuario: Usuario = Depends(erp_permisos.editar_impuestos)):
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_ventas.editar_retenciones(db, fac, datos, quien(usuario)))
    return await detalle_de(db, fac)


# ─── Anticipos (Pagos recibidos) ──────────────────────────────────────────────

class AnticipoDisponible(BaseModel):
    id: int
    numero: str
    fecha: date
    moneda: str
    valor_bruto: Decimal
    saldo_por_aplicar: Decimal
    tiene_retenciones: bool


@router.get("/{factura_id}/anticipos", response_model=List[AnticipoDisponible])
async def anticipos_de_la_factura(factura_id: int, db: AsyncSession = Depends(get_db),
                                  usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Los anticipos del cliente que se pueden aplicar a esta factura."""
    from app.core import erp_recibos
    from app.infrastructure.models.erp_recibos import ERPReciboRetencion

    fac = await _factura(db, factura_id)
    tercero = await erp_recibos.tercero_de_factura(db, fac)
    salida = []
    for r in await erp_recibos.anticipos_por_aplicar(db, tercero.id if tercero else None,
                                                      fac.moneda or "COP"):
        con_ret = (await db.execute(select(ERPReciboRetencion.id).where(
            ERPReciboRetencion.recibo_id == r.id).limit(1))).first() is not None
        salida.append(AnticipoDisponible(id=r.id, numero=r.numero, fecha=r.fecha, moneda=r.moneda,
                                         valor_bruto=r.valor_bruto, saldo_por_aplicar=r.saldo_por_aplicar,
                                         tiene_retenciones=con_ret))
    return salida


@router.post("/{factura_id}/anticipos", response_model=FacturaDetalle, status_code=201)
async def aplicar_anticipo(factura_id: int, datos: AplicarAnticipoEntrada,
                           db: AsyncSession = Depends(get_db),
                           usuario: Usuario = Depends(erp_permisos.registrar_pagos)):
    """«Aplicar anticipo»: cruza el saldo a favor del cliente contra la factura.
    Responde 409 con la advertencia si las retenciones podrían duplicarse; se
    reenvía con `confirmar_retenciones` para aplicar de todos modos."""
    from app.core import erp_recibos
    fac = await _factura(db, factura_id)
    await erp_ventas.exigir_accion(db, fac, "aplicar_anticipo")
    await _guardar(db, erp_recibos.aplicar_anticipo(db, fac, datos, quien(usuario)))
    return await detalle_de(db, await _factura(db, factura_id))


@router.post("/{factura_id}/anticipos/{pago_id}/desaplicar", response_model=FacturaDetalle)
async def desaplicar_anticipo(factura_id: int, pago_id: int, datos: MotivoEntrada,
                              db: AsyncSession = Depends(get_db),
                              usuario: Usuario = Depends(erp_permisos.registrar_pagos)):
    from app.core import erp_recibos
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_recibos.desaplicar_anticipo(db, fac, pago_id, datos.motivo, quien(usuario)))
    return await detalle_de(db, await _factura(db, factura_id))


@router.post("/{factura_id}/castigar", response_model=FacturaDetalle)
async def castigar(factura_id: int, datos: MotivoEntrada, db: AsyncSession = Depends(get_db),
                   usuario: Usuario = Depends(erp_permisos.castigar)):
    """«Cerrar sin pago». Exige el permiso de castigar cartera."""
    fac = await _factura(db, factura_id)
    await _guardar(db, erp_ventas.castigar(db, fac, datos.motivo, quien(usuario)))
    return await detalle_de(db, fac)


@router.post("/{factura_id}/clonar", response_model=FacturaDetalle, status_code=201)
async def clonar(factura_id: int, db: AsyncSession = Depends(get_db),
                 usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    fac = await _factura(db, factura_id)
    nueva = await _guardar(db, erp_ventas.clonar(db, fac, quien(usuario)))
    return await detalle_de(db, nueva)


@router.post("/{factura_id}/comentarios", status_code=201)
async def comentar(factura_id: int, datos: ComentarioEntrada, db: AsyncSession = Depends(get_db),
                   usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    fac = await _factura(db, factura_id)
    if not datos.texto.strip():
        raise HTTPException(422, "El comentario está vacío.")
    await erp_ventas.historial(db, fac.id, "COMENTARIO", quien(usuario), datos.texto.strip())
    await db.commit()
    return {"ok": True}


# ─── Adjuntos ─────────────────────────────────────────────────────────────────

import os  # noqa: E402
import uuid  # noqa: E402

from fastapi import File, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from app.core.config import settings  # noqa: E402


def _carpeta(factura_id: int) -> str:
    return os.path.join(settings.UPLOAD_DIR, "erp_facturas", str(factura_id))


@router.post("/{factura_id}/adjuntos", response_model=AdjuntoResumen, status_code=201)
async def adjuntar(factura_id: int, archivo: UploadFile = File(...),
                   db: AsyncSession = Depends(get_db),
                   usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    """Adjunta un archivo a la factura, sea cual sea su estado."""
    fac = await _factura(db, factura_id)
    contenido = await archivo.read()
    tope = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if not contenido:
        raise HTTPException(422, "El archivo está vacío.")
    if len(contenido) > tope:
        raise HTTPException(413, f"El archivo supera {settings.MAX_UPLOAD_SIZE_MB} MB.")
    # El nombre en disco no viene del usuario: así no hay rutas inventadas.
    nombre = os.path.basename(archivo.filename or "adjunto")[:200]
    os.makedirs(_carpeta(fac.id), exist_ok=True)
    ruta = os.path.join(_carpeta(fac.id), f"{uuid.uuid4().hex}{os.path.splitext(nombre)[1][:10]}")
    with open(ruta, "wb") as f:
        f.write(contenido)
    adj = ERPFacturaClienteAdjunto(factura_id=fac.id, nombre=nombre, ruta=ruta,
                                   tipo_mime=archivo.content_type, tamano=len(contenido),
                                   subido_por=quien(usuario))
    db.add(adj)
    await erp_ventas.historial(db, fac.id, "ADJUNTO", quien(usuario), f"Adjuntó «{nombre}»")
    await db.commit()
    await db.refresh(adj)
    return adj


FIRMAS = {".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}


@router.post("/{factura_id}/firma", response_model=FacturaDetalle)
async def firmar(factura_id: int, archivo: UploadFile = File(...),
                 db: AsyncSession = Depends(get_db),
                 usuario: Usuario = Depends(erp_permisos.crear_comprobantes)):
    """Guarda la firma (imagen PNG o JPG, subida o capturada en pantalla)."""
    fac = await _factura(db, factura_id)
    if fac.estado == EstadoFactura.ANULADA:
        raise HTTPException(409, "La factura está anulada.")
    contenido = await archivo.read()
    ext = os.path.splitext((archivo.filename or "").lower())[1]
    if ext not in FIRMAS or not contenido.startswith(FIRMAS[ext]):
        raise HTTPException(422, "La firma debe ser una imagen PNG o JPG.")
    if len(contenido) > 2 * 1024 * 1024:
        raise HTTPException(413, "La firma supera 2 MB.")
    os.makedirs(_carpeta(fac.id), exist_ok=True)
    ruta = os.path.join(_carpeta(fac.id), f"firma-{uuid.uuid4().hex}{ext}")
    with open(ruta, "wb") as f:
        f.write(contenido)
    fac.firma = ruta
    await erp_ventas.historial(db, fac.id, "FIRMA", quien(usuario), "Firma registrada")
    await db.commit()
    return await detalle_de(db, fac)


@router.get("/{factura_id}/firma")
async def ver_firma(factura_id: int, db: AsyncSession = Depends(get_db),
                    usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    fac = await _factura(db, factura_id)
    if not fac.firma or not os.path.isfile(fac.firma):
        raise HTTPException(404, "La factura no tiene firma.")
    return FileResponse(fac.firma)


@router.get("/{factura_id}/adjuntos/{adjunto_id}")
async def descargar_adjunto(factura_id: int, adjunto_id: int, db: AsyncSession = Depends(get_db),
                            usuario: Usuario = Depends(erp_permisos.ver_cartera)):
    adj = await db.get(ERPFacturaClienteAdjunto, adjunto_id)
    if adj is None or adj.factura_id != factura_id or not os.path.isfile(adj.ruta):
        raise HTTPException(404, "Ese adjunto no existe.")
    return FileResponse(adj.ruta, media_type=adj.tipo_mime or "application/octet-stream",
                        filename=adj.nombre)

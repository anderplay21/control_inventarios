"""
Factura asistida por IA: leer un PDF o una imagen y proponer el formulario.

El agente NO guarda nada. Devuelve un borrador con el mismo formato que el
formulario de nueva factura; la persona lo revisa, completa lo interno (centro
de costo, vendedor) y guarda por el camino normal, con todas sus validaciones.

Usa la clave de Anthropic de TittanBot (`TITTANBOT_API_KEY`) con su propio
modelo y esfuerzo (`ERP_IA_MODELO`, `ERP_IA_ESFUERZO`). Para probar sin red se
instala un extractor de pruebas con `usar_extractor_de_pruebas`.
"""
from __future__ import annotations

import base64
import json
from datetime import date
from decimal import Decimal
from typing import Awaitable, Callable, List, Optional

import anthropic
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

# Firma: (contenido, tipo MIME) → lo que el modelo devolvería, ya como dict.
Extractor = Callable[[bytes, str], Awaitable[dict]]
_extractor_de_pruebas: Optional[Extractor] = None

TIPOS = {"application/pdf": b"%PDF", "image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG"}
EXTENSIONES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

# Los modelos que aceptan el respaldo automático del lado de Anthropic.
CON_RESPALDO = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1",
                "claude-fable-5", "claude-fable-5-2")


def usar_extractor_de_pruebas(extractor: Optional[Extractor]) -> None:
    global _extractor_de_pruebas
    _extractor_de_pruebas = extractor


def proveedor() -> str:
    return "openai" if (settings.ERP_IA_PROVEEDOR or "").strip().lower() == "openai" else "anthropic"


def configurada() -> bool:
    if _extractor_de_pruebas is not None:
        return True
    return bool(settings.OPENAI_API_KEY if proveedor() == "openai" else settings.TITTANBOT_API_KEY)


# ─── Lo que se le pide al modelo ──────────────────────────────────────────────

def _texto(desc: str) -> dict:
    return {"type": ["string", "null"], "description": desc}


def _numero(desc: str) -> dict:
    return {"type": ["number", "null"], "description": desc}


_LINEA = {
    "type": "object",
    "properties": {
        "referencia": _texto("Código o referencia del ítem, si aparece."),
        "descripcion": {"type": "string", "description": "Descripción del producto o servicio."},
        "cantidad": _numero("Cantidad."),
        "precio_unitario": _numero("Precio unitario ANTES de impuestos y descuentos."),
        "descuento_pct": _numero("Descuento de la línea en porcentaje (0-100)."),
        "tarifa_impuesto": _numero("Tarifa de IVA de la línea en porcentaje, p. ej. 19, 5 o 0."),
    },
    "required": ["referencia", "descripcion", "cantidad", "precio_unitario", "descuento_pct",
                 "tarifa_impuesto"],
    "additionalProperties": False,
}

ESQUEMA = {
    "type": "object",
    "properties": {
        "legible": {"type": "boolean",
                    "description": "false si el archivo no es una factura o no se puede leer."},
        "cliente_tipo_identificacion": _texto("NIT, CC, CE, TI o PASAPORTE del comprador."),
        "cliente_identificacion": _texto("Número de identificación del COMPRADOR, sin dígito de verificación."),
        "cliente_nombre": _texto("Nombre o razón social del COMPRADOR (no del emisor)."),
        "cliente_email": _texto("Correo del comprador."),
        "fecha": _texto("Fecha de emisión en formato AAAA-MM-DD."),
        "fecha_vencimiento": _texto("Fecha de vencimiento en formato AAAA-MM-DD."),
        "moneda": _texto("Código ISO de tres letras, p. ej. COP o USD."),
        "forma_pago": _texto("Forma de pago tal como aparece (contado, crédito 30 días...)."),
        "medio_pago": _texto("Medio de pago (efectivo, transferencia, tarjeta...)."),
        "notas": _texto("Notas u observaciones impresas en la factura."),
        "lineas": {"type": "array", "items": _LINEA},
    },
    "required": ["legible", "cliente_tipo_identificacion", "cliente_identificacion", "cliente_nombre",
                 "cliente_email", "fecha", "fecha_vencimiento", "moneda", "forma_pago", "medio_pago",
                 "notas", "lineas"],
    "additionalProperties": False,
}

_INSTRUCCION = (
    "Esta es una factura de venta colombiana. Extrae los datos del COMPRADOR (el "
    "cliente), no los del emisor, y cada línea de ítems. Copia los valores tal como "
    "aparecen; si un dato no está en el documento, devuelve null, no lo inventes. "
    "Los precios unitarios van sin IVA. Si el archivo no es una factura o no se "
    "puede leer, responde con legible=false y el resto en null.")


class _Linea(BaseModel):
    referencia: Optional[str] = None
    descripcion: str = ""
    cantidad: Optional[float] = None
    precio_unitario: Optional[float] = None
    descuento_pct: Optional[float] = None
    tarifa_impuesto: Optional[float] = None


class _Extraido(BaseModel):
    legible: bool
    cliente_tipo_identificacion: Optional[str] = None
    cliente_identificacion: Optional[str] = None
    cliente_nombre: Optional[str] = None
    cliente_email: Optional[str] = None
    fecha: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    moneda: Optional[str] = None
    forma_pago: Optional[str] = None
    medio_pago: Optional[str] = None
    notas: Optional[str] = None
    lineas: List[_Linea] = []


async def _extraer_con_claude(contenido: bytes, tipo: str) -> dict:
    datos = base64.standard_b64encode(contenido).decode("utf-8")
    bloque = ({"type": "document", "source": {"type": "base64", "media_type": tipo, "data": datos}}
              if tipo == "application/pdf" else
              {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": datos}})
    opciones = {"model": settings.ERP_IA_MODELO,
                "output_config": {"effort": settings.ERP_IA_ESFUERZO,
                                  "format": {"type": "json_schema", "schema": ESQUEMA}}}
    # Si el modelo declina por sus filtros, el servidor reintenta con el
    # recomendado en la misma llamada, en vez de dejar al usuario sin respuesta.
    if settings.ERP_IA_MODELO in CON_RESPALDO:
        opciones.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    cliente = anthropic.AsyncAnthropic(api_key=settings.TITTANBOT_API_KEY, timeout=120.0, max_retries=2)
    try:
        resp = await cliente.beta.messages.create(
            max_tokens=16000,
            messages=[{"role": "user", "content": [bloque, {"type": "text", "text": _INSTRUCCION}]}],
            **opciones)
    except anthropic.AuthenticationError:
        raise HTTPException(503, "La clave del servicio de IA no es válida en este servidor.")
    except anthropic.RateLimitError:
        raise HTTPException(429, "El servicio de IA está ocupado. Intente de nuevo en un momento.")
    except anthropic.BadRequestError:
        raise HTTPException(422, "El servicio de IA no pudo procesar ese archivo.")
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        raise HTTPException(502, "El servicio de IA no respondió. Intente de nuevo.")
    finally:
        await cliente.close()
    if resp.stop_reason == "refusal":
        raise HTTPException(422, "El servicio de IA no procesó este archivo.")
    if resp.stop_reason == "max_tokens":
        raise HTTPException(422, "La factura es demasiado larga para leerla de una vez.")
    texto = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(texto)


# ─── Proveedor alternativo: OpenAI ────────────────────────────────────────────
#
# Mismo patrón que las reuniones del CRM (`reuniones/ia.py`): Responses API con
# salida estructurada (`responses.parse` + `text_format`) y `store=False` para
# que la factura no quede guardada del lado de OpenAI. Los modelos de abajo son
# los mismos campos de ESQUEMA, sin valores por omisión: la salida estricta
# exige que todos sean obligatorios (los que pueden faltar admiten null).

class _LineaOpenAI(BaseModel):
    referencia: Optional[str]
    descripcion: str
    cantidad: Optional[float]
    precio_unitario: Optional[float]
    descuento_pct: Optional[float]
    tarifa_impuesto: Optional[float]


class _ExtraidoOpenAI(BaseModel):
    legible: bool
    cliente_tipo_identificacion: Optional[str]
    cliente_identificacion: Optional[str]
    cliente_nombre: Optional[str]
    cliente_email: Optional[str]
    fecha: Optional[str]
    fecha_vencimiento: Optional[str]
    moneda: Optional[str]
    forma_pago: Optional[str]
    medio_pago: Optional[str]
    notas: Optional[str]
    lineas: List[_LineaOpenAI]


async def _extraer_con_openai(contenido: bytes, tipo: str) -> dict:
    import openai

    datos = base64.standard_b64encode(contenido).decode("utf-8")
    archivo = ({"type": "input_file", "filename": "factura.pdf",
                "file_data": f"data:application/pdf;base64,{datos}"}
               if tipo == "application/pdf" else
               {"type": "input_image", "image_url": f"data:{tipo};base64,{datos}"})
    cliente = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY, max_retries=2, timeout=120)
    try:
        r = await cliente.responses.parse(
            model=settings.ERP_IA_MODELO_OPENAI or settings.OPENAI_MODELO_EXTRACCION,
            input=[{"role": "user", "content": [archivo, {"type": "input_text", "text": _INSTRUCCION}]}],
            text_format=_ExtraidoOpenAI,
            store=False,
        )
    except openai.AuthenticationError:
        raise HTTPException(503, "La clave del servicio de IA no es válida en este servidor.")
    except openai.RateLimitError:
        raise HTTPException(429, "El servicio de IA está ocupado. Intente de nuevo en un momento.")
    except openai.BadRequestError:
        raise HTTPException(422, "El servicio de IA no pudo procesar ese archivo.")
    except (openai.APIStatusError, openai.APIConnectionError):
        raise HTTPException(502, "El servicio de IA no respondió. Intente de nuevo.")
    finally:
        await cliente.close()
    if getattr(r, "status", None) == "incomplete":
        raise HTTPException(422, "La factura es demasiado larga para leerla de una vez.")
    if r.output_parsed is None:
        # Sin resultado: el modelo se negó o la respuesta no siguió el esquema.
        raise HTTPException(422, "El servicio de IA no procesó este archivo.")
    return r.output_parsed.model_dump()


# ─── Validación del archivo y armado del borrador ─────────────────────────────

def tipo_de_archivo(nombre: Optional[str], contenido: bytes) -> str:
    """El tipo MIME, por extensión y por sus primeros bytes. Rechaza lo demás."""
    import os
    ext = os.path.splitext((nombre or "").lower())[1]
    tipo = EXTENSIONES.get(ext)
    if tipo is None:
        raise HTTPException(422, "Formato no admitido. Use un archivo PDF, JPG, JPEG o PNG.")
    if not contenido:
        raise HTTPException(422, "El archivo está vacío.")
    if len(contenido) > settings.ERP_IA_MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"El archivo supera el máximo de {settings.ERP_IA_MAX_MB} MB.")
    if not contenido.startswith(TIPOS[tipo]):
        raise HTTPException(422, "El contenido del archivo no corresponde a su extensión.")
    return tipo


def _fecha(valor: Optional[str]) -> Optional[str]:
    try:
        return date.fromisoformat(valor).isoformat() if valor else None
    except ValueError:
        return None


async def _del_catalogo(db, tipo: str, valor: Optional[str]) -> Optional[str]:
    if not valor:
        return None
    from app.api.v1.endpoints.catalogos import resolver_valor_catalogo
    try:
        return await resolver_valor_catalogo(db, "ERP", tipo, valor)
    except ValueError:
        return None


async def extraer_borrador(db: AsyncSession, empresa_id: int, nombre: Optional[str],
                           contenido: bytes) -> dict:
    """Lee el archivo y devuelve el borrador del formulario más las advertencias.

    No escribe en la base: el tercero se busca por NIT, pero no se crea; la forma
    y el medio de pago solo se llenan si existen en el catálogo.
    """
    from app.infrastructure.models.erp import ERPTipoImpuesto, TipoImpuestoEnum
    from app.infrastructure.models.erp_nucleo import ERPTercero

    tipo = tipo_de_archivo(nombre, contenido)
    if not configurada():
        raise HTTPException(503, "La factura asistida por IA no está configurada en este servidor.")
    extractor = _extractor_de_pruebas or (
        _extraer_con_openai if proveedor() == "openai" else _extraer_con_claude)
    try:
        x = _Extraido.model_validate(await extractor(contenido, tipo))
    except (ValidationError, json.JSONDecodeError):
        raise HTTPException(502, "El servicio de IA devolvió una respuesta que no se pudo leer.")
    if not x.legible:
        raise HTTPException(422, "No se pudieron extraer datos de ese archivo. Revise que sea una "
                                 "factura legible.")

    advertencias: List[str] = []
    numero = "".join(c for c in (x.cliente_identificacion or "") if c.isalnum()) or None
    tercero = None
    if numero:
        tercero = (await db.execute(select(ERPTercero).where(
            ERPTercero.empresa_id == empresa_id,
            ERPTercero.numero_identificacion == numero))).scalar_one_or_none()
    if numero and tercero is None:
        advertencias.append("El cliente no existe en Finanzas: se creará al guardar.")

    ivas = {Decimal(str(t.porcentaje)).normalize(): t.id for t in (await db.execute(
        select(ERPTipoImpuesto).where(ERPTipoImpuesto.tipo == TipoImpuestoEnum.IVA,
                                      ERPTipoImpuesto.activo.is_(True)))).scalars()}
    lineas = []
    for ln in x.lineas:
        impuesto_id = None
        if ln.tarifa_impuesto:
            impuesto_id = ivas.get(Decimal(str(ln.tarifa_impuesto)).normalize())
            if impuesto_id is None:
                advertencias.append(f"No hay un impuesto configurado con tarifa {ln.tarifa_impuesto:g} %: "
                                    f"elíjalo en «{ln.descripcion[:40]}».")
        lineas.append({"referencia": ln.referencia, "descripcion": ln.descripcion,
                       "cantidad": ln.cantidad if ln.cantidad is not None else 1,
                       "precio_unitario": ln.precio_unitario or 0,
                       "descuento_pct": ln.descuento_pct or 0, "impuesto_id": impuesto_id})

    forma = await _del_catalogo(db, "FORMA_PAGO", x.forma_pago)
    medio = await _del_catalogo(db, "MEDIO_PAGO", x.medio_pago)
    if x.forma_pago and not forma:
        advertencias.append(f"La forma de pago «{x.forma_pago}» no está en el catálogo: elíjala.")
    if x.medio_pago and not medio:
        advertencias.append(f"El medio de pago «{x.medio_pago}» no está en el catálogo: elíjalo.")
    moneda = (x.moneda or "").strip().upper()

    borrador = {
        "tipo_documento": "FACTURA_VENTA",
        "tercero_id": tercero.id if tercero else None,
        "cliente_tipo_identificacion": (x.cliente_tipo_identificacion or "NIT").upper(),
        "cliente_identificacion": numero,
        "cliente_nombre": x.cliente_nombre or (tercero.razon_social if tercero else ""),
        "cliente_email": x.cliente_email or (tercero.email if tercero else None),
        "fecha": _fecha(x.fecha), "fecha_vencimiento": _fecha(x.fecha_vencimiento),
        "moneda": moneda if len(moneda) == 3 and moneda.isalpha() else "COP",
        "forma_pago": forma, "medio_pago": medio, "notas": x.notas,
        # Lo interno no viene en la factura: lo completa la persona.
        "centro_costo_id": None, "vendedor_id": None,
        "lineas": lineas,
    }
    return {"borrador": borrador, "advertencias": advertencias}

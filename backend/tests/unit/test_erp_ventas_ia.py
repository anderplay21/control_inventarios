"""La llamada a Claude de la factura asistida (core/erp_ventas_ia.py), sin red.

Se sustituye `anthropic.AsyncAnthropic` por uno falso que guarda la petición y
devuelve la respuesta que se le indique.
"""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core import erp_ventas_ia as ia


class _ClienteFalso:
    ultima = None

    def __init__(self, respuesta):
        self._respuesta = respuesta
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))

    async def _crear(self, **kwargs):
        _ClienteFalso.ultima = kwargs
        return self._respuesta

    async def close(self):
        pass


def _respuesta(stop_reason="end_turn", datos=None):
    texto = json.dumps(datos or {"legible": True, "lineas": []})
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=texto)])


@pytest.fixture
def cliente(monkeypatch):
    def instalar(respuesta):
        monkeypatch.setattr(ia.anthropic, "AsyncAnthropic", lambda **kw: _ClienteFalso(respuesta))
    monkeypatch.setattr(ia.settings, "ERP_IA_MODELO", "claude-opus-5-5")
    monkeypatch.setattr(ia.settings, "ERP_IA_ESFUERZO", "medium")
    return instalar


def test_pdf_va_como_documento_con_esquema_y_respaldo(cliente):
    cliente(_respuesta(datos={"legible": True, "cliente_nombre": "X", "lineas": []}))
    pdf = b"%PDF-1.7 contenido"
    datos = asyncio.run(ia._extraer_con_claude(pdf, "application/pdf"))
    assert datos["cliente_nombre"] == "X"
    pedido = _ClienteFalso.ultima
    bloque, instruccion = pedido["messages"][0]["content"]
    assert bloque == {"type": "document", "source": {
        "type": "base64", "media_type": "application/pdf",
        "data": base64.standard_b64encode(pdf).decode()}}
    assert instruccion["type"] == "text" and "COMPRADOR" in instruccion["text"]
    assert pedido["model"] == "claude-opus-5-5"
    assert pedido["output_config"]["effort"] == "medium"
    assert pedido["output_config"]["format"] == {"type": "json_schema", "schema": ia.ESQUEMA}
    assert pedido["fallbacks"] == "default"
    assert pedido["betas"] == ["server-side-fallback-2026-07-01"]


def test_imagen_va_como_bloque_de_imagen(cliente):
    cliente(_respuesta())
    asyncio.run(ia._extraer_con_claude(b"\x89PNG...", "image/png"))
    bloque = _ClienteFalso.ultima["messages"][0]["content"][0]
    assert bloque["type"] == "image" and bloque["source"]["media_type"] == "image/png"


def test_modelo_sin_respaldo_no_pide_fallbacks(cliente, monkeypatch):
    monkeypatch.setattr(ia.settings, "ERP_IA_MODELO", "claude-haiku-5-5")
    cliente(_respuesta())
    asyncio.run(ia._extraer_con_claude(b"%PDF", "application/pdf"))
    assert "fallbacks" not in _ClienteFalso.ultima and "betas" not in _ClienteFalso.ultima


@pytest.mark.parametrize("stop_reason,mensaje", [
    ("refusal", "no procesó"), ("max_tokens", "demasiado larga")])
def test_negativa_o_corte_no_se_toman_como_respuesta(cliente, stop_reason, mensaje):
    cliente(_respuesta(stop_reason=stop_reason))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ia._extraer_con_claude(b"%PDF", "application/pdf"))
    assert exc.value.status_code == 422 and mensaje in exc.value.detail


def test_esquema_estricto():
    """Structured outputs exige todos los campos en `required` y nada adicional."""
    for esquema in (ia.ESQUEMA, ia._LINEA):
        assert esquema["additionalProperties"] is False
        assert set(esquema["required"]) == set(esquema["properties"])


@pytest.mark.parametrize("nombre,contenido,codigo", [
    ("factura.docx", b"PK..", 422), ("factura.pdf", b"", 422),
    ("foto.png", b"%PDF-1.7", 422),            # la extensión no corresponde al contenido
])
def test_archivos_rechazados(nombre, contenido, codigo):
    with pytest.raises(HTTPException) as exc:
        ia.tipo_de_archivo(nombre, contenido)
    assert exc.value.status_code == codigo


def test_tipos_aceptados():
    assert ia.tipo_de_archivo("A.PDF", b"%PDF-1.4") == "application/pdf"
    assert ia.tipo_de_archivo("a.jpeg", b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert ia.tipo_de_archivo("a.jpg", b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert ia.tipo_de_archivo("a.png", b"\x89PNG\r\n") == "image/png"


# ── Proveedor OpenAI ──────────────────────────────────────────────────────────

class _OpenAIFalso:
    ultima = None

    def __init__(self, respuesta):
        self._respuesta = respuesta
        self.responses = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        _OpenAIFalso.ultima = kwargs
        return self._respuesta

    async def close(self):
        pass


@pytest.fixture
def openai_falso(monkeypatch):
    import openai

    def instalar(respuesta):
        monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: _OpenAIFalso(respuesta))
    monkeypatch.setattr(ia.settings, "ERP_IA_PROVEEDOR", "openai")
    monkeypatch.setattr(ia.settings, "ERP_IA_MODELO_OPENAI", "")
    monkeypatch.setattr(ia.settings, "OPENAI_MODELO_EXTRACCION", "modelo-de-reuniones")
    return instalar


def _leido(**cambios):
    base = dict(legible=True, cliente_tipo_identificacion=None, cliente_identificacion=None,
                cliente_nombre="Comprador", cliente_email=None, fecha=None, fecha_vencimiento=None,
                moneda=None, forma_pago=None, medio_pago=None, notas=None, lineas=[])
    base.update(cambios)
    return ia._ExtraidoOpenAI(**base)


def test_openai_pdf_como_archivo_con_salida_estructurada(openai_falso):
    openai_falso(SimpleNamespace(status="completed", output_parsed=_leido()))
    pdf = b"%PDF-1.7"
    datos = asyncio.run(ia._extraer_con_openai(pdf, "application/pdf"))
    assert datos["cliente_nombre"] == "Comprador" and datos["lineas"] == []
    pedido = _OpenAIFalso.ultima
    archivo, instruccion = pedido["input"][0]["content"]
    assert archivo == {"type": "input_file", "filename": "factura.pdf",
                       "file_data": "data:application/pdf;base64," + base64.standard_b64encode(pdf).decode()}
    assert instruccion["type"] == "input_text" and "COMPRADOR" in instruccion["text"]
    assert pedido["text_format"] is ia._ExtraidoOpenAI
    assert pedido["store"] is False
    # Sin modelo propio usa el de extracción de las reuniones.
    assert pedido["model"] == "modelo-de-reuniones"


def test_openai_imagen_como_input_image(openai_falso):
    openai_falso(SimpleNamespace(status="completed", output_parsed=_leido()))
    asyncio.run(ia._extraer_con_openai(b"\xff\xd8\xff", "image/jpeg"))
    archivo = _OpenAIFalso.ultima["input"][0]["content"][0]
    assert archivo["type"] == "input_image" and archivo["image_url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("respuesta,mensaje", [
    (SimpleNamespace(status="completed", output_parsed=None), "no procesó"),
    (SimpleNamespace(status="incomplete", output_parsed=None), "demasiado larga"),
])
def test_openai_sin_resultado(openai_falso, respuesta, mensaje):
    openai_falso(respuesta)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ia._extraer_con_openai(b"%PDF", "application/pdf"))
    assert exc.value.status_code == 422 and mensaje in exc.value.detail


def test_openai_esquema_estricto_sin_valores_por_omision():
    for modelo in (ia._ExtraidoOpenAI, ia._LineaOpenAI):
        assert all(c.is_required() for c in modelo.model_fields.values()), modelo
    assert set(ia._ExtraidoOpenAI.model_fields) == set(ia.ESQUEMA["properties"])
    assert set(ia._LineaOpenAI.model_fields) == set(ia._LINEA["properties"])


def test_configurada_segun_el_proveedor(monkeypatch):
    ia.usar_extractor_de_pruebas(None)
    monkeypatch.setattr(ia.settings, "TITTANBOT_API_KEY", "")
    monkeypatch.setattr(ia.settings, "OPENAI_API_KEY", "sk-prueba")
    monkeypatch.setattr(ia.settings, "ERP_IA_PROVEEDOR", "openai")
    assert ia.proveedor() == "openai" and ia.configurada()
    monkeypatch.setattr(ia.settings, "ERP_IA_PROVEEDOR", "anthropic")
    assert ia.proveedor() == "anthropic" and not ia.configurada()

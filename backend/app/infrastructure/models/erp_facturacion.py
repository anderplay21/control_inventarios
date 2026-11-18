"""
Facturación de venta con resolución DIAN y notas crédito.

Lo que la DIAN exige para numerar: una RESOLUCIÓN de facturación por empresa y
tipo de documento, con prefijo, rango autorizado (desde–hasta), vigencia y la
clave técnica con que se calcula el CUFE. Facturar fuera del rango o de la
vigencia no es válido, así que el número se toma de la resolución —nunca lo
escribe una persona— y el servidor rechaza cuando se agota o vence.

El ENVÍO a la DIAN (XML UBL firmado, a través de un proveedor tecnológico) no
está aquí todavía: el documento queda con su CUFE/CUDE calculado con la fórmula
del anexo técnico y en estado «por transmitir», listo para cuando la empresa
contrate el proveedor.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.infrastructure.models.base import TimestampMixin


class ERPResolucionFacturacion(Base, TimestampMixin):
    __tablename__ = "erp_resolucion_facturacion"
    __table_args__ = (
        sa.UniqueConstraint("empresa_id", "prefijo", "desde", name="uq_erp_resolucion"),
    )

    id = sa.Column(sa.Integer, primary_key=True, index=True)
    empresa_id = sa.Column(sa.Integer, sa.ForeignKey("erp_empresas.id"), nullable=False)
    # FACTURA_VENTA (factura electrónica) · POS (documento equivalente POS
    # electrónico) · NOTA_CREDITO / NOTA_DEBITO (numeración propia de las notas)
    tipo_documento = sa.Column(sa.String(20), nullable=False, default="FACTURA_VENTA")
    numero_resolucion = sa.Column(sa.String(40), nullable=False)
    fecha_resolucion = sa.Column(sa.Date, nullable=False)
    prefijo = sa.Column(sa.String(10), nullable=False)
    desde = sa.Column(sa.BigInteger, nullable=False)
    hasta = sa.Column(sa.BigInteger, nullable=False)
    # Último número usado; el siguiente se toma con UPDATE … RETURNING.
    actual = sa.Column(sa.BigInteger, nullable=False, default=0)
    vigencia_desde = sa.Column(sa.Date, nullable=False)
    vigencia_hasta = sa.Column(sa.Date, nullable=False)
    # Clave técnica (factura) o PIN del software (documento POS): entra al CUFE/CUDE.
    clave_tecnica = sa.Column(sa.String(120))
    # 1 = producción, 2 = pruebas (así lo codifica la DIAN).
    ambiente = sa.Column(sa.String(1), nullable=False, default="2")
    activa = sa.Column(sa.Boolean, nullable=False, default=True)
    aviso_restantes = sa.Column(sa.Integer, nullable=False, default=100)


class ERPNotaCreditoCliente(Base, TimestampMixin):
    """Nota crédito de venta: anula total o parcialmente una factura."""
    __tablename__ = "erp_nota_credito_cliente"

    id = sa.Column(sa.Integer, primary_key=True, index=True)
    empresa_id = sa.Column(sa.Integer, sa.ForeignKey("erp_empresas.id"), nullable=False)
    numero = sa.Column(sa.String(40), nullable=False, unique=True)
    resolucion_id = sa.Column(sa.Integer, sa.ForeignKey("erp_resolucion_facturacion.id"))
    factura_id = sa.Column(sa.Integer, sa.ForeignKey("erp_facturas_cliente.id"), nullable=False, index=True)
    fecha = sa.Column(sa.Date, nullable=False)
    # Concepto DIAN de corrección: 1 devolución parcial, 2 anulación, 3 rebaja,
    # 4 ajuste de precio, 5 otros.
    concepto = sa.Column(sa.String(2), nullable=False, default="1")
    motivo = sa.Column(sa.Text, nullable=False)
    subtotal = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    total_impuestos = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    total = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    cude = sa.Column(sa.String(200))
    estado_dian = sa.Column(sa.String(20), nullable=False, default="POR_TRANSMITIR")
    comprobante_id = sa.Column(sa.Integer, sa.ForeignKey("erp_comprobantes.id"))
    origen = sa.Column(sa.String(20))          # POS, ERP…
    creado_por = sa.Column(sa.String(200))

    lineas = relationship("ERPLineaNotaCredito", back_populates="nota", cascade="all, delete-orphan")


class ERPLineaNotaCredito(Base):
    __tablename__ = "erp_lineas_nota_credito"
    id = sa.Column(sa.Integer, primary_key=True)
    nota_id = sa.Column(sa.Integer, sa.ForeignKey("erp_nota_credito_cliente.id", ondelete="CASCADE"), nullable=False)
    descripcion = sa.Column(sa.String(300), nullable=False)
    cantidad = sa.Column(sa.Numeric(18, 4), nullable=False)
    precio_unitario = sa.Column(sa.Numeric(18, 2), nullable=False)
    tarifa_iva = sa.Column(sa.Numeric(5, 2), nullable=False, default=0)
    subtotal = sa.Column(sa.Numeric(18, 2), nullable=False)
    impuesto = sa.Column(sa.Numeric(18, 2), nullable=False)
    total = sa.Column(sa.Numeric(18, 2), nullable=False)

    nota = relationship("ERPNotaCreditoCliente", back_populates="lineas")


class ERPNotaDebitoCliente(Base, TimestampMixin):
    """Nota débito de venta: aumenta lo que el cliente debe por una factura.

    Espejo simplificado de la nota crédito: sin líneas, con el valor y su impuesto.
    """
    __tablename__ = "erp_nota_debito_cliente"

    id = sa.Column(sa.Integer, primary_key=True, index=True)
    empresa_id = sa.Column(sa.Integer, sa.ForeignKey("erp_empresas.id"), nullable=False)
    numero = sa.Column(sa.String(40), nullable=False, unique=True)
    resolucion_id = sa.Column(sa.Integer, sa.ForeignKey("erp_resolucion_facturacion.id"))
    factura_id = sa.Column(sa.Integer, sa.ForeignKey("erp_facturas_cliente.id"), nullable=False, index=True)
    fecha = sa.Column(sa.Date, nullable=False)
    # Concepto DIAN de la nota débito: 1 intereses, 2 gastos por cobrar,
    # 3 cambio del valor, 4 otros.
    concepto = sa.Column(sa.String(2), nullable=False, default="4")
    motivo = sa.Column(sa.Text, nullable=False)
    subtotal = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    total_impuestos = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    total = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    cude = sa.Column(sa.String(200))
    estado_dian = sa.Column(sa.String(20), nullable=False, default="POR_TRANSMITIR")
    comprobante_id = sa.Column(sa.Integer, sa.ForeignKey("erp_comprobantes.id"))
    creado_por = sa.Column(sa.String(200))


class ERPFacturaClienteEvento(Base, TimestampMixin):
    """Historial de una factura de venta: lo que le pasó, quién y cuándo.

    También guarda los comentarios internos (tipo COMENTARIO), para que el
    detalle muestre una sola línea de tiempo.
    """
    __tablename__ = "erp_factura_cliente_evento"

    id = sa.Column(sa.Integer, primary_key=True)
    factura_id = sa.Column(sa.Integer, sa.ForeignKey("erp_facturas_cliente.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    # EMISION, PAGO, NOTA_CREDITO, NOTA_DEBITO, RETENCIONES, CASTIGO, BORRADOR,
    # ANULACION, EDICION, ADJUNTO, COMENTARIO…
    tipo = sa.Column(sa.String(30), nullable=False)
    detalle = sa.Column(sa.Text)
    datos = sa.Column(JSONB)
    usuario = sa.Column(sa.String(200))


class ERPFacturaClienteAdjunto(Base, TimestampMixin):
    """Archivo adjunto a una factura de venta. El archivo vive en UPLOAD_DIR."""
    __tablename__ = "erp_factura_cliente_adjunto"

    id = sa.Column(sa.Integer, primary_key=True)
    factura_id = sa.Column(sa.Integer, sa.ForeignKey("erp_facturas_cliente.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    nombre = sa.Column(sa.String(255), nullable=False)
    ruta = sa.Column(sa.String(500), nullable=False)
    tipo_mime = sa.Column(sa.String(100))
    tamano = sa.Column(sa.Integer)
    subido_por = sa.Column(sa.String(200))

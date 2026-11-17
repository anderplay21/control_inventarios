"""
Recibos de caja: el dinero que recibe la empresa (Ingresos · Pagos recibidos).

El recibo es el documento; cada factura que cruza es un `ERPPago` con
`recibo_id`. Así la factura de venta sigue leyendo sus pagos como siempre para
su saldo, su historial y su matriz de acciones, y el recibo no duplica esa
cuenta. Un anticipo es un recibo cuyo dinero no se aplicó a ninguna factura: se
aplica después, y cada aplicación es también un `ERPPago` (origen ANTICIPO)
que apunta al recibo del anticipo.

`tipo_ingreso` y `estado` van en texto y no como enum nativo de PostgreSQL:
agregar un valor a un enum nativo exige `ALTER TYPE` en cada esquema.
"""
import sqlalchemy as sa

from app.core.database import Base
from app.infrastructure.models.base import TimestampMixin

TIPOS_INGRESO = ("PAGO_FACTURA", "ANTICIPO", "OTROS")
ESTADOS_RECIBO = ("APLICADO", "POR_APLICAR", "ANULADO")
TIPOS_RETENCION = ("RETEFUENTE", "RETEIVA", "RETEICA")


class ERPReciboCaja(Base, TimestampMixin):
    __tablename__ = "erp_recibos_caja"
    __table_args__ = (
        sa.UniqueConstraint("empresa_id", "numero", name="uq_erp_recibo_numero"),
        sa.Index("ix_erp_recibos_empresa_fecha", "empresa_id", "fecha"),
        sa.Index("ix_erp_recibos_tercero_estado", "tercero_id", "estado"),
    )

    id = sa.Column(sa.Integer, primary_key=True, index=True)
    empresa_id = sa.Column(sa.Integer, sa.ForeignKey("erp_empresas.id"), nullable=False)
    numero = sa.Column(sa.String(50), nullable=False)
    tipo_ingreso = sa.Column(sa.String(20), nullable=False)
    # Obligatorio para los recibos nuevos (lo exige el servicio); nulo solo en
    # los históricos cuya factura no tenía tercero ni coincide por NIT.
    tercero_id = sa.Column(sa.Integer, sa.ForeignKey("erp_terceros.id"), nullable=True)
    cliente_nombre = sa.Column(sa.String(300), nullable=False, default="")
    cliente_nit = sa.Column(sa.String(30))
    cuenta_bancaria_id = sa.Column(sa.Integer, sa.ForeignKey("erp_cuentas_bancarias.id"))
    fecha = sa.Column(sa.Date, nullable=False)
    forma_pago = sa.Column(sa.String(100))
    medio_pago = sa.Column(sa.String(100))
    moneda = sa.Column(sa.String(10), nullable=False, default="COP")
    centro_costo_id = sa.Column(sa.Integer, sa.ForeignKey("erp_centros_costo.id"))
    valor_bruto = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    total_retenciones = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    valor_neto = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    # Lo que queda del anticipo por aplicar a facturas. Cero en los demás tipos.
    saldo_por_aplicar = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    estado = sa.Column(sa.String(20), nullable=False, default="APLICADO")
    notas_adicionales = sa.Column(sa.Text)       # se imprimen en el PDF
    referencia = sa.Column(sa.String(200))
    comprobante_id = sa.Column(sa.Integer, sa.ForeignKey("erp_comprobantes.id"))
    movimiento_bancario_id = sa.Column(sa.Integer, sa.ForeignKey("erp_movimientos_bancarios.id"))
    creado_por = sa.Column(sa.String(200))
    anulado_motivo = sa.Column(sa.Text)
    anulado_por = sa.Column(sa.String(200))
    anulado_en = sa.Column(sa.DateTime(timezone=True))
    # Recibo armado al arrancar a partir de un pago anterior a este submódulo.
    historico = sa.Column(sa.Boolean, nullable=False, default=False)


class ERPReciboLineaOtro(Base):
    """Una línea de «Otros ingresos»: la cuenta del crédito la elige el usuario."""
    __tablename__ = "erp_recibo_lineas_otros"

    id = sa.Column(sa.Integer, primary_key=True)
    recibo_id = sa.Column(sa.Integer, sa.ForeignKey("erp_recibos_caja.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    cuenta_id = sa.Column(sa.Integer, sa.ForeignKey("erp_plan_cuentas.id"), nullable=False)
    descripcion = sa.Column(sa.String(300))
    valor = sa.Column(sa.Numeric(18, 2), nullable=False)


class ERPReciboRetencion(Base):
    """Retención que practicó el cliente sobre un anticipo u otro ingreso."""
    __tablename__ = "erp_recibo_retenciones"

    id = sa.Column(sa.Integer, primary_key=True)
    recibo_id = sa.Column(sa.Integer, sa.ForeignKey("erp_recibos_caja.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    tipo = sa.Column(sa.String(20), nullable=False)      # RETEFUENTE, RETEIVA, RETEICA
    concepto = sa.Column(sa.String(150))
    base = sa.Column(sa.Numeric(18, 2), nullable=False, default=0)
    porcentaje = sa.Column(sa.Numeric(8, 4), nullable=False, default=0)
    valor = sa.Column(sa.Numeric(18, 2), nullable=False)
    # El porcentaje o el valor no son los que sugirió la configuración fiscal.
    editada = sa.Column(sa.Boolean, nullable=False, default=False)


class ERPReciboComentario(Base, TimestampMixin):
    """Comentarios y recordatorios internos: nunca salen en el PDF."""
    __tablename__ = "erp_recibo_comentarios"

    id = sa.Column(sa.Integer, primary_key=True)
    recibo_id = sa.Column(sa.Integer, sa.ForeignKey("erp_recibos_caja.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    usuario = sa.Column(sa.String(200))
    texto = sa.Column(sa.Text, nullable=False)

"""La fecha contable es la de Bogotá, no la UTC."""
from datetime import date, datetime, timezone

from app.core import erp_motor


class _Reloj(datetime):
    ahora = None

    @classmethod
    def now(cls, tz=None):
        return cls.ahora


def test_a_las_8_pm_de_bogota_sigue_siendo_hoy(monkeypatch):
    # 30 de noviembre, 8 p. m. en Bogotá = 1 de diciembre, 1 a. m. UTC.
    _Reloj.ahora = datetime(2026, 12, 1, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(erp_motor, "datetime", _Reloj)
    assert erp_motor.hoy_colombia() == date(2026, 11, 30)


def test_en_la_manana_coincide_con_utc(monkeypatch):
    _Reloj.ahora = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(erp_motor, "datetime", _Reloj)
    assert erp_motor.hoy_colombia() == date(2026, 11, 16)

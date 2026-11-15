"""
Comprueba la sincronización de TittanLite con TittanWare, caso por caso.

La regla: una inspección de Lite entra al seguimiento si la profundidad es igual
o menor que la última y el odómetro igual o mayor; una disposición final entra
si la llanta ya está desmontada (en bodega, en reencauche o de baja) y su
profundidad no sube. Lo demás se queda en Lite con el motivo.

Crea sus propias llantas sobre un vehículo que ya exista y lo hace todo dentro
de una transacción que se DESHACE al terminar: no deja nada en la base.

    PYTHONPATH=/app python tools/probar_sync_lite.py cli_<empresa>
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.main  # noqa: F401  (registra todos los modelos)
from app.api.v1.endpoints import eam_lite as lite
from app.core.database import engine
from app.infrastructure.models.eam import (
    EAMActivo, EAMInspeccionNeumatico, EAMNeumatico, EAMNeumaticoDisposicion,
)
from app.infrastructure.models.lite import LiteDisposicion, LiteInspeccionLlanta

fallos = 0


def comprobar(condicion: bool, texto: str, detalle: str = "") -> None:
    global fallos
    if not condicion:
        fallos += 1
    print(f"{'ok ' if condicion else 'X  '} {texto}{('  → ' + detalle) if detalle and not condicion else ''}")


async def main(esquema: str) -> int:
    usuario = SimpleNamespace(id=1, nombre="Prueba", apellido="Sincronización", username="prueba")
    async with engine.connect() as conexion:
        externa = await conexion.begin()
        db = AsyncSession(bind=conexion, expire_on_commit=False, autoflush=False,
                          join_transaction_mode="create_savepoint", info={"esquema": esquema})
        try:
            activo = (await db.execute(select(EAMActivo).where(
                EAMActivo.tipo_activo == "VEHICULO", EAMActivo.odometro_actual.isnot(None))
                .order_by(EAMActivo.id).limit(1))).scalar()
            if activo is None:
                print("No hay un vehículo con odómetro en", esquema)
                return 2
            aid, odo = activo.id, float(activo.odometro_actual)
            sello = uuid.uuid4().hex[:6].upper()

            def llanta(sufijo: str, estado: str, prof: float, **mas) -> EAMNeumatico:
                n = EAMNeumatico(codigo=f"ZZS{sello}{sufijo}", marca="ZZPRUEBA", referencia="ZZ", medida="295/80R22.5",
                                 estado=estado, profundidad_diseño=18.0, profundidad_actual=prof,
                                 profundidad_externa=prof, profundidad_interna=prof, reencauches=0, **mas)
                db.add(n)
                return n

            a = llanta("A", "INSTALADO", 10.0, activo_id=aid, posicion="ZZ1", km_actual=odo)
            b = llanta("B", "ALMACENADO", 10.0)
            c = llanta("C", "ALMACENADO", 6.0)
            d = llanta("D", "ALMACENADO", 6.0)
            e = llanta("E", "INSTALADO", 7.0, activo_id=aid, posicion="ZZ2", km_actual=odo)
            f = llanta("F", "BAJA", 4.0)
            g = llanta("G", "BAJA", 4.0)
            h = llanta("H", "REENCAUCHE", 5.0)
            await db.commit()
            ids = {k: v.id for k, v in dict(a=a, b=b, c=c, d=d, e=e, f=f, g=g, h=h).items()}
            cod = {k: v.codigo for k, v in dict(a=a, b=b, c=c, d=d, e=e, f=f, g=g, h=h).items()}
            ahora = datetime.now() - timedelta(hours=2)
            paso = iter(range(1, 100))

            async def inspeccionar(codigo, prof, odometro, posicion="ZZ1"):
                r = await lite.crear_inspeccion_lite(lite.InspeccionLiteIn(
                    uuid=uuid.uuid4().hex, activo_id=aid, fecha=ahora + timedelta(minutes=next(paso)),
                    odometro=odometro, llantas=[lite.LlantaLiteIn(
                        posicion=posicion, codigo=codigo, profundidad_interna=prof, profundidad_central=prof,
                        profundidad_externa=prof)]), db=db, usuario=usuario)
                return r["llantas"][0]

            async def disponer(codigo, prof):
                return await lite.crear_disposicion_lite(lite.DisposicionLiteIn(
                    uuid=uuid.uuid4().hex, fecha=ahora + timedelta(minutes=next(paso)), codigo=codigo,
                    profundidad_interna=prof, profundidad_central=prof, profundidad_externa=prof,
                    observaciones="Prueba de sincronización"), db=db, usuario=usuario)

            async def ver(k):
                n = await db.get(EAMNeumatico, ids[k])
                await db.refresh(n)
                return n

            print(f"— Inspecciones (vehículo {activo.placa or activo.codigo}, odómetro {odo:,.0f})")
            r = await inspeccionar(cod["a"], 10.0, odo)
            comprobar(r["sync_estado"] == "SINCRONIZADA", "igual profundidad e igual odómetro: sincroniza", str(r["sync_motivo"]))
            r = await inspeccionar(cod["a"], 9.0, odo + 100)
            n = await ver("a")
            comprobar(r["sync_estado"] == "SINCRONIZADA" and n.profundidad_actual == 9.0 and n.km_actual == odo + 100,
                      "menor profundidad y mayor odómetro: sincroniza y actualiza la llanta", str(r["sync_motivo"]))
            r = await inspeccionar(cod["a"], 11.0, odo + 200)
            comprobar(r["sync_estado"] == "RECHAZADA" and "MAYOR" in (r["sync_motivo"] or ""),
                      "mayor profundidad: se queda en Lite con el motivo", f"{r['sync_estado']} {r['sync_motivo']}")
            r = await inspeccionar(cod["a"], 8.0, odo + 50)
            comprobar(r["sync_estado"] == "RECHAZADA" and "menor" in (r["sync_motivo"] or ""),
                      "menor odómetro: se queda en Lite con el motivo", f"{r['sync_estado']} {r['sync_motivo']}")
            n = await ver("a")
            cuantas = len((await db.execute(select(EAMInspeccionNeumatico.id).where(
                EAMInspeccionNeumatico.neumatico_id == ids["a"]))).all())
            comprobar(n.profundidad_actual == 9.0 and n.km_actual == odo + 100 and cuantas == 2,
                      "lo rechazado no toca la llanta ni deja inspección en TittanWare", f"{n.profundidad_actual} {n.km_actual} {cuantas}")
            r = await inspeccionar(cod["b"], 9.0, odo + 300, posicion="ZZ3")
            comprobar(r["sync_estado"] == "RECHAZADA" and "sin montar" in (r["sync_motivo"] or ""),
                      "llanta que en TittanWare está en bodega: rechazada", f"{r['sync_estado']} {r['sync_motivo']}")
            r = await inspeccionar(f"NOEXISTE{sello}", 9.0, odo + 300, posicion="ZZ4")
            comprobar(r["sync_estado"] == "SIN_SEGUIMIENTO", "llanta que no existe: solo vive en Lite", r["sync_estado"])
            r = await inspeccionar(cod["a"], 8.5, odo + 400)
            comprobar(r["sync_estado"] == "SINCRONIZADA", "después de un rechazo, la siguiente buena sí entra", str(r["sync_motivo"]))

            print("— Disposición final")
            r = await disponer(cod["c"], 5.0)
            n = await ver("c")
            disp = (await db.execute(select(EAMNeumaticoDisposicion).where(
                EAMNeumaticoDisposicion.neumatico_id == ids["c"]))).scalar()
            comprobar(r["sync_estado"] == "SINCRONIZADA" and n.estado == "BAJA" and disp is not None and disp.origen == "LITE",
                      "llanta en bodega, profundidad menor: sincroniza, queda de baja con su disposición",
                      f"{r['sync_estado']} {r['sync_motivo']} {n.estado}")
            r = await disponer(cod["d"], 9.0)
            n = await ver("d")
            comprobar(r["sync_estado"] == "RECHAZADA" and n.estado == "ALMACENADO" and n.profundidad_actual == 6.0,
                      "llanta en bodega, profundidad MAYOR: rechazada y la llanta no cambia",
                      f"{r['sync_estado']} {r['sync_motivo']} {n.estado} {n.profundidad_actual}")
            r = await disponer(cod["e"], 6.0)
            n = await ver("e")
            comprobar(r["sync_estado"] == "RECHAZADA" and n.estado == "INSTALADO" and n.activo_id == aid,
                      "llanta todavía MONTADA: rechazada y sigue en el vehículo",
                      f"{r['sync_estado']} {r['sync_motivo']} {n.estado}")
            lite_e = r["id"]
            r = await disponer(cod["f"], 3.0)
            comprobar(r["sync_estado"] == "SINCRONIZADA", "llanta ya de baja, profundidad menor: sincroniza", f"{r['sync_estado']} {r['sync_motivo']}")
            r = await disponer(cod["g"], 8.0)
            n = await ver("g")
            comprobar(r["sync_estado"] == "RECHAZADA" and n.profundidad_actual == 4.0,
                      "llanta ya de baja, profundidad MAYOR: rechazada", f"{r['sync_estado']} {r['sync_motivo']} {n.profundidad_actual}")
            r = await disponer(cod["h"], 5.0)
            comprobar(r["sync_estado"] == "SINCRONIZADA", "llanta en reencauche (desmontada), igual profundidad: sincroniza",
                      f"{r['sync_estado']} {r['sync_motivo']}")
            r = await disponer(f"NOEXISTE{sello}", 5.0)
            comprobar(r["sync_estado"] == "SIN_SEGUIMIENTO", "llanta que no existe: solo vive en Lite", r["sync_estado"])
            r = await disponer(cod["c"], 4.0)
            comprobar(r["sync_estado"] == "RECHAZADA" and "ya tiene" in (r["sync_motivo"] or ""),
                      "segunda disposición de la misma llanta: rechazada", f"{r['sync_estado']} {r['sync_motivo']}")

            print("— Reintentos")
            n = await ver("e")
            n.estado, n.activo_id, n.posicion = "ALMACENADO", None, None      # el analista la desmonta en TittanWare
            await db.commit()
            r = await lite.resincronizar_disposicion(lite_e, db=db, usuario=usuario)
            comprobar(r["sync_estado"] == "SINCRONIZADA", "desmontada después en TittanWare, al reintentar entra",
                      f"{r['sync_estado']} {r['sync_motivo']}")
            if hasattr(lite, "sincronizar_pendientes"):
                n = await ver("b")
                n.estado, n.activo_id, n.posicion, n.km_actual = "INSTALADO", aid, "ZZ3", odo
                await db.commit()
                hechas, corte, desde = 0, None, 0
                for _ in range(40):          # por tandas, hasta que no falte nada
                    r = await lite.sincronizar_pendientes(corte=corte, desde_disposicion=desde, db=db, usuario=usuario)
                    hechas += r["inspecciones"]["sincronizadas"]
                    corte, desde = datetime.fromisoformat(r["corte"]), r["desde_disposicion"]
                    if not r["faltan"]:
                        break
                ln = (await db.execute(select(LiteInspeccionLlanta).where(
                    LiteInspeccionLlanta.codigo == cod["b"]))).scalar()
                await db.refresh(ln)
                comprobar(ln.sync_estado == "SINCRONIZADA" and hechas >= 1 and not r["faltan"],
                          "«sincronizar pendientes» recoge lo que ya se puede", f"{ln.sync_estado} {ln.sync_motivo} {r}")
                otra = (await db.execute(select(LiteDisposicion).where(LiteDisposicion.codigo == cod["g"]))).scalar()
                await db.refresh(otra)
                comprobar(otra.sync_estado == "RECHAZADA", "y deja rechazado lo que sigue sin cumplir", otra.sync_estado)
        finally:
            await db.close()
            await externa.rollback()
    print("FALLOS:", fallos) if fallos else print("TODO BIEN (nada quedó en la base)")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "cli_demo")))

/**
 * Inicio de TittanLite: buscar el vehículo a inspeccionar, registrar una
 * disposición final y ver qué se envió (y qué espera señal).
 */
import { useMemo, useState } from 'react'
import { ScrollView, View } from 'react-native'
import { router } from 'expo-router'
import { CircleSlash, Container, Forklift, LogOut, RefreshCw, Search, Send, Settings, Truck, type LucideIcon } from 'lucide-react-native'
import {
  Acceso, Aparecer, Avatar, AvisoSincronizacion, Boton, Campo, Chip, ESPACIO, Esqueleto, EstadoVacio, Fila, HojaInferior,
  Seccion, Tarjeta, Texto, useTema, type TonoChip,
} from '@/diseno'
import { useSesion } from '@/datos/sesion'
import { useCola, useDatosLite } from '@/datos/lite'
import { VERSION_APP } from '@/datos/config'
import { BotonCabecera, CabeceraLite, CifraCabecera, fechaDeHoy } from '@/lite/marca'

const TONO_SYNC: Record<string, TonoChip> = {
  SINCRONIZADA: 'exito', PARCIAL: 'alerta', RECHAZADA: 'peligro', SIN_SEGUIMIENTO: 'neutro', PENDIENTE: 'acento',
}
const TEXTO_SYNC: Record<string, string> = {
  SINCRONIZADA: 'En TittanWare', PARCIAL: 'Parcial', RECHAZADA: 'Solo en Lite', SIN_SEGUIMIENTO: 'Solo en Lite', PENDIENTE: 'Pendiente',
}
const ICONO_TIPO: Record<string, LucideIcon> = { REMOLQUE: Container, MONTACARGAS: Forklift }

function saludo() {
  const h = new Date().getHours()
  return h < 12 ? 'Buenos días' : h < 19 ? 'Buenas tardes' : 'Buenas noches'
}

/** Hace cuánto fue la última inspección, corto: «hoy», «hace 3 d», «hace 2 m». */
function haceCuanto(fecha: string): { texto: string; tono: TonoChip } {
  const dias = Math.max(0, Math.floor((Date.now() - new Date(fecha).getTime()) / 86400000))
  const texto = dias === 0 ? 'Hoy' : dias < 31 ? `Hace ${dias} d` : dias < 365 ? `Hace ${Math.floor(dias / 30)} m` : 'Hace +1 año'
  return { texto, tono: dias <= 30 ? 'exito' : dias <= 60 ? 'moderada' : 'alerta' }
}

export default function InicioLite() {
  const { usuario, nombreEmpresa, salir } = useSesion()
  const { vehiculos, enLinea, cargando, actualizar } = useDatosLite()
  const { cola, hechos, ultima, sincronizar } = useCola()
  const [buscar, setBuscar] = useState('')
  const [perfil, setPerfil] = useState(false)
  const { color } = useTema()

  const lista = useMemo(() => {
    const t = buscar.trim().toLowerCase()
    const todos = vehiculos ?? []
    return (t ? todos.filter(v => [v.placa, v.codigo, v.nombre, v.marca].some(x => (x ?? '').toLowerCase().includes(t))) : todos).slice(0, 40)
  }, [vehiculos, buscar])
  const nombre = usuario ? `${usuario.nombre} ${usuario.apellido}`.trim() : ''
  const conError = cola.filter(c => c.error).length
  const total = vehiculos?.length ?? 0

  return (
    <View style={{ flex: 1, backgroundColor: color.fondo }}>
      <CabeceraLite titulo="TittanLite" subtitulo={nombreEmpresa ?? undefined}
        accion={<View style={{ flexDirection: 'row', gap: ESPACIO.e2, alignItems: 'center' }}>
          <BotonCabecera icono={RefreshCw} etiqueta="Actualizar" onPress={() => { actualizar(); sincronizar() }} />
          <BotonCabecera icono={Settings} etiqueta="Ajustes de inspección" onPress={() => router.push('/lite/ajustes')} />
          <Avatar nombre={nombre || 'Usuario'} tamano={42} onPress={() => setPerfil(true)} />
        </View>}>
        <View style={{ gap: 2 }}>
          <Texto variante="titulo" lineas={1} style={{ color: color.sobreMarca }}>{saludo()}{usuario?.nombre ? `, ${usuario.nombre}` : ''}</Texto>
          <Texto variante="nota" style={{ color: color.sobreMarcaSuave }}>
            {fechaDeHoy()}
          </Texto>
        </View>
        <View style={{ flexDirection: 'row', gap: ESPACIO.e2 }}>
          <CifraCabecera valor={vehiculos ? total : '—'} rotulo="Vehículos" />
          <CifraCabecera valor={hechos.length} rotulo="Enviados" tono={hechos.length ? 'exito' : undefined} />
          <CifraCabecera valor={cola.length} rotulo="Por enviar" tono={cola.length ? 'alerta' : undefined} />
        </View>
      </CabeceraLite>

      <ScrollView contentContainerStyle={{ padding: ESPACIO.e4, gap: ESPACIO.e5, paddingBottom: ESPACIO.e10 }} keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}>
        <Aparecer>
          <AvisoSincronizacion enLinea={enLinea} pendientes={cola.length} rechazados={conError}
            ultima={ultima ?? undefined} onSincronizar={sincronizar} />
        </Aparecer>

        <Aparecer orden={1} style={{ flexDirection: 'row', gap: ESPACIO.e3 }}>
          <Acceso destacado titulo="Enviar reportes" detalle="PDF y Excel por período" icono={Send} onPress={() => router.push('/lite/enviar')} />
          <Acceso titulo="Disposición final" detalle="Registrar una llanta desechada" icono={CircleSlash} onPress={() => router.push('/lite/disposicion')} />
        </Aparecer>

        <Aparecer orden={2}>
          <Seccion titulo={`Inspeccionar un vehículo${total ? ` · ${total}` : ''}`}>
            <Campo sinRotulo etiqueta="Buscar placa o código" valor={buscar} onCambio={setBuscar} icono={Search} sinMayusculas />
            <View style={{ marginTop: ESPACIO.e1 }}>
              {cargando && !vehiculos && (
                <Tarjeta style={{ gap: ESPACIO.e5 }}>
                  {[0, 1, 2, 3].map(i => (
                    <View key={i} style={{ flexDirection: 'row', gap: ESPACIO.e3, alignItems: 'center' }}>
                      <Esqueleto alto={38} ancho={38} radio={12} />
                      <View style={{ flex: 1, gap: 8 }}><Esqueleto alto={14} ancho="55%" /><Esqueleto alto={12} ancho="80%" /></View>
                    </View>
                  ))}
                </Tarjeta>
              )}
              {vehiculos && lista.length === 0 && (
                <Tarjeta><EstadoVacio icono={Truck} titulo="Ningún vehículo coincide" detalle="Los vehículos se crean en TittanWare." /></Tarjeta>
              )}
              {lista.length > 0 && (
                <Tarjeta relleno={0} style={{ overflow: 'hidden' }}>
                  {lista.map((v, i) => {
                    const montadas = v.posiciones.filter(p => p.llanta).length
                    const ult = v.ultima_inspeccion?.fecha
                    const hace = ult ? haceCuanto(ult) : null
                    return (
                      <Fila key={v.activo_id} icono={ICONO_TIPO[(v.tipo ?? '').toUpperCase()] ?? Truck} titulo={v.placa || v.codigo}
                        detalle={[v.marca, `${montadas}/${v.posiciones.length} llantas`].filter(Boolean).join(' · ')}
                        derecha={<Chip texto={hace?.texto ?? 'Sin inspección'} tono={hace?.tono ?? 'neutro'} punto={!!hace} />}
                        onPress={() => router.push(`/lite/inspeccion/${v.activo_id}`)} ultima={i === lista.length - 1} />
                    )
                  })}
                </Tarjeta>
              )}
              {vehiculos && total > lista.length && !buscar.trim() && (
                <Texto variante="nota" tono="terciario" style={{ marginTop: ESPACIO.e2, textAlign: 'center' }}>
                  Se muestran {lista.length} de {total}. Busque por placa para ver los demás.
                </Texto>
              )}
            </View>
          </Seccion>
        </Aparecer>

        {(cola.length > 0 || hechos.length > 0) && (
          <Seccion titulo="Registros de este teléfono">
            <Tarjeta relleno={0} style={{ overflow: 'hidden' }}>
              {cola.map(c => (
                <Fila key={c.uuid} titulo={`${c.tipo === 'inspeccion' ? 'Inspección' : 'Disposición'} · ${c.placa}`}
                  icono={c.tipo === 'inspeccion' ? Truck : CircleSlash} colorIcono={c.error ? color.peligro : color.alerta}
                  detalle={c.error ?? 'Esperando señal para enviar'}
                  derecha={<Chip texto={c.error ? 'Con error' : 'Por enviar'} tono={c.error ? 'peligro' : 'acento'} />} />
              ))}
              {hechos.slice(0, 15).map((h, i) => (
                <Fila key={h.uuid} titulo={`${h.tipo === 'inspeccion' ? 'Inspección' : 'Disposición'} · ${h.placa}`}
                  icono={h.tipo === 'inspeccion' ? Truck : CircleSlash}
                  detalle={`${new Date(h.fecha).toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' })} · ${h.resumen}`}
                  derecha={<Chip texto={TEXTO_SYNC[h.estado] ?? h.estado} tono={TONO_SYNC[h.estado] ?? 'neutro'} />}
                  ultima={i === Math.min(hechos.length, 15) - 1} />
              ))}
            </Tarjeta>
            <Texto variante="nota" tono="terciario" style={{ marginTop: ESPACIO.e1 }}>
              «Solo en Lite»: la llanta no está en TittanWare o no pasó sus reglas (la profundidad subió, el odómetro bajó
              o la llanta sigue montada). El registro queda y sale en el informe.
            </Texto>
          </Seccion>
        )}
      </ScrollView>

      <HojaInferior visible={perfil} onCerrar={() => setPerfil(false)} titulo={nombre}>
        <Texto tono="secundario">{nombreEmpresa} · TittanLite {VERSION_APP}</Texto>
        <View style={{ marginTop: ESPACIO.e4 }}>
          <Boton titulo="Cerrar sesión" icono={LogOut} variante="peligro" ancho onPress={() => { setPerfil(false); salir() }} />
        </View>
      </HojaInferior>
    </View>
  )
}

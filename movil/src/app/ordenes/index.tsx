/**
 * Mis órdenes: las abiertas asignadas al técnico, o todas las abiertas de la
 * empresa. Las más urgentes y las que ya están en curso van primero.
 */
import { useMemo, useState } from 'react'
import { ScrollView, View } from 'react-native'
import { router } from 'expo-router'
import { ClipboardList, LogOut, RefreshCw, Wrench } from 'lucide-react-native'
import { ESTADO_OT, PRIORIDAD_OT, type EstadoOT, type PrioridadOT } from '@compartido/index'
import {
  Aparecer, Avatar, Boton, Chip, ESPACIO, Esqueleto, EstadoVacio, Fila, HojaInferior, Segmentado, Tarjeta,
  Texto, useTema,
} from '@/diseno'
import { BotonCabecera, CabeceraLite, CifraCabecera, fechaDeHoy } from '@/lite/marca'
import { useSesion } from '@/datos/sesion'
import { TONO_ESTADO, esMia, nombreActivo, useOrdenesAbiertas } from '@/datos/ordenes'
import { VERSION_APP } from '@/datos/config'

function saludo() {
  const h = new Date().getHours()
  return h < 12 ? 'Buenos días' : h < 19 ? 'Buenas tardes' : 'Buenas noches'
}

export default function Ordenes() {
  const { color } = useTema()
  const { usuario, nombreEmpresa, salir } = useSesion()
  const { ordenes, activos, error, cargando, recargar } = useOrdenesAbiertas()
  const [vista, setVista] = useState<'mias' | 'todas'>('mias')
  const [perfil, setPerfil] = useState(false)

  const mias = useMemo(() => (ordenes ?? []).filter(o => esMia(o, usuario)), [ordenes, usuario])
  const lista = vista === 'mias' ? mias : (ordenes ?? [])
  const enCurso = mias.filter(o => o.estado === 'EN_EJECUCION').length
  const nombre = usuario ? `${usuario.nombre} ${usuario.apellido}`.trim() : ''

  return (
    <View style={{ flex: 1, backgroundColor: color.fondo }}>
      <CabeceraLite titulo="TittanWare" subtitulo={nombreEmpresa ?? undefined}
        accion={<View style={{ flexDirection: 'row', gap: ESPACIO.e2, alignItems: 'center' }}>
          <BotonCabecera icono={RefreshCw} etiqueta="Actualizar" onPress={recargar} />
          <Avatar nombre={nombre || 'Usuario'} tamano={42} onPress={() => setPerfil(true)} />
        </View>}>
        <View style={{ gap: 2 }}>
          <Texto variante="titulo" lineas={1} accessibilityRole="header" style={{ color: color.sobreMarca }}>{saludo()}{usuario?.nombre ? `, ${usuario.nombre}` : ''}</Texto>
          <Texto variante="nota" style={{ color: color.sobreMarcaSuave }}>
            {fechaDeHoy()}
          </Texto>
        </View>
        <View style={{ flexDirection: 'row', gap: ESPACIO.e2 }}>
          <CifraCabecera valor={ordenes ? mias.length : '—'} rotulo="Asignadas" />
          <CifraCabecera valor={ordenes ? enCurso : '—'} rotulo="En curso" tono={enCurso ? 'exito' : undefined} />
          <CifraCabecera valor={ordenes ? ordenes.length : '—'} rotulo="Abiertas" />
        </View>
      </CabeceraLite>
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ padding: ESPACIO.e4, gap: ESPACIO.e4, paddingBottom: ESPACIO.e10 }}>

      <Segmentado valor={vista} onCambio={setVista} opciones={[
        { valor: 'mias', texto: `Mías${ordenes ? ` · ${mias.length}` : ''}` },
        { valor: 'todas', texto: `Todas abiertas${ordenes ? ` · ${ordenes.length}` : ''}` },
      ]} />

      {error ? (
        <Tarjeta><EstadoVacio icono={RefreshCw} titulo="No se pudieron cargar" detalle={error} accion="Reintentar" onAccion={recargar} /></Tarjeta>
      ) : !ordenes ? (
        <Tarjeta style={{ gap: ESPACIO.e5 }}>
          {[0, 1, 2, 3].map(i => (
            <View key={i} style={{ flexDirection: 'row', gap: ESPACIO.e3, alignItems: 'center' }}>
              <Esqueleto alto={40} ancho={40} radio={12} />
              <View style={{ flex: 1, gap: 8 }}><Esqueleto alto={14} ancho="75%" /><Esqueleto alto={12} ancho="50%" /></View>
            </View>
          ))}
        </Tarjeta>
      ) : lista.length === 0 ? (
        <Tarjeta>
          <EstadoVacio icono={ClipboardList}
            titulo={vista === 'mias' ? 'No tiene órdenes asignadas' : 'No hay órdenes abiertas'}
            detalle={vista === 'mias' ? 'Cuando le asignen una orden en el portal aparecerá aquí.' : undefined}
            accion={vista === 'mias' && ordenes.length ? 'Ver todas las abiertas' : undefined} onAccion={() => setVista('todas')} />
        </Tarjeta>
      ) : (
        <Aparecer>
        <Tarjeta relleno={0} style={{ overflow: 'hidden', opacity: cargando ? 0.6 : 1 }}>
          {lista.map((o, i) => (
            <Fila key={o.id} titulo={o.descripcion} detalle={`${o.numero} · ${nombreActivo(activos[o.activo_id])}`}
              icono={Wrench} colorIcono={PRIORIDAD_OT[o.prioridad as PrioridadOT]?.color}
              onPress={() => router.push({ pathname: '/ordenes/[id]', params: { id: String(o.id) } })}
              ultima={i === lista.length - 1}
              derecha={<Chip texto={ESTADO_OT[o.estado as EstadoOT]?.corto ?? o.estado ?? ''} tono={TONO_ESTADO[o.estado ?? ''] ?? 'neutro'} />} />
          ))}
        </Tarjeta>
        </Aparecer>
      )}
      </ScrollView>

      <HojaInferior visible={perfil} onCerrar={() => setPerfil(false)} titulo={nombre}>
        <Texto tono="secundario">{usuario?.username} · {nombreEmpresa}</Texto>
        <Texto variante="nota" tono="terciario">TittanWare {VERSION_APP}</Texto>
        <Boton titulo="Salir de este teléfono" variante="peligro" icono={LogOut} ancho tamano="grande"
          onPress={() => { setPerfil(false); salir() }} />
      </HojaInferior>
    </View>
  )
}

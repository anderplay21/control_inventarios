/**
 * La identidad de TittanWare en las apps: el logo corporativo (aro y marca,
 * el mismo del ícono) y la cabecera de marca —degradado oscuro con el
 * resplandor del acento— que usan TittanLite y TittanWare.
 */
import type { ReactNode } from 'react'
import { Image, Pressable, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { ArrowLeft, type LucideIcon } from 'lucide-react-native'
import { ESPACIO, FondoMarca, RADIO, Texto, useTema } from '@/diseno'

const LOGO = require('../../assets/marca/logo.png')
const LOGO_PEQUENO = require('../../assets/marca/logo-pequeno.png')

/** El logo en su disco blanco. Pequeño, sin el lema (a ese tamaño no se lee). */
export function LogoTW({ tamano = 40 }: { tamano?: number }) {
  return (
    <Image source={tamano > 72 ? LOGO : LOGO_PEQUENO} accessibilityLabel="TittanWare" accessibilityRole="image"
      style={{ width: tamano, height: tamano }} resizeMode="contain" />
  )
}

/** Un botón redondo translúcido, para las acciones que van sobre la cabecera. */
export function BotonCabecera({ icono: Icono, etiqueta, onPress }: { icono: LucideIcon; etiqueta: string; onPress: () => void }) {
  const { color } = useTema()
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={etiqueta} onPress={onPress} hitSlop={8}
      style={({ pressed }) => ({ width: 42, height: 42, borderRadius: 21, alignItems: 'center', justifyContent: 'center',
        backgroundColor: color.cristal, borderWidth: 1, borderColor: color.cristalBorde, opacity: pressed ? 0.6 : 1 })}>
      <Icono size={20} color={color.sobreMarca} strokeWidth={2.1} />
    </Pressable>
  )
}

/**
 * Cabecera de marca: logo (o volver), título y acciones a la derecha.
 * `children` va debajo, todavía sobre el degradado: indicadores, un saludo.
 */
export function CabeceraLite({ titulo, subtitulo, accion, volver, children }: {
  titulo: string; subtitulo?: string; accion?: ReactNode; volver?: () => void; children?: ReactNode
}) {
  const { color } = useTema()
  const borde = useSafeAreaInsets()
  return (
    <View style={{ paddingTop: borde.top + ESPACIO.e3, paddingBottom: children ? ESPACIO.e5 : ESPACIO.e4, paddingHorizontal: ESPACIO.e4,
      gap: ESPACIO.e3, borderBottomLeftRadius: RADIO.xl + 4, borderBottomRightRadius: RADIO.xl + 4 }}>
      <FondoMarca />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: ESPACIO.e3 }}>
        {volver ? <BotonCabecera icono={ArrowLeft} etiqueta="Volver" onPress={volver} /> : <LogoTW tamano={42} />}
        <View style={{ flex: 1, minWidth: 0 }}>
          {subtitulo && <Texto variante="nota" lineas={1} style={{ color: color.sobreMarcaSuave }}>{subtitulo}</Texto>}
          <Texto variante="subtitulo" lineas={1} accessibilityRole="header" style={{ color: color.sobreMarca }}>{titulo}</Texto>
        </View>
        {accion}
      </View>
      {children}
    </View>
  )
}

/** La fecha de hoy como se dice: «Domingo 15 de noviembre». */
export function fechaDeHoy(): string {
  const t = new Date().toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long' }).replace(',', '')
  return t.charAt(0).toUpperCase() + t.slice(1)
}

/** Una cifra sobre la cabecera: ficha de cristal con su rótulo. */
export function CifraCabecera({ valor, rotulo, tono }: { valor: string | number; rotulo: string; tono?: 'alerta' | 'exito' }) {
  const { color } = useTema()
  return (
    <View accessible accessibilityLabel={`${rotulo}: ${valor}`}
      style={{ flex: 1, backgroundColor: color.cristal, borderWidth: 1, borderColor: color.cristalBorde, borderRadius: RADIO.grande,
        paddingVertical: ESPACIO.e2 + 2, paddingHorizontal: ESPACIO.e3, gap: 0 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <Texto variante="subtitulo" style={{ color: color.sobreMarca, fontSize: 22, lineHeight: 26 }}>{valor}</Texto>
        {tono && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: tono === 'alerta' ? color.alertaSobreMarca : color.exitoSobreMarca }} />}
      </View>
      <Texto variante="nota" lineas={1} style={{ color: color.sobreMarcaSuave, fontSize: 12 }}>{rotulo}</Texto>
    </View>
  )
}

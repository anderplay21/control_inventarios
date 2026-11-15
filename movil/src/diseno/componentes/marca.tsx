/**
 * El fondo de la marca: el degradado oscuro de las cabeceras, con un resplandor
 * del acento en una esquina y los aros del logo, muy tenues, como textura.
 *
 * Se dibuja con SVG (ya viene en la app) para no sumar un módulo nativo solo
 * por un degradado. Va detrás del contenido: quien lo usa pone encima lo suyo.
 */
import { useId, useState } from 'react'
import { StyleSheet, View } from 'react-native'
import Svg, { Circle, Defs, LinearGradient, RadialGradient, Rect, Stop } from 'react-native-svg'
import { useTema } from '../tema'
import { RADIO } from '../tokens'

export function FondoMarca({ radio = RADIO.xl + 4, aros = true }: { radio?: number; aros?: boolean }) {
  const { color } = useTema()
  const [t, setT] = useState({ w: 0, h: 0 })
  // Los identificadores de un SVG son globales en la web: uno por cabecera.
  const id = useId().replace(/[^a-zA-Z0-9]/g, '')
  return (
    <View pointerEvents="none" onLayout={e => setT({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}
      style={[StyleSheet.absoluteFill, { backgroundColor: color.marca[0], overflow: 'hidden',
        borderBottomLeftRadius: radio, borderBottomRightRadius: radio }]}>
      {t.w > 0 && (
        <Svg width={t.w} height={t.h}>
          <Defs>
            <LinearGradient id={`f${id}`} x1="0" y1="0" x2="1" y2="1">
              <Stop offset="0" stopColor={color.marca[0]} />
              <Stop offset="1" stopColor={color.marca[1]} />
            </LinearGradient>
            <RadialGradient id={`b${id}`} cx={t.w * 0.92} cy={0} r={Math.max(t.w * 0.7, 220)} gradientUnits="userSpaceOnUse">
              <Stop offset="0" stopColor={color.marcaBrillo} stopOpacity={0.5} />
              <Stop offset="0.55" stopColor={color.marcaBrillo} stopOpacity={0.12} />
              <Stop offset="1" stopColor={color.marcaBrillo} stopOpacity={0} />
            </RadialGradient>
          </Defs>
          <Rect x={0} y={0} width={t.w} height={t.h} fill={`url(#f${id})`} />
          <Rect x={0} y={0} width={t.w} height={t.h} fill={`url(#b${id})`} />
          {aros && (
            <>
              <Circle cx={t.w - 44} cy={18} r={92} fill="none" stroke={color.sobreMarca} strokeOpacity={0.055} strokeWidth={22} />
              <Circle cx={t.w - 44} cy={18} r={150} fill="none" stroke={color.sobreMarca} strokeOpacity={0.07} strokeWidth={1.5} />
              <Circle cx={-30} cy={t.h + 10} r={110} fill="none" stroke={color.sobreMarca} strokeOpacity={0.04} strokeWidth={16} />
            </>
          )}
        </Svg>
      )}
    </View>
  )
}

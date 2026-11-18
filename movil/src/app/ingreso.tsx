/**
 * Ingreso: empresa (recordada del último ingreso), usuario y clave. Al entrar,
 * el servidor vincula este teléfono por 30 días.
 */
import { useState } from 'react'
import { KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native'
import { Redirect } from 'expo-router'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { Building2, KeyRound, LogIn, UserRound } from 'lucide-react-native'
import { Aparecer, Boton, Campo, ESPACIO, FondoMarca, Tarjeta, Texto, useTema } from '@/diseno'
import { useSesion } from '@/datos/sesion'
import { LogoTW } from '@/lite/marca'
import { ES_LITE, NOMBRE_APP, RUTA_INICIO } from '@/datos/variante'
import { VERSION_APP } from '@/datos/config'

export default function Ingreso() {
  const { color } = useTema()
  const borde = useSafeAreaInsets()
  const { usuario: actual, empresaRecordada, ingresar, aviso } = useSesion()
  const [empresa, setEmpresa] = useState(empresaRecordada ?? '')
  const [usuario, setUsuario] = useState('')
  const [clave, setClave] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)

  if (actual) return <Redirect href={RUTA_INICIO} />

  const listo = empresa.trim() && usuario.trim() && clave
  const enviar = async () => {
    if (!listo || enviando) return
    setEnviando(true)
    setError(null)
    try {
      await ingresar(empresa, usuario, clave)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'No se pudo ingresar')
      setClave('')
    } finally {
      setEnviando(false)
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: color.fondo }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView keyboardShouldPersistTaps="handled" bounces={false} contentContainerStyle={{ flexGrow: 1, paddingBottom: borde.bottom + ESPACIO.e6 }}>
        {/* Portada: el degradado de la marca con el logo; la tarjeta la pisa por abajo. */}
        <View style={{ alignItems: 'center', gap: ESPACIO.e2, paddingTop: borde.top + ESPACIO.e10, paddingBottom: ESPACIO.e16 + ESPACIO.e2, paddingHorizontal: ESPACIO.e5 }}>
          <FondoMarca radio={36} />
          <Aparecer>
            <View style={{ padding: 6, borderRadius: 999, backgroundColor: color.cristal, borderWidth: 1, borderColor: color.cristalBorde }}>
              <LogoTW tamano={124} />
            </View>
          </Aparecer>
          <Aparecer orden={1} style={{ alignItems: 'center', gap: 4, marginTop: ESPACIO.e3 }}>
            <Texto variante="titulo" style={{ letterSpacing: 5, color: color.sobreMarca }}>{NOMBRE_APP.toUpperCase()}</Texto>
            <Texto style={{ textAlign: 'center', color: color.sobreMarcaSuave }}>{ES_LITE ? 'Inspección de llantas en campo' : 'Mantenimiento en campo'}</Texto>
          </Aparecer>
        </View>

        <Aparecer orden={2} style={{ marginTop: -ESPACIO.e12, paddingHorizontal: ESPACIO.e5 }}>
        <Tarjeta elevacion={3} relleno={ESPACIO.e5} style={{ gap: ESPACIO.e4 }}>
          <View style={{ gap: 2 }}>
            <Texto variante="subtitulo">Bienvenido</Texto>
            <Texto variante="nota" tono="secundario">Ingrese con el usuario que le dio su empresa.</Texto>
          </View>
          {aviso && !error && <Texto variante="nota" tono="alerta">{aviso}</Texto>}
          <Campo etiqueta="Código de la empresa" valor={empresa} onCambio={setEmpresa} icono={Building2} sinMayusculas placeholder="por ejemplo: demo" />
          <Campo etiqueta="Usuario" valor={usuario} onCambio={setUsuario} icono={UserRound} sinMayusculas />
          <Campo etiqueta="Contraseña" valor={clave} onCambio={setClave} icono={KeyRound} secreto alEnviar={enviar} />
          {error && <Texto variante="nota" tono="peligro" accessibilityLiveRegion="polite">{error}</Texto>}
          <Boton titulo="Ingresar" icono={LogIn} tamano="grande" ancho onPress={enviar} cargando={enviando} deshabilitado={!listo} />
        </Tarjeta>
        </Aparecer>

        <View style={{ flex: 1, justifyContent: 'flex-end', gap: ESPACIO.e2, paddingHorizontal: ESPACIO.e6, paddingTop: ESPACIO.e6 }}>
          <Texto variante="nota" tono="terciario" style={{ textAlign: 'center' }}>
            Este teléfono quedará vinculado a su usuario. Su administrador puede retirarle el acceso desde el portal.
          </Texto>
          <Texto variante="mini" tono="terciario" style={{ textAlign: 'center' }}>{NOMBRE_APP} {VERSION_APP}</Texto>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

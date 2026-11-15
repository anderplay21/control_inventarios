/**
 * Lo que se ve mientras baja el código de una pantalla.
 *
 * Antes era un fondo oscuro a pantalla completa: cada vez que se abría un
 * módulo por primera vez, el portal entero se apagaba un instante y volvía.
 * Esto dibuja el mismo marco de la aplicación —barra superior, franja de
 * módulos y panel, con sus medidas reales— y el contenido como esqueleto, de
 * modo que al llegar la pantalla nada salta de sitio.
 *
 * Es solo forma: no consulta nada ni importa el menú (que pesa); por eso puede
 * vivir en el arranque sin engordarlo.
 */
import { Box, LinearProgress, Skeleton } from '@mui/material'
import { PALETA } from '@compartido/marca'
import { useNavStore } from '@/store/navStore'

const ALTO_ENCABEZADO = 61
const ANCHO_FRANJA = 60

export function MarcoCargando() {
  const { plegado, ancho } = useNavStore()
  const claro = 'rgba(255,255,255,0.08)'
  return (
    <Box aria-busy="true" aria-label="Cargando la pantalla"
      sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', bgcolor: '#F5F7F8' }}>
      <Box sx={{ height: ALTO_ENCABEZADO, flexShrink: 0, bgcolor: PALETA.tinta, display: 'flex', alignItems: 'center', px: 2.5, gap: 2 }}>
        <Box sx={{ width: 30, height: 30, borderRadius: '9px', bgcolor: claro }} />
        <Box sx={{ width: 110, height: 10, borderRadius: 99, bgcolor: claro }} />
        <Box sx={{ width: 220, height: 10, borderRadius: 99, bgcolor: 'rgba(255,255,255,0.05)', ml: 2, display: { xs: 'none', md: 'block' } }} />
        <Box sx={{ flex: 1 }} />
        <Box sx={{ width: 34, height: 34, borderRadius: '50%', bgcolor: claro }} />
      </Box>
      {/* La barra de progreso de la navegación, igual a la del encabezado real. */}
      <LinearProgress sx={{ height: 2, borderRadius: 0 }} />
      <Box sx={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <Box sx={{ display: { xs: 'none', md: 'flex' }, flexShrink: 0 }}>
          <Box sx={{ width: ANCHO_FRANJA, bgcolor: PALETA.tinta, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2.5, pt: 2.5 }}>
            {Array.from({ length: 9 }, (_, i) => <Box key={i} sx={{ width: 26, height: 8, borderRadius: 99, bgcolor: claro }} />)}
          </Box>
          {!plegado && (
            <Box sx={{ width: ancho, bgcolor: '#fff', borderRight: '1px solid rgba(15,23,42,0.07)', p: 2, display: 'flex', flexDirection: 'column', gap: 1.6 }}>
              <Skeleton variant="rounded" height={36} />
              {[70, 55, 62, 48, 66, 58, 52, 64].map((w, i) => <Skeleton key={i} variant="text" width={`${w}%`} height={22} />)}
            </Box>
          )}
        </Box>
        <Box component="main" className="anim-fade-in" sx={{ flex: 1, minWidth: 0, p: { xs: 2, sm: 3 } }}>
          <Skeleton variant="text" width={280} height={34} />
          <Skeleton variant="text" width={420} height={20} sx={{ maxWidth: '80%', mb: 2 }} />
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 2, mb: 2 }}>
            {[0, 1, 2, 3].map(i => <Skeleton key={i} variant="rounded" height={92} sx={{ borderRadius: '16px' }} />)}
          </Box>
          <Skeleton variant="rounded" height={320} sx={{ borderRadius: '16px' }} />
        </Box>
      </Box>
    </Box>
  )
}

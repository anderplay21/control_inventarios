/**
 * ERP · Ingresos · Pagos recibidos.
 *
 * Todo el dinero que recibe la empresa, como recibos de caja: pagos a facturas
 * (totales o abonos, a una o varias), anticipos (un pasivo que después se
 * aplica a facturas) y otros ingresos contra la cuenta que elija el usuario.
 * Las reglas viven en el servidor; esta pantalla muestra lo que él decide y,
 * cuando rechaza algo, su motivo.
 */
import React, { useMemo, useState } from 'react'
import {
  Box, Card, Typography, Button, Table, TableBody, TableCell, TableHead, TableRow, Chip,
  Dialog, DialogTitle, DialogContent, DialogActions, Grid, TextField, Select, MenuItem,
  FormControl, InputLabel, IconButton, Drawer, Alert, TablePagination, LinearProgress, Stack,
  Autocomplete, ToggleButton, ToggleButtonGroup, Tooltip, alpha,
} from '@mui/material'
import { Add, Close, Delete, PictureAsPdf, Print, Block, PriceCheck, FilterAltOff } from '@mui/icons-material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { apiClient } from '@/api/client'
import { Layout } from '@/components/layout/Layout'
import { SelectorCatalogo } from '@/components/catalogo/SelectorCatalogo'
import { COLOR_MODULO } from '@/config/marca'
import { mensajeDeError } from '@/utils/errorApi'

const ERP_COLOR = COLOR_MODULO
const API = '/erp/ingresos/pagos-recibidos'
const hoyISO = () => new Date().toISOString().slice(0, 10)
const r2 = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100
const plata = (v: number | string | null | undefined, moneda = 'COP') =>
  new Intl.NumberFormat('es-CO', { style: 'currency', currency: moneda || 'COP', minimumFractionDigits: 2 })
    .format(Number(v ?? 0))

// ── Tipos ─────────────────────────────────────────────────────────────────────
type TipoIngreso = 'PAGO_FACTURA' | 'ANTICIPO' | 'OTROS'
type TipoRetencion = 'RETEFUENTE' | 'RETEIVA' | 'RETEICA'

interface ReciboFila {
  id: number; numero: string; tipo_ingreso: TipoIngreso; tercero_id?: number | null
  cliente_nombre: string; cliente_nit?: string | null; detalle: string; fecha: string
  cuenta_bancaria_id?: number | null; cuenta_bancaria?: string | null
  estado: 'APLICADO' | 'POR_APLICAR' | 'ANULADO'; moneda: string
  valor_bruto: string; valor_neto: string; saldo_por_aplicar: string; historico: boolean
}
interface ReciboDetalle extends ReciboFila {
  forma_pago?: string | null; medio_pago?: string | null; centro_costo?: string | null
  referencia?: string | null; notas_adicionales?: string | null; total_retenciones: string
  comprobante_numero?: string | null; creado_por?: string | null
  anulado_motivo?: string | null; anulado_por?: string | null; anulado_en?: string | null
  facturas: { pago_id: number; factura_id: number; factura_numero: string; monto: string; estado: string }[]
  lineas_otros: { cuenta_codigo: string; cuenta_nombre: string; descripcion?: string | null; valor: string }[]
  retenciones: { tipo: string; nombre: string; concepto?: string | null; base: string; porcentaje: string; valor: string; editada: boolean }[]
  aplicaciones: { pago_id: number; factura_id: number; factura_numero: string; fecha: string; monto: string; estado: string }[]
  comentarios: { id: number; usuario?: string | null; texto: string; created_at: string }[]
}
interface Cliente { id: number; numero_identificacion: string; digito_verificacion?: string | null; razon_social: string }
interface FacturaPorCobrar {
  id: number; numero: string; fecha: string; fecha_vencimiento: string; total: string; saldo: string
  moneda: string; retencion_fuente: string; retencion_ica: string; retencion_iva: string; vencida: boolean
}
interface CuentaContable { id: number; codigo: string; nombre: string }
interface LineaOtro { cuenta: CuentaContable | null; descripcion: string; valor: string }
interface Retencion { tipo: TipoRetencion; concepto: string; base: string; porcentaje: string; motivo?: string | null }

const ESTADOS: Record<ReciboFila['estado'], { texto: string; color: string }> = {
  APLICADO: { texto: 'Aplicado', color: '#16A34A' },
  POR_APLICAR: { texto: 'Pendiente por aplicar', color: '#B45309' },
  ANULADO: { texto: 'Anulado', color: '#64748B' },
}
const TIPOS: Record<TipoIngreso, string> = {
  PAGO_FACTURA: 'Pago a factura', ANTICIPO: 'Anticipo', OTROS: 'Otros ingresos',
}
const NOMBRE_RETENCION: Record<TipoRetencion, string> = {
  RETEFUENTE: 'Retención en la fuente', RETEIVA: 'Retención de IVA', RETEICA: 'Retención de ICA',
}
const MONEDAS = ['COP', 'USD', 'EUR']
const FILTROS_VACIOS = { identificacion: '', cliente: '', desde: '', hasta: '', cuenta: '', estado: '', min: '', max: '' }
type Filtros = typeof FILTROS_VACIOS

function ChipEstado({ estado }: { estado: ReciboFila['estado'] }) {
  const e = ESTADOS[estado]
  return <Chip size="small" label={e.texto} data-prueba="estado-recibo"
    sx={{ bgcolor: alpha(e.color, 0.12), color: e.color, fontWeight: 600 }} />
}

// ── Datos de apoyo ────────────────────────────────────────────────────────────
function useApoyo() {
  const cuentas = useQuery({ queryKey: ['erp-cuentas-bancarias'], staleTime: 300_000,
    queryFn: () => apiClient.get('/erp/tesoreria/cuentas').then(r => r.data as { id: number; numero: string; descripcion?: string; activo: boolean; moneda?: string }[]) })
  const centros = useQuery({ queryKey: ['erp-centros-costo'], staleTime: 300_000,
    queryFn: () => apiClient.get('/erp/contabilidad/centros-costo').then(r => r.data as { id: number; codigo: string; nombre: string }[]) })
  const permisos = useQuery({ queryKey: ['pagos-recibidos-permisos'], staleTime: 300_000,
    queryFn: () => apiClient.get(`${API}/permisos`).then(r => r.data as { registrar: boolean; anular: boolean }) })
  return {
    cuentas: (cuentas.data ?? []).filter(c => c.activo), centros: centros.data ?? [],
    permisos: permisos.data ?? { registrar: false, anular: false },
  }
}

async function abrirPdf(id: number, numero: string, modo: 'descargar' | 'imprimir') {
  const r = await apiClient.get(`${API}/${id}/pdf`, { responseType: 'blob' })
  const url = URL.createObjectURL(new Blob([r.data], { type: 'application/pdf' }))
  if (modo === 'descargar') {
    const a = document.createElement('a')
    a.href = url; a.download = `${numero}.pdf`; a.click()
  } else {
    const w = window.open(url)
    w?.addEventListener('load', () => w.print())
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

// ── Página ────────────────────────────────────────────────────────────────────
export default function ERPPagosRecibidos() {
  const [params, setParams] = useSearchParams()
  const { cuentas, permisos } = useApoyo()
  const [pagina, setPagina] = useState(0)
  const [porPagina, setPorPagina] = useState(25)
  const [nuevo, setNuevo] = useState(false)

  // Los filtros viven en la URL: se pueden compartir y sobreviven a recargar.
  const filtros: Filtros = useMemo(() => {
    const f = { ...FILTROS_VACIOS }
    for (const k of Object.keys(f) as (keyof Filtros)[]) f[k] = params.get(k) ?? ''
    return f
  }, [params])
  const hayFiltros = Object.values(filtros).some(Boolean)
  const cambiarFiltro = (k: keyof Filtros, v: string) => {
    const p = new URLSearchParams(params)
    if (v) p.set(k, v); else p.delete(k)
    setParams(p, { replace: true }); setPagina(0)
  }
  const limpiar = () => {
    const p = new URLSearchParams()
    if (params.get('recibo')) p.set('recibo', params.get('recibo')!)
    setParams(p, { replace: true }); setPagina(0)
  }
  const detalleId = params.get('recibo') ? Number(params.get('recibo')) : null
  const abrirDetalle = (id: number | null) => {
    const p = new URLSearchParams(params)
    if (id) p.set('recibo', String(id)); else p.delete('recibo')
    setParams(p, { replace: true })
  }

  const lista = useQuery({
    queryKey: ['pagos-recibidos', filtros, pagina, porPagina],
    queryFn: () => apiClient.get(API, { params: {
      identificacion: filtros.identificacion || undefined, cliente: filtros.cliente || undefined,
      desde: filtros.desde || undefined, hasta: filtros.hasta || undefined,
      cuenta_bancaria_id: filtros.cuenta || undefined, estado: filtros.estado || undefined,
      monto_min: filtros.min || undefined, monto_max: filtros.max || undefined,
      limite: porPagina, desplazamiento: pagina * porPagina,
    } }).then(r => ({ filas: r.data as ReciboFila[], total: Number(r.headers['x-total-count'] ?? r.data.length) })),
  })

  return (
    <Layout title="ERP — Pagos recibidos">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3, flexWrap: 'wrap' }}>
        <Box sx={{ width: 36, height: 36, borderRadius: '10px', display: 'flex', alignItems: 'center',
          justifyContent: 'center', background: `linear-gradient(135deg, ${ERP_COLOR} 0%, #0D2347 100%)` }}>
          <PriceCheck sx={{ color: '#fff', fontSize: 18 }} />
        </Box>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>Pagos recibidos</Typography>
          <Typography variant="body2" sx={{ color: '#64748B', fontSize: 12 }}>
            ERP · Ingresos · Recibos de caja, anticipos y otros ingresos
          </Typography>
        </Box>
        {permisos.registrar && (
          <Button variant="contained" startIcon={<Add />} onClick={() => setNuevo(true)} data-prueba="nuevo-ingreso"
            sx={{ ml: 'auto', bgcolor: ERP_COLOR, '&:hover': { bgcolor: '#0D2347' } }}>
            Nuevo ingreso
          </Button>
        )}
      </Box>

      <Card sx={{ borderRadius: '16px', border: '1px solid #E2E8F0', boxShadow: 'none' }}>
        <Grid container spacing={1.5} sx={{ p: 2 }} alignItems="center">
          <Grid item xs={12} sm={6} md={2}>
            <TextField size="small" fullWidth label="N.º de cliente (NIT/CC)" value={filtros.identificacion}
              onChange={e => cambiarFiltro('identificacion', e.target.value)} inputProps={{ 'data-prueba': 'filtro-identificacion' }} />
          </Grid>
          <Grid item xs={12} sm={6} md={2.5}>
            <TextField size="small" fullWidth label="Cliente" value={filtros.cliente}
              onChange={e => cambiarFiltro('cliente', e.target.value)} />
          </Grid>
          <Grid item xs={6} md={1.5}>
            <TextField size="small" fullWidth type="date" label="Desde" InputLabelProps={{ shrink: true }}
              value={filtros.desde} onChange={e => cambiarFiltro('desde', e.target.value)} />
          </Grid>
          <Grid item xs={6} md={1.5}>
            <TextField size="small" fullWidth type="date" label="Hasta" InputLabelProps={{ shrink: true }}
              value={filtros.hasta} onChange={e => cambiarFiltro('hasta', e.target.value)} />
          </Grid>
          <Grid item xs={12} sm={6} md={2}>
            <FormControl size="small" fullWidth>
              <InputLabel id="filtro-cuenta">Cuenta bancaria</InputLabel>
              <Select labelId="filtro-cuenta" label="Cuenta bancaria" value={filtros.cuenta}
                onChange={e => cambiarFiltro('cuenta', String(e.target.value))}>
                <MenuItem value="">Todas</MenuItem>
                {cuentas.map(c => <MenuItem key={c.id} value={String(c.id)}>{c.numero}{c.descripcion ? ` · ${c.descripcion}` : ''}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} sm={6} md={2.5}>
            <FormControl size="small" fullWidth>
              <InputLabel id="filtro-estado">Estado</InputLabel>
              <Select labelId="filtro-estado" label="Estado" value={filtros.estado}
                onChange={e => cambiarFiltro('estado', String(e.target.value))}>
                <MenuItem value="">Todos</MenuItem>
                {(Object.keys(ESTADOS) as ReciboFila['estado'][]).map(k => <MenuItem key={k} value={k}>{ESTADOS[k].texto}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={6} md={1.5}>
            <TextField size="small" fullWidth type="number" label="Monto mínimo" value={filtros.min}
              onChange={e => cambiarFiltro('min', e.target.value)} />
          </Grid>
          <Grid item xs={6} md={1.5}>
            <TextField size="small" fullWidth type="number" label="Monto máximo" value={filtros.max}
              onChange={e => cambiarFiltro('max', e.target.value)} />
          </Grid>
          <Grid item xs={12} md="auto">
            <Button size="small" startIcon={<FilterAltOff />} disabled={!hayFiltros} onClick={limpiar}
              data-prueba="limpiar-filtros">Limpiar filtros</Button>
          </Grid>
        </Grid>
        {lista.isFetching && <LinearProgress />}
        {lista.isError && <Alert severity="error" sx={{ mx: 2 }}>{mensajeDeError(lista.error, 'No se pudo cargar el listado')}</Alert>}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Número</TableCell><TableCell>Cliente</TableCell><TableCell>Detalles</TableCell>
                <TableCell>Fecha</TableCell><TableCell>Cuenta bancaria</TableCell><TableCell>Estado</TableCell>
                <TableCell align="right">Monto</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(lista.data?.filas ?? []).map(f => (
                <TableRow key={f.id} hover sx={{ cursor: 'pointer' }} onClick={() => abrirDetalle(f.id)} data-prueba="fila-recibo">
                  <TableCell sx={{ fontWeight: 600 }}>{f.numero}</TableCell>
                  <TableCell>
                    {f.cliente_nombre}
                    {f.cliente_nit && <Typography variant="caption" sx={{ display: 'block', color: '#64748B' }}>{f.cliente_nit}</Typography>}
                  </TableCell>
                  <TableCell sx={{ maxWidth: 260 }}>{f.detalle}</TableCell>
                  <TableCell>{f.fecha}</TableCell>
                  <TableCell>{f.cuenta_bancaria ?? '—'}</TableCell>
                  <TableCell><ChipEstado estado={f.estado} /></TableCell>
                  <TableCell align="right">{plata(f.valor_bruto, f.moneda)}</TableCell>
                </TableRow>
              ))}
              {lista.data && lista.data.filas.length === 0 && (
                <TableRow><TableCell colSpan={7} align="center" sx={{ color: '#64748B', py: 4 }} data-prueba="sin-resultados">
                  {hayFiltros ? 'No hay pagos recibidos con esos criterios.' : 'Todavía no hay pagos recibidos.'}
                </TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </Box>
        <TablePagination component="div" count={lista.data?.total ?? 0} page={pagina}
          onPageChange={(_, p) => setPagina(p)} rowsPerPage={porPagina}
          onRowsPerPageChange={e => { setPorPagina(Number(e.target.value)); setPagina(0) }}
          rowsPerPageOptions={[25, 50, 100]} labelRowsPerPage="Por página" />
      </Card>

      {nuevo && <FormularioIngreso onClose={() => setNuevo(false)} onGuardado={id => { setNuevo(false); abrirDetalle(id) }} />}
      {detalleId && <DetalleRecibo id={detalleId} puedeAnular={permisos.anular} puedeComentar={permisos.registrar}
        onClose={() => abrirDetalle(null)} />}
    </Layout>
  )
}

// ── Detalle ───────────────────────────────────────────────────────────────────
function DetalleRecibo({ id, puedeAnular, puedeComentar, onClose }: {
  id: number; puedeAnular: boolean; puedeComentar: boolean; onClose: () => void
}) {
  const qc = useQueryClient()
  const navegar = useNavigate()
  const [anular, setAnular] = useState(false)
  const [motivo, setMotivo] = useState('')
  const [comentario, setComentario] = useState('')
  const det = useQuery({ queryKey: ['pago-recibido', id],
    queryFn: () => apiClient.get(`${API}/${id}`).then(r => r.data as ReciboDetalle) })
  const d = det.data
  const refrescar = () => {
    qc.invalidateQueries({ queryKey: ['pago-recibido', id] })
    qc.invalidateQueries({ queryKey: ['pagos-recibidos'] })
  }
  const accion = useMutation({
    mutationFn: ({ ruta, cuerpo }: { ruta: string; cuerpo: unknown }) => apiClient.post(`${API}/${id}/${ruta}`, cuerpo),
    onSuccess: (_, v) => {
      refrescar()
      if (v.ruta === 'anular') { toast.success('Recibo anulado'); setAnular(false); setMotivo('') }
      else setComentario('')
    },
    onError: e => toast.error(mensajeDeError(e)),
  })
  const irAFactura = (facturaId: number) => navegar(`/erp/ingresos/factura-venta?factura=${facturaId}`)
  const mon = d?.moneda ?? 'COP'

  return (
    <Drawer anchor="right" open onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', md: 640 }, bgcolor: '#fff', color: 'text.primary', backgroundImage: 'none' } }}>
      {!d ? <LinearProgress /> : (
        <Box sx={{ p: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>Recibo {d.numero}</Typography>
            <ChipEstado estado={d.estado} />
            <IconButton sx={{ ml: 'auto' }} onClick={onClose} aria-label="Cerrar"><Close /></IconButton>
          </Box>
          <Typography variant="body2" sx={{ color: '#64748B' }}>
            {TIPOS[d.tipo_ingreso]} · {d.cliente_nombre}{d.cliente_nit ? ` · ${d.cliente_nit}` : ''} · {d.fecha}
          </Typography>
          {d.historico && <Alert severity="info" sx={{ mt: 1 }}>Cobro registrado antes de los recibos de caja: conserva su número original.</Alert>}
          {d.estado === 'ANULADO' && <Alert severity="warning" sx={{ mt: 1 }}>Anulado por {d.anulado_por}: {d.anulado_motivo}</Alert>}

          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ my: 2 }}>
            <Button size="small" variant="outlined" startIcon={<Print />} onClick={() => abrirPdf(id, d.numero, 'imprimir')}>Imprimir</Button>
            <Button size="small" variant="outlined" startIcon={<PictureAsPdf />} onClick={() => abrirPdf(id, d.numero, 'descargar')}>Descargar PDF</Button>
            {puedeAnular && d.estado !== 'ANULADO' && (
              <Tooltip title={d.aplicaciones.some(a => a.estado === 'PROCESADO') ? 'El anticipo ya se aplicó: desaplíquelo primero desde cada factura.' : ''}>
                <span>
                  <Button size="small" variant="outlined" color="warning" startIcon={<Block />} onClick={() => setAnular(true)}
                    disabled={d.aplicaciones.some(a => a.estado === 'PROCESADO')} data-prueba="anular-recibo">Anular</Button>
                </span>
              </Tooltip>
            )}
          </Stack>

          <Grid container spacing={1} sx={{ mb: 2 }}>
            {([
              ['Cuenta bancaria', d.cuenta_bancaria], ['Forma de pago', d.forma_pago], ['Medio de pago', d.medio_pago],
              ['Moneda', d.moneda], ['Centro de costo', d.centro_costo], ['Referencia', d.referencia],
              ['Comprobante', d.comprobante_numero], ['Registrado por', d.creado_por],
            ] as const).filter(([, v]) => v).map(([t, v]) => (
              <Grid item xs={6} key={t}>
                <Typography variant="caption" sx={{ color: '#64748B' }}>{t}</Typography>
                <Typography variant="body2">{v}</Typography>
              </Grid>
            ))}
          </Grid>

          {d.facturas.length > 0 && <Seccion titulo="Facturas cruzadas">
            <Table size="small"><TableBody>{d.facturas.map(f => (
              <TableRow key={f.pago_id}>
                <TableCell><Box component="a" href={`/erp/ingresos/factura-venta?factura=${f.factura_id}`}
                  onClick={(e: React.MouseEvent) => { e.preventDefault(); irAFactura(f.factura_id) }}
                  sx={{ color: ERP_COLOR, fontWeight: 600 }}>{f.factura_numero}</Box></TableCell>
                <TableCell align="right">{plata(f.monto, mon)}</TableCell>
                <TableCell>{f.estado === 'ANULADO' ? 'Anulado' : ''}</TableCell>
              </TableRow>))}</TableBody></Table>
          </Seccion>}
          {d.lineas_otros.length > 0 && <Seccion titulo="Cuentas contables">
            <Table size="small"><TableBody>{d.lineas_otros.map((l, i) => (
              <TableRow key={i}><TableCell>{l.cuenta_codigo} {l.cuenta_nombre}</TableCell>
                <TableCell>{l.descripcion}</TableCell><TableCell align="right">{plata(l.valor, mon)}</TableCell></TableRow>))}
            </TableBody></Table>
          </Seccion>}
          {d.retenciones.length > 0 && <Seccion titulo="Retenciones que practicó el cliente">
            <Table size="small"><TableBody>{d.retenciones.map((r, i) => (
              <TableRow key={i}><TableCell>{r.nombre}{r.concepto ? ` · ${r.concepto}` : ''}</TableCell>
                <TableCell align="right">{Number(r.porcentaje)} %</TableCell>
                <TableCell align="right">{plata(r.valor, mon)}{r.editada && <Typography variant="caption" color="warning.main"> (editada)</Typography>}</TableCell></TableRow>))}
            </TableBody></Table>
          </Seccion>}
          {d.tipo_ingreso === 'ANTICIPO' && <Seccion titulo="Aplicaciones del anticipo">
            {d.aplicaciones.length === 0 && <Typography variant="body2" sx={{ color: '#64748B' }}>Todavía no se ha aplicado a ninguna factura.</Typography>}
            {d.aplicaciones.map(a => (
              <Typography key={a.pago_id} variant="body2" sx={{ textDecoration: a.estado === 'ANULADO' ? 'line-through' : undefined }}>
                {a.fecha} · <Box component="a" href={`/erp/ingresos/factura-venta?factura=${a.factura_id}`}
                  onClick={(e: React.MouseEvent) => { e.preventDefault(); irAFactura(a.factura_id) }}
                  sx={{ color: ERP_COLOR, fontWeight: 600 }}>{a.factura_numero}</Box> · {plata(a.monto, mon)}
                {a.estado === 'ANULADO' ? ' (desaplicada)' : ''}
              </Typography>))}
            <Typography variant="body2" sx={{ mt: 1, fontWeight: 600 }} data-prueba="saldo-por-aplicar">
              Saldo por aplicar: {plata(d.saldo_por_aplicar, mon)}
            </Typography>
          </Seccion>}

          <Box sx={{ mt: 2, ml: 'auto', maxWidth: 320 }}>
            {([['Valor recibido', d.valor_bruto], ['Retenciones', -Number(d.total_retenciones)], ['Neto al banco', d.valor_neto]] as const)
              .filter(([t, v]) => t !== 'Retenciones' || Number(v) !== 0).map(([t, v]) => (
                <Box key={t} sx={{ display: 'flex', justifyContent: 'space-between', fontWeight: t === 'Neto al banco' ? 700 : 400 }}>
                  <span>{t}</span><span>{plata(v, mon)}</span></Box>
              ))}
          </Box>

          {d.notas_adicionales && <Seccion titulo="Notas y detalles adicionales (se imprimen)">
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{d.notas_adicionales}</Typography></Seccion>}
          <Seccion titulo="Comentarios y recordatorios (internos)">
            {d.comentarios.map(c => (
              <Typography key={c.id} variant="body2">{new Date(c.created_at).toLocaleString('es-CO')} · {c.usuario} · {c.texto}</Typography>
            ))}
            {puedeComentar && (
              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                <TextField size="small" fullWidth placeholder="Comentario interno" value={comentario} onChange={e => setComentario(e.target.value)} />
                <Button disabled={!comentario.trim() || accion.isPending}
                  onClick={() => accion.mutate({ ruta: 'comentarios', cuerpo: { texto: comentario } })}>Comentar</Button>
              </Stack>
            )}
          </Seccion>
        </Box>
      )}

      {d && anular && (
        <Dialog open onClose={() => setAnular(false)} maxWidth="xs" fullWidth aria-labelledby="titulo-anular">
          <DialogTitle id="titulo-anular">Anular recibo {d.numero}</DialogTitle>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>
              Se reversa el asiento y el movimiento bancario, y cada factura recupera lo que se le había aplicado. No se puede deshacer.
            </Alert>
            <TextField fullWidth multiline minRows={2} required label="Motivo" value={motivo} onChange={e => setMotivo(e.target.value)}
              inputProps={{ 'data-prueba': 'motivo-anulacion' }} />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setAnular(false)}>Cancelar</Button>
            <Button variant="contained" color="warning" disabled={accion.isPending || !motivo.trim()}
              onClick={() => accion.mutate({ ruta: 'anular', cuerpo: { motivo: motivo.trim() } })}>Anular</Button>
          </DialogActions>
        </Dialog>
      )}
    </Drawer>
  )
}

function Seccion({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>{titulo}</Typography>
      {children}
    </Box>
  )
}

// ── Nuevo ingreso ─────────────────────────────────────────────────────────────
function FormularioIngreso({ onClose, onGuardado }: { onClose: () => void; onGuardado: (id: number) => void }) {
  const qc = useQueryClient()
  const { cuentas, centros } = useApoyo()
  const [tipo, setTipo] = useState<TipoIngreso>('PAGO_FACTURA')
  const [cliente, setCliente] = useState<Cliente | null>(null)
  const [buscarCliente, setBuscarCliente] = useState('')
  const [cuenta, setCuenta] = useState<number | ''>('')
  const [fecha, setFecha] = useState(hoyISO())
  const [forma, setForma] = useState('')
  const [medio, setMedio] = useState('')
  const [moneda, setMoneda] = useState('COP')
  const [centro, setCentro] = useState<number | ''>('')
  const [referencia, setReferencia] = useState('')
  const [notas, setNotas] = useState('')
  const [comentario, setComentario] = useState('')
  const [montos, setMontos] = useState<Record<number, string>>({})
  const [valorAnticipo, setValorAnticipo] = useState('')
  const [lineas, setLineas] = useState<LineaOtro[]>([{ cuenta: null, descripcion: '', valor: '' }])
  const [retenciones, setRetenciones] = useState<Retencion[]>([])
  const [buscarCuenta, setBuscarCuenta] = useState('')
  const [errorServidor, setErrorServidor] = useState<string | null>(null)

  const clientes = useQuery({ queryKey: ['pr-clientes', buscarCliente], staleTime: 30_000,
    queryFn: () => apiClient.get(`${API}/clientes`, { params: { buscar: buscarCliente } }).then(r => r.data as Cliente[]) })
  const facturas = useQuery({ queryKey: ['pr-facturas', cliente?.id, moneda], enabled: !!cliente && tipo === 'PAGO_FACTURA',
    queryFn: () => apiClient.get(`${API}/facturas-por-cobrar`, { params: { tercero_id: cliente!.id, moneda } })
      .then(r => r.data as FacturaPorCobrar[]) })
  const saldoFavor = useQuery({ queryKey: ['pr-saldo-favor', cliente?.id], enabled: !!cliente,
    queryFn: () => apiClient.get(`${API}/saldo-a-favor`, { params: { tercero_id: cliente!.id } }).then(r => r.data as Record<string, string>) })
  const cuentasContables = useQuery({ queryKey: ['pr-cuentas-contables', buscarCuenta], staleTime: 60_000, enabled: tipo === 'OTROS',
    queryFn: () => apiClient.get(`${API}/cuentas-contables`, { params: { buscar: buscarCuenta } }).then(r => r.data as CuentaContable[]) })
  const conceptos = useQuery({ queryKey: ['pr-conceptos-retencion'], staleTime: 300_000, enabled: tipo !== 'PAGO_FACTURA',
    queryFn: () => apiClient.get(`${API}/retenciones/conceptos`).then(r => r.data as { tipo: TipoRetencion; nombre: string; conceptos: string[] }[]) })

  // Totales en vivo (el servidor los vuelve a calcular al guardar).
  const bruto = r2(tipo === 'PAGO_FACTURA'
    ? Object.values(montos).reduce((s, v) => s + Number(v || 0), 0)
    : tipo === 'ANTICIPO' ? Number(valorAnticipo || 0)
      : lineas.reduce((s, l) => s + Number(l.valor || 0), 0))
  const valorRetencion = (r: Retencion) => r2(Number(r.base || bruto) * Number(r.porcentaje || 0) / 100)
  const totalRet = tipo === 'PAGO_FACTURA' ? 0 : r2(retenciones.reduce((s, r) => s + valorRetencion(r), 0))
  const neto = r2(bruto - totalRet)

  const sugerir = async (i: number, r: Retencion) => {
    try {
      const s = (await apiClient.get(`${API}/retenciones/sugerida`, { params: {
        tipo: r.tipo, base: r.base || bruto || 0, tercero_id: cliente?.id, concepto: r.concepto || undefined, fecha } })).data
      // Si mientras llegaba la sugerencia la persona ya escribió el %, se respeta.
      setRetenciones(p => p.map((x, j) => j !== i ? x
        : x.porcentaje === r.porcentaje ? { ...x, porcentaje: String(Number(s.porcentaje)), motivo: s.motivo }
          : { ...x, motivo: s.motivo }))
    } catch { /* sin sugerencia: se digita a mano */ }
  }
  const cambiarRetencion = (i: number, cambio: Partial<Retencion>, recalcular = false) => {
    const nueva = { ...retenciones[i], ...cambio }
    setRetenciones(p => p.map((x, j) => j === i ? nueva : x))
    if (recalcular) sugerir(i, nueva)
  }

  const guardar = useMutation({
    mutationFn: () => { setErrorServidor(null); return apiClient.post(API, {
      tipo_ingreso: tipo, tercero_id: cliente?.id ?? null, cuenta_bancaria_id: cuenta || null, fecha,
      forma_pago: forma || null, medio_pago: medio || null, moneda, centro_costo_id: centro || null,
      referencia: referencia || null, notas_adicionales: notas || null, comentario: comentario || null,
      facturas: tipo === 'PAGO_FACTURA'
        ? Object.entries(montos).filter(([, v]) => Number(v) !== 0 && v !== '').map(([id, v]) => ({ factura_id: Number(id), monto: v }))
        : [],
      valor: tipo === 'ANTICIPO' ? valorAnticipo || null : null,
      lineas: tipo === 'OTROS'
        ? lineas.filter(l => l.cuenta || l.valor).map(l => ({ cuenta_id: l.cuenta?.id, descripcion: l.descripcion || null, valor: l.valor || 0 }))
        : [],
      retenciones: tipo === 'PAGO_FACTURA' ? [] : retenciones.map(r => ({
        tipo: r.tipo, concepto: r.concepto || null, base: r.base || null,
        porcentaje: r.porcentaje === '' ? null : r.porcentaje, valor: valorRetencion(r) })),
    }) },
    onSuccess: r => {
      toast.success(`Recibo ${r.data.numero} guardado`)
      qc.invalidateQueries({ queryKey: ['pagos-recibidos'] })
      qc.invalidateQueries({ queryKey: ['facturas-venta'] })
      onGuardado(r.data.id)
    },
    // El error queda a la vista dentro del formulario, junto a Guardar, y no solo en un aviso que se va.
    onError: e => setErrorServidor(mensajeDeError(e, 'No se pudo guardar el pago recibido')),
  })

  const facturasFaltan = tipo === 'PAGO_FACTURA' && !Object.values(montos).some(v => Number(v) > 0)
  const otrosFaltan = tipo === 'OTROS' && !lineas.some(l => l.cuenta && Number(l.valor) > 0)
  // Un monto que supera el saldo o no es positivo ya se marca en su campo; además impide guardar.
  const montosInvalidos = tipo === 'PAGO_FACTURA' && (facturas.data ?? []).some(f => {
    const v = montos[f.id]
    return v !== undefined && v !== '' && (Number(v) > Number(f.saldo) || Number(v) <= 0)
  })
  const listo = !!cliente && !!cuenta && !!fecha && bruto > 0 && neto > 0 && !facturasFaltan && !otrosFaltan && !montosInvalidos

  return (
    <Dialog open onClose={onClose} fullScreen aria-labelledby="titulo-ingreso">
      <DialogTitle id="titulo-ingreso" sx={{ display: 'flex', alignItems: 'center' }}>
        Nuevo pago recibido
        <IconButton sx={{ ml: 'auto' }} onClick={onClose} aria-label="Cerrar"><Close /></IconButton>
      </DialogTitle>
      <DialogContent dividers>
        <Box sx={{ maxWidth: 1100, mx: 'auto' }}>
          <ToggleButtonGroup exclusive size="small" value={tipo} sx={{ mb: 2, flexWrap: 'wrap' }}
            onChange={(_, v) => v && setTipo(v)}>
            {(Object.keys(TIPOS) as TipoIngreso[]).map(t => (
              <ToggleButton key={t} value={t} data-prueba={`tipo-${t}`}>{TIPOS[t]}</ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Typography variant="body2" sx={{ color: '#64748B', mb: 2 }}>
            {tipo === 'PAGO_FACTURA' && 'Reparta el dinero entre las facturas por cobrar del cliente: el saldo completo o un abono.'}
            {tipo === 'ANTICIPO' && 'Dinero recibido antes de facturar. Queda como pasivo (anticipos de clientes) y se aplica después desde la factura.'}
            {tipo === 'OTROS' && 'Dinero que no viene de una factura de venta. Elija la cuenta contable de cada valor.'}
          </Typography>

          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              <Autocomplete options={clientes.data ?? []} value={cliente}
                onChange={(_, v) => { setCliente(v); setMontos({}) }}
                onInputChange={(_, v, motivo) => motivo === 'input' && setBuscarCliente(v)}
                getOptionLabel={c => `${c.razon_social} · ${c.numero_identificacion}${c.digito_verificacion ? `-${c.digito_verificacion}` : ''}`}
                isOptionEqualToValue={(a, b) => a.id === b.id} filterOptions={x => x}
                noOptionsText="Sin coincidencias. El cliente debe estar registrado en Terceros."
                renderInput={p => <TextField {...p} required label="Cliente" inputProps={{ ...p.inputProps, 'data-prueba': 'cliente' }} />} />
              {cliente && saldoFavor.data && Object.keys(saldoFavor.data).length > 0 && (
                <Typography variant="caption" sx={{ color: '#B45309' }}>
                  Saldo a favor por anticipos: {Object.entries(saldoFavor.data).map(([m, v]) => plata(v, m)).join(' · ')}
                </Typography>
              )}
            </Grid>
            <Grid item xs={12} md={3}>
              <FormControl fullWidth required>
                <InputLabel id="campo-cuenta">Cuenta bancaria</InputLabel>
                <Select labelId="campo-cuenta" label="Cuenta bancaria" value={cuenta} onChange={e => setCuenta(e.target.value as number)}
                  inputProps={{ 'data-prueba': 'cuenta' }}>
                  {cuentas.map(c => <MenuItem key={c.id} value={c.id}>{c.numero}{c.descripcion ? ` · ${c.descripcion}` : ''}</MenuItem>)}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12} md={3}>
              <TextField fullWidth required type="date" label="Fecha de pago" InputLabelProps={{ shrink: true }}
                value={fecha} onChange={e => setFecha(e.target.value)} />
            </Grid>
            <Grid item xs={12} md={3}>
              <SelectorCatalogo modulo="ERP" tipo="FORMA_PAGO" label="Forma de pago" valor={forma} onChange={v => setForma(v)} />
            </Grid>
            <Grid item xs={12} md={3}>
              <SelectorCatalogo modulo="ERP" tipo="MEDIO_PAGO" label="Medio de pago" valor={medio} onChange={v => setMedio(v)} />
            </Grid>
            <Grid item xs={6} md={2}>
              <FormControl fullWidth>
                <InputLabel id="campo-moneda">Moneda</InputLabel>
                <Select labelId="campo-moneda" label="Moneda" value={moneda} onChange={e => { setMoneda(String(e.target.value)); setMontos({}) }}>
                  {MONEDAS.map(m => <MenuItem key={m} value={m}>{m}</MenuItem>)}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={6} md={2}>
              <FormControl fullWidth>
                <InputLabel id="campo-centro">Centro de costo</InputLabel>
                <Select labelId="campo-centro" label="Centro de costo" value={centro} onChange={e => setCentro(e.target.value as number)}>
                  <MenuItem value="">Ninguno</MenuItem>
                  {centros.map(c => <MenuItem key={c.id} value={c.id}>{c.codigo} · {c.nombre}</MenuItem>)}
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12} md={2}>
              <TextField fullWidth label="Referencia" value={referencia} onChange={e => setReferencia(e.target.value)} />
            </Grid>
          </Grid>

          {/* Pago a factura: facturas por cobrar del cliente */}
          {tipo === 'PAGO_FACTURA' && (
            <Seccion titulo="Facturas por cobrar">
              {!cliente ? <Typography variant="body2" sx={{ color: '#64748B' }}>Elija el cliente para cargar sus facturas pendientes.</Typography>
                : facturas.isLoading ? <LinearProgress />
                  : (facturas.data ?? []).length === 0 ? <Typography variant="body2" sx={{ color: '#64748B' }}>El cliente no tiene facturas por cobrar en {moneda}.</Typography>
                    : (
                      <Box sx={{ overflowX: 'auto' }}>
                        <Table size="small">
                          <TableHead><TableRow>
                            <TableCell>Factura</TableCell><TableCell>Fecha</TableCell><TableCell>Vence</TableCell>
                            <TableCell align="right">Total</TableCell><TableCell align="right">Retenciones</TableCell>
                            <TableCell align="right">Saldo</TableCell><TableCell align="right" sx={{ minWidth: 170 }}>Monto a recibir</TableCell>
                          </TableRow></TableHead>
                          <TableBody>{(facturas.data ?? []).map(f => {
                            const ret = Number(f.retencion_fuente) + Number(f.retencion_ica) + Number(f.retencion_iva)
                            const monto = montos[f.id] ?? ''
                            const excede = Number(monto) > Number(f.saldo)
                            const negativo = monto !== '' && Number(monto) <= 0
                            return (
                              <TableRow key={f.id} data-prueba="fila-por-cobrar">
                                <TableCell sx={{ fontWeight: 600 }}>{f.numero}</TableCell>
                                <TableCell>{f.fecha}</TableCell>
                                <TableCell sx={{ color: f.vencida ? '#DC2626' : undefined }}>{f.fecha_vencimiento}</TableCell>
                                <TableCell align="right">{plata(f.total, f.moneda)}</TableCell>
                                <TableCell align="right">{ret ? plata(ret, f.moneda) : '—'}</TableCell>
                                <TableCell align="right">{plata(f.saldo, f.moneda)}</TableCell>
                                <TableCell align="right">
                                  <Stack direction="row" spacing={0.5} alignItems="center" justifyContent="flex-end">
                                    <TextField size="small" type="number" value={monto} error={excede || negativo}
                                      helperText={excede ? 'Supera el saldo' : negativo ? 'Debe ser mayor que cero' : ''}
                                      onChange={e => setMontos(p => ({ ...p, [f.id]: e.target.value }))}
                                      inputProps={{ 'data-prueba': `monto-${f.numero}`, min: 0 }} sx={{ width: 140 }} />
                                    <Button size="small" onClick={() => setMontos(p => ({ ...p, [f.id]: String(Number(f.saldo)) }))}>Total</Button>
                                  </Stack>
                                </TableCell>
                              </TableRow>
                            )
                          })}</TableBody>
                        </Table>
                        <Typography variant="caption" sx={{ color: '#64748B' }}>
                          Las retenciones de cada factura se registran en la factura; aquí solo se muestran.
                        </Typography>
                      </Box>
                    )}
            </Seccion>
          )}

          {tipo === 'ANTICIPO' && (
            <Seccion titulo="Valor del anticipo">
              <TextField type="number" required label="Valor recibido (antes de retenciones)" value={valorAnticipo}
                onChange={e => setValorAnticipo(e.target.value)} inputProps={{ 'data-prueba': 'valor-anticipo' }} sx={{ maxWidth: 320 }} fullWidth />
            </Seccion>
          )}

          {tipo === 'OTROS' && (
            <Seccion titulo="Cuentas contables">
              {lineas.map((l, i) => (
                <Grid container spacing={1} key={i} sx={{ mb: 1 }} alignItems="center">
                  <Grid item xs={12} md={5}>
                    <Autocomplete options={cuentasContables.data ?? []} value={l.cuenta}
                      onChange={(_, v) => setLineas(p => p.map((x, j) => j === i ? { ...x, cuenta: v } : x))}
                      onInputChange={(_, v, motivo) => motivo === 'input' && setBuscarCuenta(v)}
                      getOptionLabel={c => `${c.codigo} · ${c.nombre}`} isOptionEqualToValue={(a, b) => a.id === b.id}
                      filterOptions={x => x} noOptionsText="Escriba el código o el nombre de la cuenta"
                      renderInput={p => <TextField {...p} size="small" required label="Cuenta contable" />} />
                  </Grid>
                  <Grid item xs={12} md={4}>
                    <TextField size="small" fullWidth label="Descripción" value={l.descripcion}
                      onChange={e => setLineas(p => p.map((x, j) => j === i ? { ...x, descripcion: e.target.value } : x))} />
                  </Grid>
                  <Grid item xs={9} md={2}>
                    <TextField size="small" fullWidth type="number" label="Valor" value={l.valor}
                      onChange={e => setLineas(p => p.map((x, j) => j === i ? { ...x, valor: e.target.value } : x))} />
                  </Grid>
                  <Grid item xs={3} md={1}>
                    <IconButton aria-label="Quitar línea" disabled={lineas.length === 1}
                      onClick={() => setLineas(p => p.filter((_, j) => j !== i))}><Delete /></IconButton>
                  </Grid>
                </Grid>
              ))}
              <Button size="small" startIcon={<Add />} onClick={() => setLineas(p => [...p, { cuenta: null, descripcion: '', valor: '' }])}>
                Agregar cuenta
              </Button>
            </Seccion>
          )}

          {tipo !== 'PAGO_FACTURA' && (
            <Seccion titulo="Retenciones que practicó el cliente">
              {retenciones.map((r, i) => {
                const opciones = conceptos.data?.find(c => c.tipo === r.tipo)?.conceptos ?? []
                return (
                  <Grid container spacing={1} key={i} sx={{ mb: 1 }} alignItems="center">
                    <Grid item xs={12} md={3}>
                      <FormControl size="small" fullWidth>
                        <InputLabel id={`ret-tipo-${i}`}>Tipo de retención</InputLabel>
                        <Select labelId={`ret-tipo-${i}`} label="Tipo de retención" value={r.tipo}
                          onChange={e => cambiarRetencion(i, { tipo: e.target.value as TipoRetencion, concepto: '' }, true)}>
                          {(Object.keys(NOMBRE_RETENCION) as TipoRetencion[]).map(t => <MenuItem key={t} value={t}>{NOMBRE_RETENCION[t]}</MenuItem>)}
                        </Select>
                      </FormControl>
                    </Grid>
                    <Grid item xs={12} md={3}>
                      <FormControl size="small" fullWidth>
                        <InputLabel id={`ret-concepto-${i}`}>Concepto</InputLabel>
                        <Select labelId={`ret-concepto-${i}`} label="Concepto" value={r.concepto}
                          onChange={e => cambiarRetencion(i, { concepto: String(e.target.value) }, true)}>
                          {opciones.map(c => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                        </Select>
                      </FormControl>
                    </Grid>
                    <Grid item xs={4} md={2}>
                      <TextField size="small" fullWidth type="number" label="Base" value={r.base} placeholder={String(bruto)}
                        onChange={e => cambiarRetencion(i, { base: e.target.value })} />
                    </Grid>
                    <Grid item xs={4} md={1.5}>
                      <TextField size="small" fullWidth type="number" label="%" value={r.porcentaje}
                        onChange={e => cambiarRetencion(i, { porcentaje: e.target.value })} />
                    </Grid>
                    <Grid item xs={3} md={1.5}>
                      <Typography variant="body2" sx={{ textAlign: 'right' }}>{plata(valorRetencion(r), moneda)}</Typography>
                    </Grid>
                    <Grid item xs={1} md={1}>
                      <IconButton aria-label="Quitar retención" onClick={() => setRetenciones(p => p.filter((_, j) => j !== i))}><Delete /></IconButton>
                    </Grid>
                    {r.motivo && <Grid item xs={12}><Typography variant="caption" sx={{ color: '#64748B' }}>{r.motivo}</Typography></Grid>}
                  </Grid>
                )
              })}
              <Button size="small" startIcon={<Add />} data-prueba="agregar-retencion" onClick={() => {
                const nueva: Retencion = { tipo: 'RETEFUENTE', concepto: '', base: '', porcentaje: '' }
                setRetenciones(p => [...p, nueva]); sugerir(retenciones.length, nueva)
              }}>Agregar retención</Button>
            </Seccion>
          )}

          <Box sx={{ mt: 3, ml: 'auto', maxWidth: 340 }}>
            {([['Valor recibido', bruto], ...(totalRet ? [['Retenciones', -totalRet]] : []), ['Entra al banco', neto]] as [string, number][]).map(([t, v]) => (
              <Box key={t} sx={{ display: 'flex', justifyContent: 'space-between', fontWeight: t === 'Entra al banco' ? 700 : 400 }}>
                <span>{t}</span><span data-prueba={`total-${t}`}>{plata(v, moneda)}</span></Box>
            ))}
          </Box>

          <Grid container spacing={2} sx={{ mt: 1 }}>
            <Grid item xs={12} md={6}>
              <TextField fullWidth multiline minRows={3} label="Notas y detalles adicionales" value={notas}
                onChange={e => setNotas(e.target.value)} helperText="Se imprimen en el PDF del recibo." />
            </Grid>
            <Grid item xs={12} md={6}>
              <TextField fullWidth multiline minRows={3} label="Comentarios y recordatorios" value={comentario}
                onChange={e => setComentario(e.target.value)} helperText="Internos: no salen en el documento impreso." />
            </Grid>
          </Grid>
        </Box>
      </DialogContent>
      <DialogActions>
        {errorServidor && (
          <Alert severity="error" onClose={() => setErrorServidor(null)} sx={{ mr: 'auto', flex: 1 }} data-prueba="error-servidor">
            {errorServidor}
          </Alert>
        )}
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={!listo || guardar.isPending} onClick={() => guardar.mutate()}
          data-prueba="guardar-ingreso" sx={{ bgcolor: ERP_COLOR }}>Guardar</Button>
      </DialogActions>
    </Dialog>
  )
}

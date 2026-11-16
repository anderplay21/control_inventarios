/**
 * ERP · Ingresos · Factura de venta.
 *
 * La única puerta para crear facturas a mano. Las reglas (numeración, qué se
 * puede hacer con cada factura, validaciones) viven en el servidor: esta
 * pantalla muestra lo que él decide y, cuando rechaza algo, muestra su motivo.
 */
import React, { useEffect, useRef, useState } from 'react'
import {
  Box, Card, Typography, Button, Table, TableBody, TableCell, TableHead, TableRow,
  Chip, Dialog, DialogTitle, DialogContent, DialogActions, Grid, TextField, Select,
  MenuItem, FormControl, InputLabel, IconButton, Tooltip, Drawer, Divider, Alert,
  TablePagination, LinearProgress, Checkbox, FormControlLabel, Stack, alpha,
} from '@mui/material'
import {
  Add, Delete, Close, Payments, Print, PictureAsPdf, Share, AutoAwesome, Receipt,
  Edit, ContentCopy, AttachFile, Block, Undo, Gavel, NoteAdd, Percent, Send, Draw,
} from '@mui/icons-material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { apiClient } from '@/api/client'
import { Layout } from '@/components/layout/Layout'
import { SelectorCatalogo } from '@/components/catalogo/SelectorCatalogo'
import { COLOR_MODULO } from '@/config/marca'
import { mensajeDeError } from '@/utils/errorApi'

const ERP_COLOR = COLOR_MODULO
const API = '/erp/ingresos/facturas'
const hoyISO = () => new Date().toISOString().slice(0, 10)
const r2 = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100
const plata = (v: number | string | null | undefined, moneda = 'COP') =>
  new Intl.NumberFormat('es-CO', { style: 'currency', currency: moneda || 'COP', minimumFractionDigits: 2 })
    .format(Number(v ?? 0))

// ── Tipos ─────────────────────────────────────────────────────────────────────
interface Regla { permitida: boolean; motivo?: string | null }
interface FacturaFila {
  id: number; numero: string; tipo_documento: string; estado: string
  estado_visible: string; vencida: boolean; cliente_nombre: string; cliente_nit?: string | null
  fecha: string; fecha_vencimiento: string; total: string; saldo: string; moneda?: string | null
}
interface LineaDet {
  id: number; referencia?: string | null; descripcion: string; cantidad: string
  precio_unitario: string; descuento_pct?: string | null; impuesto_id?: number | null
  subtotal: string; total_impuesto: string; total: string
}
interface FacturaDetalle extends FacturaFila {
  tercero_id?: number | null; vendedor_id?: number | null; cliente_email?: string | null
  centro_costo_id?: number | null; forma_pago?: string | null; medio_pago?: string | null
  subtotal: string; descuento: string; total_impuestos: string
  retencion_fuente: string; retencion_ica: string; retencion_iva: string
  cufe?: string | null; remision?: string | null; concepto?: string | null
  observaciones?: string | null; notas_adicionales?: string | null
  lineas: LineaDet[]
  pagos: { id: number; numero: string; fecha: string; monto: string; metodo_pago?: string | null }[]
  notas: { id: number; tipo: string; numero: string; fecha: string; motivo: string; total: string }[]
  adjuntos: { id: number; nombre: string; created_at: string; subido_por?: string | null }[]
  historial: { id: number; tipo: string; detalle?: string | null; usuario?: string | null; created_at: string }[]
  acciones: Record<string, Regla>
  cerrada_motivo?: string | null; retenciones_editadas: boolean; tiene_firma: boolean
}
interface Linea {
  referencia: string; descripcion: string; cantidad: string; precio_unitario: string
  descuento_pct: string; impuesto_id: number | ''
}
interface Formulario {
  tipo_documento: 'FACTURA_VENTA' | 'TICKET'; fecha: string; fecha_vencimiento: string
  moneda: string; centro_costo_id: number | ''; vendedor_id: number | ''
  forma_pago: string; medio_pago: string; tercero_id: number | null
  cliente_tipo_identificacion: string; cliente_identificacion: string
  cliente_nombre: string; cliente_email: string; remision: string
  notas: string; notas_adicionales: string; comentario: string; lineas: Linea[]
}
interface Impuesto { id: number; nombre: string; porcentaje: number; tipo: string; activo: boolean }

const LINEA_VACIA: Linea = { referencia: '', descripcion: '', cantidad: '1', precio_unitario: '', descuento_pct: '0', impuesto_id: '' }
const FORM_VACIO: Formulario = {
  tipo_documento: 'FACTURA_VENTA', fecha: hoyISO(), fecha_vencimiento: '', moneda: 'COP',
  centro_costo_id: '', vendedor_id: '', forma_pago: '', medio_pago: '', tercero_id: null,
  cliente_tipo_identificacion: 'NIT', cliente_identificacion: '', cliente_nombre: '', cliente_email: '',
  remision: '', notas: '', notas_adicionales: '', comentario: '', lineas: [{ ...LINEA_VACIA }],
}
const TIPOS_ID = ['NIT', 'CC', 'CE', 'TI', 'PASAPORTE']
const MONEDAS = ['COP', 'USD', 'EUR']
const CORREO = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

const COLOR_ESTADO: Record<string, string> = {
  'Por cobrar': '#1D4ED8', 'Cobrada': '#16A34A', 'Anulada': '#64748B',
  'Borrador': '#94A3B8', 'Castigada': '#B45309',
}
const FILTROS: { clave: string; texto: string }[] = [
  { clave: '', texto: 'Todas' }, { clave: 'por_cobrar', texto: 'Por cobrar' },
  { clave: 'vencida', texto: 'Vencidas' }, { clave: 'cobrada', texto: 'Cobradas' },
  { clave: 'borrador', texto: 'Borradores' }, { clave: 'anulada', texto: 'Anuladas' },
  { clave: 'castigada', texto: 'Castigadas' },
]

// ── Cálculo en vivo (el mismo redondeo que el servidor) ──────────────────────
function calcularLinea(l: Linea, tarifa: number) {
  const bruto = r2(Number(l.cantidad || 0) * Number(l.precio_unitario || 0))
  const descuento = r2(bruto * Number(l.descuento_pct || 0) / 100)
  const subtotal = r2(bruto - descuento)
  const impuesto = r2(subtotal * tarifa / 100)
  return { bruto, descuento, subtotal, impuesto, total: r2(subtotal + impuesto) }
}

// ── Datos de apoyo ────────────────────────────────────────────────────────────
function useApoyo() {
  const centros = useQuery({ queryKey: ['erp-centros-costo'], staleTime: 300_000,
    queryFn: () => apiClient.get('/erp/contabilidad/centros-costo').then(r => r.data as { id: number; codigo: string; nombre: string }[]) })
  const vendedores = useQuery({ queryKey: ['erp-vendedores'], staleTime: 300_000,
    queryFn: () => apiClient.get('/erp/terceros', { params: { tipo: 'vendedor' } })
      .then(r => r.data as { id: number; razon_social: string }[]).catch(() => []) })
  const impuestos = useQuery({ queryKey: ['erp-impuestos'], staleTime: 300_000,
    queryFn: () => apiClient.get('/erp/tributacion/impuestos').then(r => (r.data as Impuesto[]).filter(i => i.activo && i.tipo === 'IVA')) })
  const cuentas = useQuery({ queryKey: ['erp-cuentas-bancarias'], staleTime: 300_000,
    queryFn: () => apiClient.get('/erp/tesoreria/cuentas').then(r => r.data as { id: number; numero: string; descripcion?: string; activo: boolean }[]) })
  return {
    centros: centros.data ?? [], vendedores: vendedores.data ?? [],
    impuestos: impuestos.data ?? [], cuentas: (cuentas.data ?? []).filter(c => c.activo),
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
export default function ERPFacturaVenta() {
  const [params, setParams] = useSearchParams()
  const [filtro, setFiltro] = useState('')
  const [buscar, setBuscar] = useState('')
  const [pagina, setPagina] = useState(0)
  const [porPagina, setPorPagina] = useState(25)
  const [nueva, setNueva] = useState(false)
  const [pagar, setPagar] = useState<FacturaFila | null>(null)
  const detalleId = params.get('factura') ? Number(params.get('factura')) : null
  const abrirDetalle = (id: number | null) => {
    const p = new URLSearchParams(params)
    if (id) p.set('factura', String(id)); else p.delete('factura')
    setParams(p, { replace: true })
  }

  const lista = useQuery({
    queryKey: ['facturas-venta', filtro, buscar, pagina, porPagina],
    queryFn: () => apiClient.get(API, { params: {
      estado: filtro || undefined, buscar: buscar || undefined,
      limite: porPagina, desplazamiento: pagina * porPagina,
    } }).then(r => ({ filas: r.data as FacturaFila[], total: Number(r.headers['x-total-count'] ?? r.data.length) })),
  })

  return (
    <Layout title="ERP — Factura de venta">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3 }}>
        <Box sx={{ width: 36, height: 36, borderRadius: '10px', display: 'flex', alignItems: 'center',
          justifyContent: 'center', background: `linear-gradient(135deg, ${ERP_COLOR} 0%, #0D2347 100%)` }}>
          <Receipt sx={{ color: '#fff', fontSize: 18 }} />
        </Box>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>Factura de venta</Typography>
          <Typography variant="body2" sx={{ color: '#64748B', fontSize: 12 }}>
            ERP · Ingresos · Facturas y tickets de venta
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<Add />} onClick={() => setNueva(true)}
          sx={{ ml: 'auto', bgcolor: ERP_COLOR, '&:hover': { bgcolor: '#0D2347' } }}>
          Nueva factura de venta
        </Button>
      </Box>

      <Card sx={{ borderRadius: '16px', border: '1px solid #E2E8F0', boxShadow: 'none' }}>
        <Box sx={{ display: 'flex', gap: 1, p: 2, flexWrap: 'wrap', alignItems: 'center' }}>
          {FILTROS.map(f => (
            <Chip key={f.clave} label={f.texto} clickable
              onClick={() => { setFiltro(f.clave); setPagina(0) }}
              color={filtro === f.clave ? 'primary' : 'default'}
              variant={filtro === f.clave ? 'filled' : 'outlined'} size="small" />
          ))}
          <TextField size="small" placeholder="Buscar número, cliente o NIT" value={buscar}
            onChange={e => { setBuscar(e.target.value); setPagina(0) }} sx={{ ml: 'auto', minWidth: 260 }} />
        </Box>
        {lista.isFetching && <LinearProgress />}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Número</TableCell><TableCell>Cliente</TableCell>
                <TableCell>Creación</TableCell><TableCell>Vencimiento</TableCell>
                <TableCell align="right">Total</TableCell><TableCell>Estado</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {(lista.data?.filas ?? []).map(f => (
                <TableRow key={f.id} hover sx={{ cursor: 'pointer' }} onClick={() => abrirDetalle(f.id)}
                  data-prueba="fila-factura">
                  <TableCell sx={{ fontWeight: 600 }}>
                    {f.estado === 'BORRADOR' ? 'Borrador' : f.numero}
                    {f.tipo_documento === 'TICKET' && <Chip label="Ticket" size="small" sx={{ ml: 1, height: 18 }} />}
                  </TableCell>
                  <TableCell>{f.cliente_nombre}</TableCell>
                  <TableCell>{f.fecha}</TableCell>
                  <TableCell sx={{ color: f.vencida ? '#DC2626' : undefined }}>{f.fecha_vencimiento}</TableCell>
                  <TableCell align="right">{plata(f.total, f.moneda ?? 'COP')}</TableCell>
                  <TableCell>
                    <Chip size="small" label={f.vencida ? 'Vencida' : f.estado_visible}
                      sx={{ bgcolor: alpha(f.vencida ? '#DC2626' : COLOR_ESTADO[f.estado_visible] ?? '#64748B', 0.12),
                        color: f.vencida ? '#DC2626' : COLOR_ESTADO[f.estado_visible] ?? '#64748B', fontWeight: 600 }} />
                  </TableCell>
                  <TableCell align="right" onClick={e => e.stopPropagation()}>
                    {f.estado_visible === 'Por cobrar' && (
                      <Button size="small" startIcon={<Payments />} onClick={() => setPagar(f)}>Agregar pago</Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {lista.data && lista.data.filas.length === 0 && (
                <TableRow><TableCell colSpan={7} align="center" sx={{ color: '#64748B', py: 4 }}>
                  No hay facturas con este filtro.
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

      {nueva && <FormularioFactura onClose={() => setNueva(false)} onGuardada={id => { setNueva(false); abrirDetalle(id) }} />}
      {pagar && <PagoDialog factura={pagar} onClose={() => setPagar(null)} />}
      {detalleId && <DetalleFactura id={detalleId} onClose={() => abrirDetalle(null)} onAbrir={abrirDetalle} />}
    </Layout>
  )
}

// ── Pago rápido ───────────────────────────────────────────────────────────────
function PagoDialog({ factura, onClose }: { factura: { id: number; numero: string; saldo: string; moneda?: string | null }; onClose: () => void }) {
  const qc = useQueryClient()
  const { cuentas } = useApoyo()
  const [valor, setValor] = useState(String(factura.saldo))
  const [cuenta, setCuenta] = useState<number | ''>('')
  const [fecha, setFecha] = useState(hoyISO())
  const [metodo, setMetodo] = useState('')
  const [referencia, setReferencia] = useState('')
  const guardar = useMutation({
    mutationFn: () => apiClient.post(`${API}/${factura.id}/pagos`, {
      monto: valor, cuenta_bancaria_id: cuenta || null, fecha, metodo_pago: metodo || null,
      referencia: referencia || null }),
    onSuccess: () => {
      toast.success('Pago registrado')
      qc.invalidateQueries({ queryKey: ['facturas-venta'] })
      qc.invalidateQueries({ queryKey: ['factura-venta', factura.id] })
      onClose()
    },
    onError: e => toast.error(mensajeDeError(e, 'No se pudo registrar el pago')),
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="titulo-pago">
      <DialogTitle id="titulo-pago">Agregar pago · {factura.numero}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Valor" type="number" value={valor} onChange={e => setValor(e.target.value)}
            helperText={`Saldo pendiente: ${plata(factura.saldo, factura.moneda ?? 'COP')}`} inputProps={{ 'data-prueba': 'pago-valor' }} />
          <FormControl required>
            <InputLabel id="campo-1">Cuenta bancaria</InputLabel>
            <Select labelId="campo-1" label="Cuenta bancaria" value={cuenta} onChange={e => setCuenta(e.target.value as number)}>
              {cuentas.map(c => <MenuItem key={c.id} value={c.id}>{c.numero}{c.descripcion ? ` · ${c.descripcion}` : ''}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField label="Fecha" type="date" value={fecha} onChange={e => setFecha(e.target.value)}
            InputLabelProps={{ shrink: true }} inputProps={{ 'data-prueba': 'pago-fecha' }} />
          <SelectorCatalogo modulo="ERP" tipo="MEDIO_PAGO" label="Método de pago" valor={metodo} onChange={v => setMetodo(v)} />
          <TextField label="Referencia" value={referencia} onChange={e => setReferencia(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={guardar.isPending} onClick={() => guardar.mutate()}>Agregar pago</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Firma capturada en pantalla ───────────────────────────────────────────────
function LienzoFirma({ onListo }: { onListo: (b: Blob | null) => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const dibujando = useRef(false)
  const punto = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top]
  }
  const exportar = () => ref.current?.toBlob(b => onListo(b), 'image/png')
  return (
    <Box>
      <canvas ref={ref} width={360} height={120} style={{ border: '1px dashed #94A3B8', borderRadius: 8, touchAction: 'none', background: '#fff' }}
        onPointerDown={e => { dibujando.current = true; const c = ref.current!.getContext('2d')!; const [x, y] = punto(e); c.beginPath(); c.moveTo(x, y) }}
        onPointerMove={e => { if (!dibujando.current) return; const c = ref.current!.getContext('2d')!; const [x, y] = punto(e); c.lineWidth = 2; c.lineTo(x, y); c.stroke() }}
        onPointerUp={() => { dibujando.current = false; exportar() }} />
      <Button size="small" onClick={() => { ref.current!.getContext('2d')!.clearRect(0, 0, 360, 120); onListo(null) }}>Limpiar</Button>
    </Box>
  )
}

// ── Formulario de nueva factura / edición ─────────────────────────────────────
function desdeDetalle(d: FacturaDetalle): Formulario {
  return {
    tipo_documento: d.tipo_documento as Formulario['tipo_documento'], fecha: d.fecha,
    fecha_vencimiento: d.fecha_vencimiento, moneda: d.moneda ?? 'COP',
    centro_costo_id: d.centro_costo_id ?? '', vendedor_id: d.vendedor_id ?? '',
    forma_pago: d.forma_pago ?? '', medio_pago: d.medio_pago ?? '', tercero_id: d.tercero_id ?? null,
    cliente_tipo_identificacion: 'NIT', cliente_identificacion: d.cliente_nit ?? '',
    cliente_nombre: d.cliente_nombre, cliente_email: d.cliente_email ?? '', remision: d.remision ?? '',
    notas: d.observaciones ?? '', notas_adicionales: d.notas_adicionales ?? '', comentario: '',
    lineas: d.lineas.map(l => ({ referencia: l.referencia ?? '', descripcion: l.descripcion,
      cantidad: String(Number(l.cantidad)), precio_unitario: String(Number(l.precio_unitario)),
      descuento_pct: String(Number(l.descuento_pct ?? 0)), impuesto_id: l.impuesto_id ?? '' })),
  }
}

function FormularioFactura({ editar, onClose, onGuardada }: {
  editar?: FacturaDetalle; onClose: () => void; onGuardada: (id: number) => void
}) {
  const qc = useQueryClient()
  const { centros, vendedores, impuestos, cuentas } = useApoyo()
  const [f, setF] = useState<Formulario>(editar ? desdeDetalle(editar) : { ...FORM_VACIO, fecha: hoyISO() })
  const [pagoAlCrear, setPagoAlCrear] = useState(false)
  const [pago, setPago] = useState({ cuenta: '' as number | '', medio: '' })
  const [firma, setFirma] = useState<Blob | null>(null)
  const [verLienzo, setVerLienzo] = useState(false)
  const [advertencias, setAdvertencias] = useState<string[]>([])
  const [leyendo, setLeyendo] = useState(false)
  const archivoIA = useRef<HTMLInputElement>(null)
  const ia = useQuery({ queryKey: ['erp-ia-estado'], staleTime: 300_000, enabled: !editar,
    queryFn: () => apiClient.get(`${API}/ia/estado`).then(r => r.data as { visible: boolean; disponible: boolean; motivo?: string }) })

  const set = <K extends keyof Formulario>(k: K, v: Formulario[K]) => setF(p => ({ ...p, [k]: v }))
  const setLinea = (i: number, k: keyof Linea, v: string | number) =>
    setF(p => ({ ...p, lineas: p.lineas.map((l, j) => j === i ? { ...l, [k]: v } : l) }))
  const tarifa = (id: number | '') => Number(impuestos.find(t => t.id === id)?.porcentaje ?? 0)
  const calc = f.lineas.map(l => calcularLinea(l, tarifa(l.impuesto_id)))
  const tot = calc.reduce((a, c) => ({ bruto: a.bruto + c.bruto, descuento: a.descuento + c.descuento,
    impuestos: a.impuestos + c.impuesto, total: a.total + c.total }), { bruto: 0, descuento: 0, impuestos: 0, total: 0 })
  const correoMalo = !!f.cliente_email && !CORREO.test(f.cliente_email.trim())

  const buscarCliente = async () => {
    const id = f.cliente_identificacion.trim()
    if (!id) return
    try {
      const r = await apiClient.get(`${API}/clientes/${encodeURIComponent(id)}`)
      setF(p => ({ ...p, tercero_id: r.data.id, cliente_nombre: r.data.razon_social,
        cliente_email: r.data.email ?? p.cliente_email, cliente_tipo_identificacion: r.data.tipo_identificacion }))
    } catch { setF(p => ({ ...p, tercero_id: null })) }
  }

  const leerConIA = async (archivo: File) => {
    setLeyendo(true)
    try {
      const datos = new FormData()
      datos.append('archivo', archivo)
      const r = await apiClient.post(`${API}/ia/extraer`, datos)
      const b = r.data.borrador
      setF(p => ({ ...p, ...Object.fromEntries(Object.entries(b).filter(([k, v]) => v !== null && k !== 'lineas')),
        fecha: b.fecha ?? p.fecha, fecha_vencimiento: b.fecha_vencimiento ?? '',
        lineas: b.lineas.length ? b.lineas.map((l: any) => ({ referencia: l.referencia ?? '', descripcion: l.descripcion,
          cantidad: String(l.cantidad), precio_unitario: String(l.precio_unitario),
          descuento_pct: String(l.descuento_pct), impuesto_id: l.impuesto_id ?? '' })) : p.lineas }) as Formulario)
      setAdvertencias(r.data.advertencias)
      toast.success('Formulario completado: revise los datos antes de guardar')
    } catch (e) {
      toast.error(mensajeDeError(e, 'No se pudo leer el archivo'))
    } finally {
      setLeyendo(false)
      if (archivoIA.current) archivoIA.current.value = ''
    }
  }

  const guardar = useMutation({
    mutationFn: async () => {
      const cuerpo: Record<string, unknown> = {
        ...f, centro_costo_id: f.centro_costo_id || null, vendedor_id: f.vendedor_id || null,
        forma_pago: f.forma_pago || null, medio_pago: f.medio_pago || null,
        fecha_vencimiento: f.fecha_vencimiento || null, cliente_email: f.cliente_email || null,
        cliente_identificacion: f.cliente_identificacion || null,
        lineas: f.lineas.map(l => ({ ...l, impuesto_id: l.impuesto_id || null })),
      }
      if (!editar && pagoAlCrear) {
        cuerpo.pago = { monto: r2(tot.total), cuenta_bancaria_id: pago.cuenta || null, metodo_pago: pago.medio || null, fecha: f.fecha }
      }
      const r = editar ? await apiClient.put(`${API}/${editar.id}`, cuerpo) : await apiClient.post(API, cuerpo)
      if (firma) {
        const datos = new FormData()
        datos.append('archivo', firma, 'firma.png')
        await apiClient.post(`${API}/${r.data.id}/firma`, datos)
      }
      return r.data.id as number
    },
    onSuccess: id => {
      toast.success(editar ? 'Factura actualizada' : 'Factura emitida')
      qc.invalidateQueries({ queryKey: ['facturas-venta'] })
      qc.invalidateQueries({ queryKey: ['factura-venta', id] })
      onGuardada(id)
    },
    onError: e => toast.error(mensajeDeError(e, 'No se pudo guardar la factura')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth aria-labelledby="titulo-factura">
      <DialogTitle id="titulo-factura" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {editar ? `Editar ${editar.estado === 'BORRADOR' ? 'borrador' : editar.numero}` : 'Nueva factura de venta'}
        {!editar && ia.data?.visible && (
          <Tooltip title={ia.data.disponible ? 'Completar usando un archivo o imagen (PDF, JPG, PNG)' : ia.data.motivo ?? ''}>
            <span style={{ marginLeft: 'auto' }}>
              <Button startIcon={<AutoAwesome />} disabled={!ia.data.disponible || leyendo}
                onClick={() => archivoIA.current?.click()} data-prueba="boton-ia">
                Agente de IA
              </Button>
            </span>
          </Tooltip>
        )}
        <input ref={archivoIA} type="file" hidden accept=".pdf,.jpg,.jpeg,.png"
          onChange={e => e.target.files?.[0] && leerConIA(e.target.files[0])} />
      </DialogTitle>
      {leyendo && <LinearProgress />}
      <DialogContent dividers>
        {leyendo && <Alert severity="info" sx={{ mb: 2 }}>El agente está leyendo el documento…</Alert>}
        {advertencias.map(a => <Alert key={a} severity="warning" sx={{ mb: 1 }}>{a}</Alert>)}
        <Grid container spacing={2}>
          <Grid item xs={12} sm={4}>
            <FormControl fullWidth size="small" disabled={!!editar}>
              <InputLabel id="campo-2">Tipo de documento</InputLabel>
              <Select labelId="campo-2" label="Tipo de documento" value={f.tipo_documento}
                onChange={e => set('tipo_documento', e.target.value as Formulario['tipo_documento'])}>
                <MenuItem value="FACTURA_VENTA">Factura de venta</MenuItem>
                <MenuItem value="TICKET">Ticket</MenuItem>
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={6} sm={2}>
            <TextField fullWidth size="small" label="Fecha" type="date" value={f.fecha}
              onChange={e => set('fecha', e.target.value)} InputLabelProps={{ shrink: true }} />
          </Grid>
          <Grid item xs={6} sm={2}>
            <TextField fullWidth size="small" label="Vencimiento" type="date" value={f.fecha_vencimiento}
              onChange={e => set('fecha_vencimiento', e.target.value)} InputLabelProps={{ shrink: true }} />
          </Grid>
          <Grid item xs={6} sm={2}>
            <FormControl fullWidth size="small">
              <InputLabel id="campo-3">Moneda</InputLabel>
              <Select labelId="campo-3" label="Moneda" value={f.moneda} onChange={e => set('moneda', e.target.value)}>
                {MONEDAS.map(m => <MenuItem key={m} value={m}>{m}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={6} sm={2}>
            <FormControl fullWidth size="small">
              <InputLabel id="campo-4">Centro de costos</InputLabel>
              <Select labelId="campo-4" label="Centro de costos" value={f.centro_costo_id}
                onChange={e => set('centro_costo_id', e.target.value as number | '')}>
                <MenuItem value="">Sin centro</MenuItem>
                {centros.map(c => <MenuItem key={c.id} value={c.id}>{c.codigo} · {c.nombre}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} sm={4}>
            <FormControl fullWidth size="small">
              <InputLabel id="campo-5">Vendedor</InputLabel>
              <Select labelId="campo-5" label="Vendedor" value={f.vendedor_id} onChange={e => set('vendedor_id', e.target.value as number | '')}>
                <MenuItem value="">Sin vendedor</MenuItem>
                {vendedores.map(v => <MenuItem key={v.id} value={v.id}>{v.razon_social}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={12} sm={4}>
            <SelectorCatalogo modulo="ERP" tipo="FORMA_PAGO" label="Forma de pago" valor={f.forma_pago} onChange={v => set('forma_pago', v)} />
          </Grid>
          <Grid item xs={12} sm={4}>
            <SelectorCatalogo modulo="ERP" tipo="MEDIO_PAGO" label="Medio de pago" valor={f.medio_pago} onChange={v => set('medio_pago', v)} />
          </Grid>

          <Grid item xs={12}><Divider textAlign="left">Cliente</Divider></Grid>
          <Grid item xs={4} sm={2}>
            <FormControl fullWidth size="small">
              <InputLabel id="campo-6">Tipo ID</InputLabel>
              <Select labelId="campo-6" label="Tipo ID" value={f.cliente_tipo_identificacion} onChange={e => set('cliente_tipo_identificacion', e.target.value)}>
                {TIPOS_ID.map(t => <MenuItem key={t} value={t}>{t}</MenuItem>)}
              </Select>
            </FormControl>
          </Grid>
          <Grid item xs={8} sm={3}>
            <TextField fullWidth size="small" label="Número de documento" value={f.cliente_identificacion}
              onChange={e => set('cliente_identificacion', e.target.value)} onBlur={buscarCliente}
              helperText={f.tercero_id ? 'Cliente registrado' : f.cliente_identificacion ? 'Se creará al guardar' : 'Vacío: consumidor final'}
              inputProps={{ 'data-prueba': 'cliente-identificacion' }} />
          </Grid>
          <Grid item xs={12} sm={4}>
            <TextField fullWidth size="small" required label="Nombre o razón social" value={f.cliente_nombre}
              onChange={e => set('cliente_nombre', e.target.value)} inputProps={{ 'data-prueba': 'cliente-nombre' }} />
          </Grid>
          <Grid item xs={12} sm={3}>
            <TextField fullWidth size="small" label="Correo electrónico" value={f.cliente_email}
              onChange={e => set('cliente_email', e.target.value)} error={correoMalo}
              helperText={correoMalo ? 'Formato de correo inválido' : ' '} />
          </Grid>

          <Grid item xs={12}><Divider textAlign="left">Ítems</Divider></Grid>
          <Grid item xs={12}>
            <Table size="small">
              <TableHead><TableRow>
                <TableCell>Referencia</TableCell><TableCell>Descripción</TableCell><TableCell>Cantidad</TableCell>
                <TableCell>Precio</TableCell><TableCell>Desc. %</TableCell><TableCell>Impuesto</TableCell>
                <TableCell align="right">Total</TableCell><TableCell />
              </TableRow></TableHead>
              <TableBody>
                {f.lineas.map((l, i) => (
                  <TableRow key={i} data-prueba="linea">
                    <TableCell><TextField size="small" value={l.referencia} onChange={e => setLinea(i, 'referencia', e.target.value)} /></TableCell>
                    <TableCell sx={{ minWidth: 200 }}><TextField size="small" fullWidth value={l.descripcion} onChange={e => setLinea(i, 'descripcion', e.target.value)} /></TableCell>
                    <TableCell><TextField size="small" type="number" value={l.cantidad} onChange={e => setLinea(i, 'cantidad', e.target.value)} sx={{ width: 90 }} /></TableCell>
                    <TableCell><TextField size="small" type="number" value={l.precio_unitario} onChange={e => setLinea(i, 'precio_unitario', e.target.value)} sx={{ width: 130 }} /></TableCell>
                    <TableCell><TextField size="small" type="number" value={l.descuento_pct} onChange={e => setLinea(i, 'descuento_pct', e.target.value)} sx={{ width: 80 }} /></TableCell>
                    <TableCell>
                      <Select size="small" value={l.impuesto_id} displayEmpty sx={{ minWidth: 110 }}
                        onChange={e => setLinea(i, 'impuesto_id', e.target.value as number | '')}>
                        <MenuItem value="">Sin IVA</MenuItem>
                        {impuestos.map(t => <MenuItem key={t.id} value={t.id}>{t.nombre}</MenuItem>)}
                      </Select>
                    </TableCell>
                    <TableCell align="right" data-prueba="total-linea">{plata(calc[i].total, f.moneda)}</TableCell>
                    <TableCell>
                      <IconButton size="small" disabled={f.lineas.length === 1}
                        onClick={() => setF(p => ({ ...p, lineas: p.lineas.filter((_, j) => j !== i) }))}><Delete fontSize="small" /></IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Button size="small" startIcon={<Add />} onClick={() => setF(p => ({ ...p, lineas: [...p.lineas, { ...LINEA_VACIA }] }))}
              data-prueba="agregar-linea">Agregar línea</Button>
          </Grid>
          <Grid item xs={12} sm={6}>
            <TextField fullWidth size="small" label="Remisión" value={f.remision} onChange={e => set('remision', e.target.value)} sx={{ mb: 2 }} />
            <TextField fullWidth size="small" multiline minRows={2} label="Notas (se imprimen)" value={f.notas} onChange={e => set('notas', e.target.value)} sx={{ mb: 2 }} />
            <TextField fullWidth size="small" multiline minRows={2} label="Notas adicionales (internas)" value={f.notas_adicionales} onChange={e => set('notas_adicionales', e.target.value)} sx={{ mb: 2 }} />
            {!editar && <TextField fullWidth size="small" multiline label="Comentario interno" value={f.comentario} onChange={e => set('comentario', e.target.value)} />}
          </Grid>
          <Grid item xs={12} sm={6}>
            <Box sx={{ p: 2, bgcolor: '#F8FAFC', borderRadius: 2 }} data-prueba="totales">
              {([['Subtotal', tot.bruto], ['Descuentos', -tot.descuento], ['Impuestos', tot.impuestos]] as const).map(([t, v]) => (
                <Box key={t} sx={{ display: 'flex', justifyContent: 'space-between' }}><span>{t}</span><span>{plata(v, f.moneda)}</span></Box>
              ))}
              <Divider sx={{ my: 1 }} />
              <Box sx={{ display: 'flex', justifyContent: 'space-between', fontWeight: 700 }}>
                <span>Total neto a pagar</span><span data-prueba="total-neto">{plata(tot.total, f.moneda)}</span>
              </Box>
            </Box>
            <Box sx={{ mt: 2 }}>
              <Button size="small" startIcon={<Draw />} onClick={() => setVerLienzo(v => !v)}>Capturar firma</Button>
              <Button size="small" component="label" startIcon={<AttachFile />}>
                Adjuntar firma<input hidden type="file" accept=".png,.jpg,.jpeg" onChange={e => setFirma(e.target.files?.[0] ?? null)} />
              </Button>
              {firma && <Chip size="small" label="Firma lista" onDelete={() => setFirma(null)} sx={{ ml: 1 }} />}
              {verLienzo && <LienzoFirma onListo={setFirma} />}
            </Box>
            {!editar && (
              <Box sx={{ mt: 2 }}>
                <FormControlLabel control={<Checkbox checked={pagoAlCrear} onChange={e => setPagoAlCrear(e.target.checked)} />}
                  label={`Registrar el pago ahora (${plata(tot.total, f.moneda)})`} />
                {pagoAlCrear && (
                  <Stack direction="row" spacing={1}>
                    <FormControl size="small" sx={{ minWidth: 180 }}>
                      <InputLabel id="campo-7">Cuenta bancaria</InputLabel>
                      <Select labelId="campo-7" label="Cuenta bancaria" value={pago.cuenta} onChange={e => setPago(p => ({ ...p, cuenta: e.target.value as number }))}>
                        {cuentas.map(c => <MenuItem key={c.id} value={c.id}>{c.numero}</MenuItem>)}
                      </Select>
                    </FormControl>
                    <SelectorCatalogo modulo="ERP" tipo="MEDIO_PAGO" label="Método" valor={pago.medio} onChange={v => setPago(p => ({ ...p, medio: v }))} />
                  </Stack>
                )}
              </Box>
            )}
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={guardar.isPending || correoMalo} onClick={() => guardar.mutate()}
          data-prueba="guardar-factura" sx={{ bgcolor: ERP_COLOR }}>
          Guardar
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Detalle ───────────────────────────────────────────────────────────────────
type Dialogo = null | 'pago' | 'editar' | 'nota_credito' | 'nota_debito' | 'retenciones'
  | 'cerrar_sin_pago' | 'convertir_borrador' | 'anular'

function DetalleFactura({ id, onClose, onAbrir }: { id: number; onClose: () => void; onAbrir: (id: number) => void }) {
  const qc = useQueryClient()
  const [dialogo, setDialogo] = useState<Dialogo>(null)
  const [comentario, setComentario] = useState('')
  const det = useQuery({ queryKey: ['factura-venta', id],
    queryFn: () => apiClient.get(`${API}/${id}`).then(r => r.data as FacturaDetalle) })
  const d = det.data
  const refrescar = () => {
    qc.invalidateQueries({ queryKey: ['factura-venta', id] })
    qc.invalidateQueries({ queryKey: ['facturas-venta'] })
  }
  const accion = useMutation({
    mutationFn: ({ ruta, cuerpo }: { ruta: string; cuerpo?: unknown }) => apiClient.post(`${API}/${id}/${ruta}`, cuerpo ?? {}),
    onSuccess: (r, v) => {
      refrescar()
      if (v.ruta === 'clonar') { toast.success('Borrador creado'); onAbrir(r.data.id) }
      else toast.success('Listo')
      setDialogo(null); setComentario('')
    },
    onError: e => toast.error(mensajeDeError(e)),
  })
  const adjuntar = async (archivo: File) => {
    const datos = new FormData()
    datos.append('archivo', archivo)
    try { await apiClient.post(`${API}/${id}/adjuntos`, datos); refrescar(); toast.success('Archivo adjuntado') }
    catch (e) { toast.error(mensajeDeError(e)) }
  }
  const descargarAdjunto = async (adj: { id: number; nombre: string }) => {
    const r = await apiClient.get(`${API}/${id}/adjuntos/${adj.id}`, { responseType: 'blob' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(r.data); a.download = adj.nombre; a.click()
  }
  const compartir = async () => {
    const url = `${window.location.origin}/erp/ingresos/factura-venta?factura=${id}`
    if (navigator.share) await navigator.share({ title: `Factura ${d?.numero}`, url }).catch(() => undefined)
    else { await navigator.clipboard.writeText(url); toast.success('Enlace copiado') }
  }

  const boton = (clave: string, texto: string, icono: React.ReactNode, alPulsar: () => void) => {
    const regla = d?.acciones[clave]
    return (
      <Tooltip key={clave} title={regla && !regla.permitida ? regla.motivo ?? '' : ''}>
        <span>
          <Button size="small" variant="outlined" startIcon={icono} disabled={!regla?.permitida || accion.isPending}
            onClick={alPulsar} data-prueba={`accion-${clave}`}>{texto}</Button>
        </span>
      </Tooltip>
    )
  }

  return (
    <Drawer anchor="right" open onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', md: 720 }, bgcolor: '#fff', color: 'text.primary', backgroundImage: 'none' } }}>
      {!d ? <LinearProgress /> : (
        <Box sx={{ p: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {d.tipo_documento === 'TICKET' ? 'Ticket' : 'Factura'} {d.estado === 'BORRADOR' ? '(borrador)' : d.numero}
            </Typography>
            <Chip size="small" label={d.vencida ? 'Vencida' : d.estado_visible} data-prueba="estado-detalle" />
            <IconButton sx={{ ml: 'auto' }} onClick={onClose}><Close /></IconButton>
          </Box>
          <Typography variant="body2" sx={{ color: '#64748B' }}>
            {d.cliente_nombre}{d.cliente_nit ? ` · ${d.cliente_nit}` : ''} · {d.fecha} → vence {d.fecha_vencimiento}
          </Typography>
          {d.cerrada_motivo && <Alert severity="warning" sx={{ mt: 1 }}>Cerrada sin pago: {d.cerrada_motivo}</Alert>}

          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ my: 2 }}>
            {boton('imprimir', 'Imprimir', <Print />, () => abrirPdf(id, d.numero, 'imprimir'))}
            {boton('descargar_pdf', 'Descargar PDF', <PictureAsPdf />, () => abrirPdf(id, d.numero, 'descargar'))}
            {boton('compartir', 'Compartir', <Share />, compartir)}
            {boton('agregar_pago', 'Agregar pago', <Payments />, () => setDialogo('pago'))}
            {boton('aplicar_anticipo', 'Aplicar anticipo', <Payments />, () => undefined)}
            {boton('cerrar_sin_pago', 'Cerrar sin pago', <Gavel />, () => setDialogo('cerrar_sin_pago'))}
            {boton('nota_credito', 'Nota crédito', <NoteAdd />, () => setDialogo('nota_credito'))}
            {boton('nota_debito', 'Nota débito', <NoteAdd />, () => setDialogo('nota_debito'))}
            {boton('editar_retenciones', 'Editar retenciones', <Percent />, () => setDialogo('retenciones'))}
            {boton('editar', 'Editar', <Edit />, () => setDialogo('editar'))}
            {boton('clonar', 'Clonar', <ContentCopy />, () => accion.mutate({ ruta: 'clonar' }))}
            {boton('volver_recurrente', 'Volver recurrente', <Undo />, () => undefined)}
            {boton('convertir_borrador', 'Convertir a borrador', <Undo />, () => setDialogo('convertir_borrador'))}
            {boton('anular', 'Anular', <Block />, () => setDialogo('anular'))}
            {d.estado === 'BORRADOR' && (
              <Button size="small" variant="contained" startIcon={<Send />} onClick={() => accion.mutate({ ruta: 'emitir' })}
                data-prueba="accion-emitir">Emitir</Button>
            )}
            <Tooltip title={d.acciones.adjuntar?.permitida ? '' : d.acciones.adjuntar?.motivo ?? ''}>
              <Button size="small" variant="outlined" component="label" startIcon={<AttachFile />}>
                Adjuntar archivos<input hidden type="file" onChange={e => e.target.files?.[0] && adjuntar(e.target.files[0])} />
              </Button>
            </Tooltip>
          </Stack>

          <Table size="small">
            <TableHead><TableRow><TableCell>Ref.</TableCell><TableCell>Descripción</TableCell><TableCell align="right">Cant.</TableCell>
              <TableCell align="right">Precio</TableCell><TableCell align="right">Total</TableCell></TableRow></TableHead>
            <TableBody>{d.lineas.map(l => (
              <TableRow key={l.id}><TableCell>{l.referencia}</TableCell><TableCell>{l.descripcion}</TableCell>
                <TableCell align="right">{Number(l.cantidad)}</TableCell><TableCell align="right">{plata(l.precio_unitario, d.moneda ?? 'COP')}</TableCell>
                <TableCell align="right">{plata(l.total, d.moneda ?? 'COP')}</TableCell></TableRow>
            ))}</TableBody>
          </Table>
          <Box sx={{ mt: 2, ml: 'auto', maxWidth: 320 }}>
            {([['Subtotal', Number(d.subtotal) + Number(d.descuento)], ['Descuentos', -Number(d.descuento)],
              ['Impuestos', d.total_impuestos], ['Total', d.total],
              ['Retenciones', -(Number(d.retencion_fuente) + Number(d.retencion_ica) + Number(d.retencion_iva))],
              ['Saldo', d.saldo]] as const).map(([t, v]) => (
              <Box key={t} sx={{ display: 'flex', justifyContent: 'space-between', fontWeight: t === 'Saldo' ? 700 : 400 }}>
                <span>{t}</span><span>{plata(v, d.moneda ?? 'COP')}</span></Box>
            ))}
            {d.retenciones_editadas && <Typography variant="caption" color="warning.main">Retenciones modificadas a mano</Typography>}
          </Box>

          {d.pagos.length > 0 && <Seccion titulo="Pagos">{d.pagos.map(p => (
            <Typography key={p.id} variant="body2">{p.fecha} · {p.numero} · {plata(p.monto, d.moneda ?? 'COP')} {p.metodo_pago ? `· ${p.metodo_pago}` : ''}</Typography>))}</Seccion>}
          {d.notas.length > 0 && <Seccion titulo="Notas crédito y débito">{d.notas.map(n => (
            <Typography key={`${n.tipo}${n.id}`} variant="body2">{n.fecha} · {n.tipo === 'CREDITO' ? 'Crédito' : 'Débito'} {n.numero} · {plata(n.total, d.moneda ?? 'COP')} · {n.motivo}</Typography>))}</Seccion>}
          {d.adjuntos.length > 0 && <Seccion titulo="Adjuntos">{d.adjuntos.map(a => (
            <Button key={a.id} size="small" startIcon={<AttachFile />} onClick={() => descargarAdjunto(a)}>{a.nombre}</Button>))}</Seccion>}
          {d.observaciones && <Seccion titulo="Notas"><Typography variant="body2">{d.observaciones}</Typography></Seccion>}
          {d.notas_adicionales && <Seccion titulo="Notas adicionales (internas)"><Typography variant="body2">{d.notas_adicionales}</Typography></Seccion>}
          {d.cufe && <Seccion titulo="CUFE"><Typography variant="caption" sx={{ wordBreak: 'break-all' }}>{d.cufe}</Typography></Seccion>}

          <Seccion titulo="Historial">
            {d.historial.map(h => (
              <Typography key={h.id} variant="body2" data-prueba="historial">
                {new Date(h.created_at).toLocaleString('es-CO')} · {h.usuario} · {h.detalle ?? h.tipo}
              </Typography>
            ))}
            <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
              <TextField size="small" fullWidth placeholder="Comentario interno" value={comentario} onChange={e => setComentario(e.target.value)} />
              <Button disabled={!comentario.trim()} onClick={() => accion.mutate({ ruta: 'comentarios', cuerpo: { texto: comentario } })}>Comentar</Button>
            </Stack>
          </Seccion>
        </Box>
      )}

      {d && dialogo === 'pago' && <PagoDialog factura={d} onClose={() => setDialogo(null)} />}
      {d && dialogo === 'editar' && <FormularioFactura editar={d} onClose={() => setDialogo(null)} onGuardada={() => setDialogo(null)} />}
      {d && (dialogo === 'cerrar_sin_pago' || dialogo === 'convertir_borrador' || dialogo === 'anular') && (
        <MotivoDialog
          titulo={{ cerrar_sin_pago: `Cerrar sin pago (castigar ${plata(d.saldo, d.moneda ?? 'COP')})`,
            convertir_borrador: 'Convertir a borrador', anular: 'Anular factura' }[dialogo]}
          aviso={{ cerrar_sin_pago: 'El saldo se da por perdido y se contabiliza como gasto. No se puede deshacer.',
            convertir_borrador: 'Se reversa el asiento; la factura vuelve a ser editable con el mismo número.',
            anular: 'Se reversa el asiento y la factura queda anulada.' }[dialogo]}
          ocupado={accion.isPending} onClose={() => setDialogo(null)}
          onConfirmar={motivo => accion.mutate({ ruta: { cerrar_sin_pago: 'castigar', convertir_borrador: 'borrador', anular: 'anular' }[dialogo], cuerpo: { motivo } })} />
      )}
      {d && (dialogo === 'nota_credito' || dialogo === 'nota_debito') && (
        <NotaDialog tipo={dialogo} ocupado={accion.isPending} onClose={() => setDialogo(null)}
          onConfirmar={cuerpo => accion.mutate({ ruta: dialogo === 'nota_credito' ? 'notas-credito' : 'notas-debito', cuerpo })} />
      )}
      {d && dialogo === 'retenciones' && <RetencionesDialog factura={d} onClose={() => setDialogo(null)} onGuardado={refrescar} />}
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

function MotivoDialog({ titulo, aviso, ocupado, onClose, onConfirmar }: {
  titulo: string; aviso: string; ocupado: boolean; onClose: () => void; onConfirmar: (m: string) => void
}) {
  const [motivo, setMotivo] = useState('')
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="titulo-motivo">
      <DialogTitle id="titulo-motivo">{titulo}</DialogTitle>
      <DialogContent>
        <Alert severity="warning" sx={{ mb: 2 }}>{aviso}</Alert>
        <TextField fullWidth multiline minRows={2} required label="Motivo" value={motivo} onChange={e => setMotivo(e.target.value)}
          inputProps={{ 'data-prueba': 'motivo' }} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" color="warning" disabled={ocupado || !motivo.trim()} onClick={() => onConfirmar(motivo.trim())}
          data-prueba="confirmar-motivo">Confirmar</Button>
      </DialogActions>
    </Dialog>
  )
}

function NotaDialog({ tipo, ocupado, onClose, onConfirmar }: {
  tipo: 'nota_credito' | 'nota_debito'; ocupado: boolean; onClose: () => void; onConfirmar: (c: object) => void
}) {
  const [valor, setValor] = useState('')
  const [tarifa, setTarifa] = useState('0')
  const [motivo, setMotivo] = useState('')
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="titulo-nota">
      <DialogTitle id="titulo-nota">{tipo === 'nota_credito' ? 'Aplicar nota crédito' : 'Aplicar nota débito'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Valor (sin IVA)" type="number" value={valor} onChange={e => setValor(e.target.value)} />
          <TextField label="Tarifa de IVA %" type="number" value={tarifa} onChange={e => setTarifa(e.target.value)} />
          <TextField label="Motivo" required multiline value={motivo} onChange={e => setMotivo(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={ocupado || !valor || !motivo.trim()}
          onClick={() => onConfirmar({ valor, tarifa_iva: tarifa || 0, motivo: motivo.trim() })}>Aplicar</Button>
      </DialogActions>
    </Dialog>
  )
}

function RetencionesDialog({ factura, onClose, onGuardado }: { factura: FacturaDetalle; onClose: () => void; onGuardado: () => void }) {
  const sug = useQuery({ queryKey: ['retenciones-sugeridas', factura.id],
    queryFn: () => apiClient.get(`${API}/${factura.id}/retenciones/sugeridas`).then(r => r.data as Record<'fuente' | 'ica' | 'iva', { valor: string; motivo?: string | null }>) })
  const [v, setV] = useState<Record<'fuente' | 'ica' | 'iva', string> | null>(null)
  useEffect(() => {
    if (sug.data && !v) setV({ fuente: sug.data.fuente.valor, ica: sug.data.ica.valor, iva: sug.data.iva.valor })
  }, [sug.data, v])
  const guardar = useMutation({
    mutationFn: () => apiClient.put(`${API}/${factura.id}/retenciones`, {
      retencion_fuente: v?.fuente || 0, retencion_ica: v?.ica || 0, retencion_iva: v?.iva || 0 }),
    onSuccess: () => { toast.success('Retenciones guardadas'); onGuardado(); onClose() },
    onError: e => toast.error(mensajeDeError(e)),
  })
  const etiquetas = { fuente: 'Retención en la fuente', ica: 'ReteICA', iva: 'ReteIVA' } as const
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="titulo-retenciones">
      <DialogTitle id="titulo-retenciones">Editar retenciones</DialogTitle>
      <DialogContent>
        {!v ? <LinearProgress /> : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            {(Object.keys(etiquetas) as (keyof typeof etiquetas)[]).map(k => (
              <TextField key={k} label={etiquetas[k]} type="number" value={v[k]}
                onChange={e => setV(p => p && ({ ...p, [k]: e.target.value }))}
                helperText={sug.data?.[k].motivo ?? `Sugerido: ${sug.data?.[k].valor}`} />
            ))}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancelar</Button>
        <Button variant="contained" disabled={!v || guardar.isPending} onClick={() => guardar.mutate()}>Guardar</Button>
      </DialogActions>
    </Dialog>
  )
}

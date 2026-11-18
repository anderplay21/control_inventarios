import React, { useState } from 'react'
import {
  Box,
  Card,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Chip,
  Skeleton,
  Grid,
  Select,
  MenuItem,
  FormControl,
  alpha,
  IconButton,
  Tooltip,
} from '@mui/material'
import {
  Add,
  Receipt,
  Send,
  Download,
  QrCode,
  Search,
  FilterList,
} from '@mui/icons-material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { Layout } from '@/components/layout/Layout'
import toast from 'react-hot-toast'
import { useNavigate } from 'react-router-dom'
import { exportarPDF } from '@/utils/exportar'
import { format } from 'date-fns'

import { COLOR_MODULO } from '@/config/marca'
import { mensajeDeError } from '@/utils/errorApi'
// ── Module brand ──────────────────────────────────────────────────────────────
const ERP_COLOR = COLOR_MODULO

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatCurrency(value: number | undefined | null): string {
  if (value == null) return '$ 0'
  return new Intl.NumberFormat('es-CO', {
    style: 'currency',
    currency: 'COP',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
}

function safeFormat(dateStr: string | undefined | null, fmt = 'dd/MM/yyyy'): string {
  if (!dateStr) return '—'
  try {
    return format(new Date(dateStr), fmt)
  } catch {
    return '—'
  }
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function currentMonthISO(): string {
  return new Date().toISOString().slice(0, 7)
}

// ── Types ─────────────────────────────────────────────────────────────────────
type EstadoFactura = 'BORRADOR' | 'EMITIDA' | 'PAGADA' | 'ANULADA' | 'VENCIDA'

interface Factura {
  id: number
  numero: string
  cliente_nombre: string
  cliente_nit?: string
  cliente_email?: string
  fecha: string
  fecha_vencimiento: string
  total: number
  estado: EstadoFactura
  cufe?: string
  moneda?: string
  concepto?: string
}



// ── Estado config ─────────────────────────────────────────────────────────────
const ESTADO_CONFIG: Record<EstadoFactura, { bg: string; color: string; border: string; label: string }> = {
  BORRADOR: { bg: '#F8FAFC', color: '#64748B', border: '#E2E8F0', label: 'Borrador' },
  EMITIDA:  { bg: '#EFF6FF', color: '#1D4ED8', border: '#BFDBFE', label: 'Emitida' },
  PAGADA:   { bg: '#F0FDF4', color: '#16A34A', border: '#BBF7D0', label: 'Pagada' },
  VENCIDA:  { bg: '#FEF2F2', color: '#DC2626', border: '#FECACA', label: 'Vencida' },
  ANULADA:  { bg: '#1E293B', color: '#94A3B8', border: '#334155', label: 'Anulada' },
}

// ── KPI Card ──────────────────────────────────────────────────────────────────
interface KpiCardProps {
  label: string
  value: string
  icon: React.ReactNode
  accent: string
  loading?: boolean
  sub?: string
}

function KpiCard({ label, value, icon, accent, loading, sub }: KpiCardProps) {
  return (
    <Card
      sx={{
        p: 2.5,
        borderRadius: '16px',
        border: '1px solid',
        borderColor: alpha(accent, 0.15),
        background: `linear-gradient(135deg, ${alpha(accent, 0.04)} 0%, #fff 100%)`,
        boxShadow: `0 1px 4px ${alpha(accent, 0.08)}, 0 0 0 1px ${alpha(accent, 0.06)}`,
        position: 'relative',
        overflow: 'hidden',
        '&::before': {
          content: '""',
          position: 'absolute',
          top: 0,
          left: 0,
          width: 4,
          height: '100%',
          bgcolor: accent,
          borderRadius: '16px 0 0 16px',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5 }}>
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: '10px',
            bgcolor: alpha(accent, 0.12),
            color: accent,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {icon}
        </Box>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography
            sx={{
              fontSize: '0.68rem',
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: '#94A3B8',
              mb: 0.5,
            }}
          >
            {label}
          </Typography>
          {loading ? (
            <Skeleton width={100} height={28} />
          ) : (
            <Typography
              sx={{
                fontSize: '1.25rem',
                fontWeight: 800,
                color: accent,
                lineHeight: 1.2,
                letterSpacing: '-0.02em',
              }}
            >
              {value}
            </Typography>
          )}
          {sub && !loading && (
            <Typography sx={{ fontSize: '0.72rem', color: '#94A3B8', mt: 0.5 }}>
              {sub}
            </Typography>
          )}
        </Box>
      </Box>
    </Card>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function ERPFacturacion() {
  const qc = useQueryClient()

  const navigate = useNavigate()

  // — Filters
  const [filtroEstado, setFiltroEstado] = useState<string>('TODOS')
  const [searchCliente, setSearchCliente] = useState('')


  // ── Queries ──────────────────────────────────────────────────────────────
  const { data: facturas = [], isLoading: loadingFacturas } = useQuery<Factura[]>({
    queryKey: ['erp-fe-facturas'],
    queryFn: () => apiClient.get('/erp/cxc/facturas').then((r) => r.data),
  })

  // ── Mutations ─────────────────────────────────────────────────────────────
  const emitirFactura = useMutation({
    mutationFn: (id: number) =>
      apiClient.patch(`/erp/cxc/facturas/${id}`, { estado: 'EMITIDA' }).then((r) => r.data),
    onSuccess: () => {
      toast.success('Factura enviada a la DIAN')
      qc.invalidateQueries({ queryKey: ['erp-fe-facturas'] })
    },
    onError: (e: any) => toast.error(mensajeDeError(e, 'Error al emitir la factura')),
  })

  // ── KPI derivations ────────────────────────────────────────────────────────
  const today = todayISO()
  const currentMonth = currentMonthISO()

  const emitidosHoy = facturas.filter((f) => f.fecha?.startsWith(today)).length
  const emitidosMes = facturas.filter((f) => f.fecha?.startsWith(currentMonth)).length
  const valorMes = facturas
    .filter((f) => f.fecha?.startsWith(currentMonth))
    .reduce((s, f) => s + (f.total ?? 0), 0)
  const pendientesDIAN = facturas.filter((f) => f.estado === 'BORRADOR').length

  // ── Filtered list ──────────────────────────────────────────────────────────
  const facturasFiltradas = facturas.filter((f) => {
    const matchEstado = filtroEstado === 'TODOS' || f.estado === filtroEstado
    const matchCliente = f.cliente_nombre
      .toLowerCase()
      .includes(searchCliente.toLowerCase())
    return matchEstado && matchCliente
  })

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <Layout title="ERP — Facturación Electrónica">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <Box sx={{ mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 0.5 }}>
          <Box
            sx={{
              width: 36,
              height: 36,
              borderRadius: '10px',
              background: `linear-gradient(135deg, ${ERP_COLOR} 0%, #0D2347 100%)`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <Receipt sx={{ color: '#fff', fontSize: 18 }} />
          </Box>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2, color: '#1E293B' }}>
              Facturación Electrónica
            </Typography>
            <Typography variant="body2" sx={{ color: '#64748B', fontSize: 12 }}>
              ERP · Motor de emisión DIAN — resolución 42946607
            </Typography>
          </Box>
          <Chip
            label="FE"
            size="small"
            sx={{
              ml: 'auto',
              bgcolor: alpha(ERP_COLOR, 0.1),
              color: ERP_COLOR,
              fontWeight: 700,
              fontSize: 11,
              height: 24,
              letterSpacing: '0.05em',
            }}
          />
        </Box>
      </Box>

      {/* ── KPI cards ─────────────────────────────────────────────────────── */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <KpiCard
            label="Emitidas hoy"
            value={String(emitidosHoy)}
            icon={<Receipt sx={{ fontSize: 18 }} />}
            accent={ERP_COLOR}
            loading={loadingFacturas}
            sub={safeFormat(today)}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <KpiCard
            label="Emitidas este mes"
            value={String(emitidosMes)}
            icon={<QrCode sx={{ fontSize: 18 }} />}
            accent="#0369A1"
            loading={loadingFacturas}
            sub={currentMonth}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <KpiCard
            label="Valor facturado mes"
            value={formatCurrency(valorMes)}
            icon={<Receipt sx={{ fontSize: 18 }} />}
            accent="#16A34A"
            loading={loadingFacturas}
            sub="Facturas del mes actual"
          />
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <KpiCard
            label="Pendientes por enviar"
            value={String(pendientesDIAN)}
            icon={<Send sx={{ fontSize: 18 }} />}
            accent={pendientesDIAN > 0 ? '#D97706' : '#64748B'}
            loading={loadingFacturas}
            sub="Estado BORRADOR"
          />
        </Grid>
      </Grid>

      {/* ── Main card ─────────────────────────────────────────────────────── */}
      <Card
        sx={{
          borderRadius: '16px',
          border: '1px solid #E2E8F0',
          boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
          overflow: 'hidden',
        }}
      >
        {/* Toolbar */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.5,
            px: 2,
            py: 1.5,
            borderBottom: '1px solid #F1F5F9',
            flexWrap: 'wrap',
          }}
        >
          {/* Search */}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.75,
              bgcolor: '#F8FAFC',
              border: '1px solid #E2E8F0',
              borderRadius: '10px',
              px: 1.5,
              py: 0.5,
              width: 240,
            }}
          >
            <Search sx={{ fontSize: 16, color: '#94A3B8' }} />
            <input
              value={searchCliente}
              onChange={(e) => setSearchCliente(e.target.value)}
              placeholder="Buscar cliente..."
              style={{
                border: 'none',
                background: 'transparent',
                outline: 'none',
                fontSize: '0.8rem',
                color: '#1E293B',
                width: '100%',
              }}
            />
          </Box>

          {/* Estado filter */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <FilterList sx={{ fontSize: 16, color: '#94A3B8' }} />
            <FormControl size="small" sx={{ minWidth: 170 }}>
              <Select
                value={filtroEstado}
                onChange={(e) => setFiltroEstado(e.target.value)}
                displayEmpty
                sx={{ fontSize: '0.8rem', height: 34 }}
              >
                <MenuItem value="TODOS">Todos los estados</MenuItem>
                {(Object.keys(ESTADO_CONFIG) as EstadoFactura[]).map((key) => (
                  <MenuItem key={key} value={key}>
                    {ESTADO_CONFIG[key].label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>

          <Box sx={{ ml: 'auto' }}>
            <Button
              variant="contained"
              size="small"
              startIcon={<Add />}
              onClick={() => navigate('/erp/ingresos/factura-venta')}
              sx={{
                bgcolor: ERP_COLOR,
                '&:hover': { bgcolor: '#0D2347' },
                height: 34,
                px: 2,
                fontSize: '0.8rem',
              }}
            >
              Nueva factura
            </Button>
          </Box>
        </Box>

        {/* Table */}
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Número</TableCell>
                <TableCell>Cliente</TableCell>
                <TableCell>NIT</TableCell>
                <TableCell>Fecha</TableCell>
                <TableCell>Venc.</TableCell>
                <TableCell align="right">Total</TableCell>
                <TableCell>Estado</TableCell>
                <TableCell>CUFE</TableCell>
                <TableCell align="center">Acciones</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {loadingFacturas
                ? Array.from({ length: 6 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 9 }).map((__, j) => (
                        <TableCell key={j}>
                          <Skeleton height={20} />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                : facturasFiltradas.length === 0
                ? (
                    <TableRow>
                      <TableCell colSpan={9} align="center" sx={{ py: 7 }}>
                        <Receipt sx={{ fontSize: 40, color: '#CBD5E1', mb: 1 }} />
                        <Typography sx={{ color: '#94A3B8', fontSize: '0.875rem' }}>
                          No se encontraron facturas
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )
                : facturasFiltradas.map((f) => {
                    const estadoCfg = ESTADO_CONFIG[f.estado] ?? ESTADO_CONFIG.BORRADOR
                    const cufeShort = f.cufe
                      ? `${f.cufe.slice(0, 8)}…${f.cufe.slice(-4)}`
                      : '—'
                    return (
                      <TableRow key={f.id}>
                        <TableCell>
                          <Typography
                            sx={{
                              fontSize: '0.8rem',
                              fontWeight: 700,
                              color: ERP_COLOR,
                              fontFamily: 'monospace',
                            }}
                          >
                            {f.numero}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
                            {f.cliente_nombre}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography
                            sx={{ fontSize: '0.78rem', color: '#64748B', fontFamily: 'monospace' }}
                          >
                            {f.cliente_nit ?? '—'}
                          </Typography>
                        </TableCell>
                        <TableCell sx={{ color: '#64748B', fontSize: '0.8rem' }}>
                          {safeFormat(f.fecha)}
                        </TableCell>
                        <TableCell sx={{ color: '#64748B', fontSize: '0.8rem' }}>
                          {safeFormat(f.fecha_vencimiento)}
                        </TableCell>
                        <TableCell align="right">
                          <Typography sx={{ fontSize: '0.875rem', fontWeight: 600 }}>
                            {formatCurrency(f.total)}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={estadoCfg.label}
                            size="small"
                            sx={{
                              bgcolor: estadoCfg.bg,
                              color: estadoCfg.color,
                              border: `1px solid ${estadoCfg.border}`,
                              fontWeight: 600,
                              fontSize: '0.7rem',
                              height: 22,
                            }}
                          />
                        </TableCell>
                        <TableCell>
                          {f.cufe ? (
                            <Tooltip title={f.cufe} arrow>
                              <Typography
                                sx={{
                                  fontSize: '0.72rem',
                                  color: '#64748B',
                                  fontFamily: 'monospace',
                                  cursor: 'default',
                                }}
                              >
                                {cufeShort}
                              </Typography>
                            </Tooltip>
                          ) : (
                            <Typography sx={{ color: '#CBD5E1', fontSize: '0.8rem' }}>—</Typography>
                          )}
                        </TableCell>
                        <TableCell align="center">
                          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0.5 }}>
                            {f.estado === 'BORRADOR' && (
                              <Tooltip title="Enviar a DIAN">
                                <IconButton
                                  size="small"
                                  disabled={emitirFactura.isPending}
                                  onClick={() => emitirFactura.mutate(f.id)}
                                  sx={{
                                    color: ERP_COLOR,
                                    '&:hover': { bgcolor: alpha(ERP_COLOR, 0.08) },
                                  }}
                                >
                                  <Send sx={{ fontSize: 16 }} />
                                </IconButton>
                              </Tooltip>
                            )}
                            <Tooltip title="Descargar PDF">
                              <IconButton
                                size="small"
                                onClick={() => exportarPDF({
                                  archivo: `factura-${f.numero}`,
                                  titulo: `Factura ${f.numero}`,
                                  subtitulo: f.cliente_nombre,
                                  color: ERP_COLOR,
                                  columnas: [{ key: 'campo', header: 'Campo' }, { key: 'valor', header: 'Valor' }],
                                  filas: [
                                    { campo: 'N° Factura', valor: f.numero },
                                    { campo: 'Cliente', valor: f.cliente_nombre },
                                    { campo: 'NIT', valor: f.cliente_nit },
                                    { campo: 'Fecha', valor: f.fecha },
                                    { campo: 'Vencimiento', valor: f.fecha_vencimiento },
                                    { campo: 'Estado', valor: f.estado },
                                    { campo: 'Total', valor: f.total },
                                  ],
                                })}
                                sx={{ color: '#64748B', '&:hover': { bgcolor: '#F1F5F9' } }}
                              >
                                <Download sx={{ fontSize: 16 }} />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        </TableCell>
                      </TableRow>
                    )
                  })}
            </TableBody>
          </Table>
        </Box>
      </Card>

    </Layout>
  )
}

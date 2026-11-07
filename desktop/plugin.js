// receipt-ledger — desktop half.
// Shows the receipt ledger: which charges have paper, which don't, where to go
// get the missing ones, and the structural fixes worth making so next year is
// cheaper. Reads everything through its own backend namespace
// (/api/plugins/receipt-ledger), never the filesystem.
// ALLOWED IMPORTS: 'react', 'react/jsx-runtime', '@hermes/plugin-sdk' — nothing else.
import { jsx } from 'react/jsx-runtime'
import { useState } from 'react'
import {
  Badge,
  Button,
  Codicon,
  EmptyState,
  Input,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  host,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'

const PAGE_PATH = '/receipt-ledger'
const QUERY_KEY = ['receipt-ledger', 'ledger']

const money = value => `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const text = {
  dim: { color: 'var(--ui-text-secondary)', fontSize: 12 },
  faint: { color: 'var(--ui-text-quaternary)', fontSize: 12 },
  mono: { fontFamily: 'var(--ui-font-mono, monospace)', fontSize: 12 },
  card: {
    border: '1px solid var(--ui-stroke-secondary)',
    borderRadius: 8,
    padding: '12px 14px',
    marginBottom: 12
  }
}

const rowStyle = {
  display: 'grid',
  gridTemplateColumns: '92px 1fr 100px 92px 1fr',
  gap: 10,
  padding: '6px 4px',
  borderTop: '1px solid var(--ui-stroke-secondary)',
  alignItems: 'center',
  fontSize: 13
}

async function call(ctx, path, options) {
  try {
    return await ctx.rest(path, options)
  } catch (error) {
    const message = String((error && error.message) || error)
    if (message.includes('404') || message.includes('405')) {
      throw new Error('The receipt-ledger backend is not loaded. Enable the plugin in config.yaml, then reload plugins.')
    }
    throw error
  }
}

function Stat({ label, value, tone }) {
  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 2 },
    children: [
      jsx('span', { style: text.faint, children: label }),
      jsx('span', {
        style: { fontSize: 20, fontWeight: 600, color: tone || 'var(--ui-text-primary)' },
        children: value
      })
    ]
  })
}

function SetupCard({ ctx, onSaved }) {
  const [receiptsDir, setReceiptsDir] = useState('')
  const [csvPath, setCsvPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const ledger = await call(ctx, '/settings', {
        method: 'POST',
        body: { receipts_dir: receiptsDir, transactions_csv: csvPath }
      })
      onSaved(ledger)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  return jsx('div', {
    style: text.card,
    children: [
      jsx('div', { style: { fontWeight: 600, marginBottom: 6 }, children: 'Point it at your paper' }),
      jsx('div', {
        style: { ...text.dim, marginBottom: 10 },
        children: 'A folder of receipts and a CSV of charges. It reads filenames and amounts, never uploads anything.'
      }),
      jsx('div', { style: { display: 'flex', gap: 8, marginBottom: 8 }, children: [
        jsx(Input, {
          value: receiptsDir,
          placeholder: '/home/you/receipts',
          onChange: event => setReceiptsDir(event.target.value),
          style: { flex: 1 }
        }),
        jsx(Input, {
          value: csvPath,
          placeholder: '/home/you/transactions-2026.csv',
          onChange: event => setCsvPath(event.target.value),
          style: { flex: 1 }
        }),
        jsx(Button, { onClick: save, disabled: busy, children: busy ? 'Scanning…' : 'Save and scan' })
      ] }),
      error ? jsx('div', { style: { color: 'var(--ui-danger, #c0392b)', fontSize: 12 }, children: error }) : null
    ]
  })
}

function LedgerPage({ ctx }) {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const ledgerQuery = useQuery({ queryKey: QUERY_KEY, queryFn: () => call(ctx, '/ledger') })
  const statusQuery = useQuery({ queryKey: ['receipt-ledger', 'status'], queryFn: () => call(ctx, '/status') })

  const ledger = ledgerQuery.data || {}
  const counts = ledger.counts || {}
  const configured = Boolean(statusQuery.data && statusQuery.data.configured)
  const rows = ledger.transactions || []
  const advice = ledger.advice || []

  const refresh = data => {
    queryClient.setQueryData(QUERY_KEY, data)
    queryClient.invalidateQueries({ queryKey: ['receipt-ledger', 'status'] })
  }

  const scan = async () => {
    setBusy('scan')
    setNotice('')
    try {
      refresh(await call(ctx, '/scan', { method: 'POST' }))
    } catch (err) {
      setNotice(String(err.message || err))
    } finally {
      setBusy('')
    }
  }

  const buildPackage = async () => {
    setBusy('package')
    setNotice('')
    try {
      const result = await call(ctx, '/package', { method: 'POST', body: {} })
      setNotice(`Wrote ${result.receipts} receipts to ${result.archive} (${result.still_missing} still missing).`)
    } catch (err) {
      setNotice(String(err.message || err))
    } finally {
      setBusy('')
    }
  }

  if (ledgerQuery.error) {
    return jsx('div', { style: { padding: 20 }, children: jsx(EmptyState, { title: 'Backend not loaded', description: String(ledgerQuery.error.message || ledgerQuery.error) }) })
  }

  return jsx('div', {
    style: { padding: '18px 20px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 },
    children: [
      jsx('div', { style: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }, children: [
        jsx('div', { style: { fontSize: 18, fontWeight: 600 }, children: 'Receipt ledger' }),
        jsx('div', { style: { ...text.faint, marginLeft: 'auto' }, children: ledger.generated_at ? `scanned ${ledger.generated_at.replace('T', ' ')}` : 'not scanned yet' }),
        jsx(Button, { variant: 'secondary', onClick: scan, disabled: busy === 'scan', children: busy === 'scan' ? 'Scanning…' : 'Rescan' }),
        jsx(Button, { onClick: buildPackage, disabled: busy === 'package' || !counts.receipts, children: busy === 'package' ? 'Building…' : 'Build accountant package' })
      ] }),
      !configured ? jsx(SetupCard, { ctx, onSaved: refresh }) : null,
      notice ? jsx('div', { style: { ...text.dim, marginBottom: 8 }, children: notice }) : null,
      jsx('div', { style: { display: 'flex', gap: 28, margin: '8px 0 14px' }, children: [
        jsx(Stat, { label: 'Charges', value: counts.transactions || 0 }),
        jsx(Stat, { label: 'Matched', value: counts.matched || 0 }),
        jsx(Stat, { label: 'Missing paper', value: counts.missing || 0, tone: counts.missing ? 'var(--ui-warning, #b9791f)' : undefined }),
        jsx(Stat, { label: 'Missing dollars', value: money(counts.missing_dollars) }),
        jsx(Stat, { label: 'Duplicates dropped', value: counts.duplicates_dropped || 0 })
      ] }),
      rows.length
        ? jsx('div', { children: [
          jsx('div', { style: { ...text.faint, display: 'grid', gridTemplateColumns: '92px 1fr 100px 92px 1fr', gap: 10, paddingBottom: 4 }, children: ['Date', 'Vendor', 'Amount', 'State', 'Paper'].map(label => jsx('span', { key: label, children: label })) }),
          ...rows.map(row => jsx('div', {
            key: row.id,
            style: rowStyle,
            children: [
              jsx('span', { style: text.mono, children: row.date }),
              jsx('span', { children: row.vendor }),
              jsx('span', { style: text.mono, children: money(row.amount) }),
              jsx(Badge, { variant: row.status === 'matched' ? 'success' : 'warn', children: row.status === 'matched' ? 'paper' : 'missing' }),
              row.receipt
                ? jsx('span', { style: { ...text.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: row.receipt, children: row.receipt_name })
                : jsx('span', { style: text.dim, children: row.portal || 'no portal known' })
            ]
          }))
        ] })
        : jsx(EmptyState, { title: 'No charges yet', description: 'Point the plugin at a receipts folder and a transactions CSV.' }),
      advice.length
        ? jsx('div', { style: { marginTop: 20 }, children: [
          jsx('div', { style: { fontWeight: 600, marginBottom: 8 }, children: 'What to fix so next year is cheaper' }),
          ...advice.map((item, index) => jsx('div', { key: `${item.kind}-${index}`, style: text.card, children: [
            jsx('div', { style: { display: 'flex', alignItems: 'center', gap: 8 }, children: [
              jsx(Codicon, { name: 'lightbulb', style: { color: 'var(--ui-accent)' } }),
              jsx('span', { style: { fontWeight: 600 }, children: item.title })
            ] }),
            jsx('div', { style: { ...text.dim, marginTop: 4 }, children: item.evidence }),
            jsx('div', { style: { ...text.faint, marginTop: 4 }, children: item.fix })
          ] }))
        ] })
        : null
    ]
  })
}

export default {
  id: 'receipt-ledger',
  name: 'Receipt Ledger',
  register(ctx) {
    ctx.register({
      id: 'receipt-ledger.page',
      area: ROUTES_AREA,
      title: 'Receipt Ledger',
      data: { path: PAGE_PATH },
      render: () => jsx(LedgerPage, { ctx })
    })

    ctx.register({
      id: 'receipt-ledger.nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: PAGE_PATH, label: 'Receipt Ledger', codicon: 'files' }
    })

    ctx.register({
      id: 'receipt-ledger.rescan',
      area: PALETTE_AREA,
      data: {
        id: 'receipt-ledger.rescan',
        label: 'Receipt Ledger: rescan',
        keywords: ['receipt', 'ledger', 'tax', 'receipts'],
        run: async () => {
          try {
            await call(ctx, '/scan', { method: 'POST' })
            host.notify({ kind: 'success', message: 'Receipt ledger rescanned.' })
          } catch (error) {
            host.notify({ kind: 'error', message: String(error.message || error) })
          }
        }
      }
    })
  }
}

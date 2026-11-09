// receipt-ledger — desktop half.
//
// One page that answers a boring, expensive question: which charges have paper
// and which do not. Charges come from a self-hosted Securo instance (or a CSV
// export); paper comes from Securo attachments (or a folder). Everything runs
// through this plugin's own backend namespace (/api/plugins/receipt-ledger) —
// the renderer never touches the filesystem and never sees a credential.
//
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
  SegmentedControl,
  host,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'

const PAGE_PATH = '/receipt-ledger'
const LEDGER_KEY = ['receipt-ledger', 'ledger']
const STATUS_KEY = ['receipt-ledger', 'status']

const money = value =>
  `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const text = {
  dim: { color: 'var(--ui-text-secondary)', fontSize: 12 },
  faint: { color: 'var(--ui-text-quaternary)', fontSize: 12 },
  mono: { fontFamily: 'var(--ui-font-mono, monospace)', fontSize: 12 },
  card: {
    border: '1px solid var(--ui-stroke-secondary)',
    borderRadius: 8,
    padding: '12px 14px',
    marginBottom: 12
  },
  sectionTitle: { fontSize: 13, fontWeight: 600, marginBottom: 8 }
}

const GRID = '96px minmax(150px, 1fr) 96px 88px minmax(120px, 1.2fr)'

const rowStyle = {
  display: 'grid',
  gridTemplateColumns: GRID,
  gap: 10,
  padding: '7px 4px',
  borderTop: '1px solid var(--ui-stroke-secondary)',
  alignItems: 'center',
  fontSize: 13
}

const fieldRow = { display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center', flexWrap: 'wrap' }

/** Turn "502: {"detail":"…","hint":"…"}" into the sentence a person can act on. */
function humanError(error) {
  const raw = String((error && error.message) || error || '')
  const brace = raw.indexOf('{')
  if (brace === -1) return raw
  try {
    const payload = JSON.parse(raw.slice(brace))
    if (payload.detail || payload.error) {
      return [payload.error || payload.detail, payload.hint].filter(Boolean).join(' ')
    }
  } catch {
    /* not JSON — fall through to the raw message */
  }
  return raw
}

async function call(ctx, path, options) {
  try {
    return await ctx.rest(path, options)
  } catch (error) {
    const message = humanError(error)
    if (message.includes('404') || message.includes('405')) {
      throw new Error('The receipt-ledger backend is not loaded. Enable the plugin in config.yaml, then reload plugins.')
    }
    throw new Error(message)
  }
}

function Stat({ label, value, tone }) {
  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 78 },
    children: [
      jsx('span', { style: text.faint, children: label }),
      jsx('span', {
        style: { fontSize: 20, fontWeight: 600, color: tone || 'var(--ui-text-primary)' },
        children: value
      })
    ]
  })
}

function Label({ children }) {
  return jsx('span', { style: { ...text.faint, width: 96, flexShrink: 0 }, children })
}

function SetupCard({ ctx, status, onSaved, onCancel }) {
  const settings = (status && status.settings) || {}
  const [mode, setMode] = useState(status && status.mode === 'files' ? 'files' : 'securo')
  const [url, setUrl] = useState(settings.securo_url || '')
  const [email, setEmail] = useState(settings.securo_email || '')
  const [password, setPassword] = useState('')
  const [token, setToken] = useState('')
  const [receiptsDir, setReceiptsDir] = useState(settings.receipts_dir || '')
  const [csvPath, setCsvPath] = useState(settings.transactions_csv || '')
  const [fromDate, setFromDate] = useState(settings.from_date || '')
  const [toDate, setToDate] = useState(settings.to_date || '')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [probe, setProbe] = useState(null)

  const securoBody = () => {
    const body = { securo_url: url, securo_email: email, from_date: fromDate, to_date: toDate }
    if (password) body.securo_password = password
    if (token) body.securo_token = token
    return body
  }

  const test = async () => {
    setBusy('test')
    setError('')
    setProbe(null)
    try {
      const result = await call(ctx, '/securo/test', {
        method: 'POST',
        body: { securo_url: url, securo_email: email, securo_password: password || undefined, securo_token: token || undefined }
      })
      setProbe(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  const save = async () => {
    setBusy('save')
    setError('')
    try {
      const body = mode === 'securo' ? { ...securoBody(), receipts_dir: receiptsDir } : {
        receipts_dir: receiptsDir,
        transactions_csv: csvPath
      }
      const result = await call(ctx, '/settings', { method: 'POST', body })
      onSaved(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  return jsx('div', {
    style: text.card,
    children: [
      jsx('div', { style: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }, children: [
        jsx('span', { style: { fontWeight: 600 }, children: 'Where the charges come from' }),
        jsx('div', { style: { marginLeft: 'auto' }, children: jsx(SegmentedControl, {
          options: [
            { id: 'securo', label: 'Securo' },
            { id: 'files', label: 'CSV + folder' }
          ],
          value: mode,
          sizing: 'content',
          onChange: value => setMode(value)
        }) }),
        onCancel
          ? jsx(Button, { variant: 'outline', size: 'sm', onClick: onCancel, children: 'Close' })
          : null
      ] }),
      mode === 'securo'
        ? jsx('div', { children: [
          jsx('div', { style: text.dim, marginBottom: 10, children: 'Reads charges and their attachments from your own Securo instance. The credential stays on this machine.' }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Instance URL' }),
            jsx(Input, { value: url, placeholder: 'http://securo.home', onChange: event => setUrl(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Securo URL' })
          ] }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Email' }),
            jsx(Input, { value: email, placeholder: 'you@example.com', onChange: event => setEmail(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Securo email' })
          ] }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Password' }),
            jsx(Input, { value: password, type: 'password', placeholder: 'your Securo password', onChange: event => setPassword(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Securo password' })
          ] }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Access token' }),
            jsx(Input, { value: token, type: 'password', placeholder: 'optional, sct_… (used instead of password)', onChange: event => setToken(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Securo access token' })
          ] }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Charges from' }),
            jsx(Input, { value: fromDate, placeholder: '2026-01-01', onChange: event => setFromDate(event.target.value), style: { width: 130 }, 'aria-label': 'From date' }),
            jsx('span', { style: text.faint, children: 'to' }),
            jsx(Input, { value: toDate, placeholder: 'today', onChange: event => setToDate(event.target.value), style: { width: 130 }, 'aria-label': 'To date' }),
            jsx('span', { style: { ...text.faint, marginLeft: 6 }, children: 'blank = this calendar year' })
          ] }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Receipt folder' }),
            jsx(Input, { value: receiptsDir, placeholder: 'optional: an old folder of PDFs', onChange: event => setReceiptsDir(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Receipts folder' })
          ] }),
          probe
            ? jsx('div', {
              style: {
                ...text.dim,
                border: '1px solid var(--ui-stroke-secondary)',
                borderRadius: 6,
                padding: '6px 8px',
                marginBottom: 8,
                color: probe.ok ? 'var(--ui-text-secondary)' : 'var(--ui-danger, #c0392b)'
              },
              children: probe.ok
                ? `Connected to ${probe.base_url} as ${probe.email} — ${probe.accounts} account${probe.accounts === 1 ? '' : 's'}, workspace ${probe.workspace || 'personal'}.`
                : `${probe.error || 'Connection failed.'} ${probe.hint || ''}`
            })
            : null
        ] })
        : jsx('div', { children: [
          jsx('div', { style: { ...text.dim, marginBottom: 10 }, children: 'No Securo? Export charges as CSV and point the plugin at a folder of receipts. It matches on amount, vendor and date.' }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Transactions' }),
            jsx(Input, { value: csvPath, placeholder: '/home/you/transactions-2026.csv', onChange: event => setCsvPath(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Transactions CSV' })
          ] }),
          jsx('div', { style: fieldRow, children: [
            jsx(Label, { children: 'Receipts' }),
            jsx(Input, { value: receiptsDir, placeholder: '/home/you/receipts', onChange: event => setReceiptsDir(event.target.value), style: { flex: 1, minWidth: 220 }, 'aria-label': 'Receipts folder' })
          ] })
        ] }),
      error ? jsx('div', { style: { color: 'var(--ui-danger, #c0392b)', fontSize: 12, marginTop: 4 }, children: error }) : null,
      jsx('div', { style: { display: 'flex', gap: 8, marginTop: 10 }, children: [
        mode === 'securo'
          ? jsx(Button, { variant: 'outline', size: 'sm', onClick: test, disabled: busy !== '' || !url, children: busy === 'test' ? 'Testing…' : 'Test connection' })
          : null,
        jsx(Button, { size: 'sm', onClick: save, disabled: busy !== '', children: busy === 'save' ? 'Scanning…' : 'Save and scan' })
      ] })
    ]
  })
}

function LedgerPage({ ctx }) {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [filter, setFilter] = useState('all')
  const [showSetup, setShowSetup] = useState(false)
  const ledgerQuery = useQuery({ queryKey: LEDGER_KEY, queryFn: () => call(ctx, '/ledger') })
  const statusQuery = useQuery({ queryKey: STATUS_KEY, queryFn: () => call(ctx, '/status', { timeoutMs: 8000 }) })

  const ledger = ledgerQuery.data || {}
  const status = statusQuery.data || {}
  const counts = ledger.counts || {}
  const rows = ledger.transactions || []
  const advice = ledger.advice || []
  const totals = ledger.totals || {}
  const configured = Boolean(status.configured)
  const setupOpen = showSetup || !configured
  const visible = filter === 'missing' ? rows.filter(row => row.status === 'missing') : rows

  const refresh = (nextStatus, nextLedger) => {
    if (nextLedger) queryClient.setQueryData(LEDGER_KEY, nextLedger)
    if (nextStatus) queryClient.setQueryData(STATUS_KEY, nextStatus)
    queryClient.invalidateQueries({ queryKey: STATUS_KEY })
  }

  const scan = async () => {
    setBusy('scan')
    setNotice('')
    try {
      refresh(null, await call(ctx, '/scan', { method: 'POST', timeoutMs: 60000 }))
    } catch (err) {
      setNotice(err.message)
    } finally {
      setBusy('')
    }
  }

  const buildPackage = async () => {
    setBusy('package')
    setNotice('')
    try {
      const result = await call(ctx, '/package', { method: 'POST', body: {}, timeoutMs: 120000 })
      const problems = (result.errors || []).length
      setNotice(
        `Wrote ${result.receipts} receipts to ${result.archive}. ${result.still_missing} still missing.` +
          (problems ? ` ${problems} download error${problems === 1 ? '' : 's'} — rebuild to retry.` : '')
      )
    } catch (err) {
      setNotice(err.message)
    } finally {
      setBusy('')
    }
  }

  const onSaved = result => {
    setShowSetup(false)
    setNotice('')
    queryClient.setQueryData(STATUS_KEY, { ...(status || {}), ...result, ledger: undefined })
    if (result.ledger) queryClient.setQueryData(LEDGER_KEY, result.ledger)
    queryClient.invalidateQueries({ queryKey: STATUS_KEY })
  }

  const sourceLine = () => {
    const source = ledger.source || status.source || {}
    if (source.kind === 'securo') {
      return `Securo at ${source.securo_url} · ${source.from} to ${source.to} · ${source.accounts || 0} accounts`
    }
    if (source.kind === 'files') {
      return `Files · ${source.transactions_csv || 'no CSV set'}`
    }
    return 'not scanned yet'
  }

  if (ledgerQuery.error && !ledgerQuery.data) {
    return jsx('div', {
      style: { padding: 20 },
      children: jsx(EmptyState, {
        title: 'Backend not loaded',
        description: String(ledgerQuery.error.message || ledgerQuery.error)
      })
    })
  }

  return jsx('div', {
    style: { padding: '18px 20px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 },
    children: [
      jsx('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 2 }, children: [
        jsx('div', { style: { fontSize: 18, fontWeight: 600 }, children: 'Receipt ledger' }),
        jsx('div', { style: { ...text.faint, marginLeft: 2 }, children: sourceLine() }),
        jsx('div', { style: { ...text.faint, marginLeft: 'auto' }, children: ledger.generated_at ? `scanned ${ledger.generated_at.replace('T', ' ')}` : 'not scanned yet' }),
        jsx(Button, { variant: 'outline', size: 'sm', onClick: () => setShowSetup(open => !open), children: showSetup ? 'Hide setup' : 'Setup' }),
        jsx(Button, { variant: 'secondary', size: 'sm', onClick: scan, disabled: busy !== '', children: busy === 'scan' ? 'Scanning…' : 'Rescan' }),
        jsx(Button, {
          size: 'sm',
          onClick: buildPackage,
          disabled: busy !== '' || !counts.with_paper,
          children: busy === 'package' ? 'Building…' : 'Accountant package'
        })
      ] }),
      notice ? jsx('div', { style: { ...text.dim, margin: '6px 0 4px' }, children: notice }) : null,
      setupOpen ? jsx(SetupCard, { ctx, status, onSaved, onCancel: configured ? () => setShowSetup(false) : null }) : null,
      counts.charges
        ? jsx('div', { style: { display: 'flex', gap: 26, margin: '10px 0 12px', flexWrap: 'wrap' }, children: [
          jsx(Stat, { label: 'Charges', value: counts.charges }),
          jsx(Stat, { label: 'With paper', value: counts.with_paper || 0 }),
          jsx(Stat, {
            label: 'Missing',
            value: counts.missing || 0,
            tone: counts.missing ? 'var(--ui-warning, #b9791f)' : undefined
          }),
          jsx(Stat, { label: 'Missing dollars', value: money(counts.missing_dollars) }),
          jsx(Stat, {
            label: 'Coverage',
            value: `${Number(counts.coverage_pct || 0).toFixed(0)}%`,
            tone: Number(counts.coverage_pct || 0) >= 90 ? undefined : 'var(--ui-warning, #b9791f)'
          })
        ] })
        : null,
      rows.length
        ? jsx('div', { children: [
          jsx('div', { style: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }, children: [
            jsx(SegmentedControl, {
              options: [
                { id: 'all', label: `All ${rows.length}` },
                { id: 'missing', label: `Missing ${counts.missing || 0}` }
              ],
              value: filter,
              sizing: 'content',
              onChange: value => setFilter(value)
            }),
            jsx('div', { style: { ...text.faint, marginLeft: 'auto' }, children: 'paper = attached in Securo or matched from the folder' })
          ] }),
          jsx('div', {
            style: { ...text.faint, display: 'grid', gridTemplateColumns: GRID, gap: 10, paddingBottom: 4 },
            children: ['Date', 'Vendor', 'Amount', 'Paper', 'Where to get it'].map(label =>
              jsx('span', { key: label, children: label })
            )
          }),
          ...visible.map(row =>
            jsx('div', {
              key: row.id,
              style: rowStyle,
              children: [
                jsx('span', { style: text.mono, children: row.date }),
                jsx('div', { style: { minWidth: 0 }, children: [
                  jsx('div', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: row.description || row.vendor, children: row.vendor }),
                  row.category || row.account
                    ? jsx('div', { style: text.faint, children: [row.category, row.account].filter(Boolean).join(' · ') })
                    : null
                ] }),
                jsx('span', { style: text.mono, children: money(row.amount) }),
                jsx('span', { children: jsx(Badge, {
                  variant: row.status === 'missing' ? 'warn' : 'success',
                  children: row.status === 'attached' ? 'securo' : row.status === 'matched' ? 'file' : 'missing'
                }) }),
                row.status === 'missing'
                  ? jsx('span', { style: { ...text.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: row.portal || '', children: row.portal || 'no portal known' })
                  : jsx('span', {
                    style: { ...text.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                    title: row.receipt_name || '',
                    children: row.receipt_name || (row.attachment_count ? `${row.attachment_count} attachment${row.attachment_count === 1 ? '' : 's'}` : 'attached')
                  })
              ]
            })
          )
        ] })
        : jsx(EmptyState, {
          title: configured ? 'No charges in this window' : 'Not connected yet',
          description: configured
            ? 'The window has no charges. Widen the date range in Setup.'
            : 'Connect a Securo instance, or point the plugin at a CSV and a receipts folder.'
        }),
      (totals.by_vendor || []).length
        ? jsx('div', { style: { display: 'flex', gap: 24, marginTop: 22, flexWrap: 'wrap' }, children: [
          jsx('div', { style: { flex: '1 1 260px', minWidth: 240 }, children: [
            jsx('div', { style: text.sectionTitle, children: 'Spend by vendor' }),
            ...(totals.by_vendor || []).slice(0, 8).map(bucket =>
              jsx('div', { key: bucket.key, style: { display: 'flex', gap: 8, padding: '3px 0', fontSize: 12 }, children: [
                jsx('span', { style: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: bucket.key }),
                jsx('span', { style: text.faint, children: bucket.count }),
                jsx('span', { style: { ...text.mono, minWidth: 78, textAlign: 'right' }, children: money(bucket.dollars) })
              ] })
            )
          ] }),
          (totals.by_category || []).length
            ? jsx('div', { style: { flex: '1 1 260px', minWidth: 240 }, children: [
              jsx('div', { style: text.sectionTitle, children: 'Spend by category' }),
              ...(totals.by_category || []).slice(0, 8).map(bucket =>
                jsx('div', { key: bucket.key, style: { display: 'flex', gap: 8, padding: '3px 0', fontSize: 12 }, children: [
                  jsx('span', { style: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: bucket.key }),
                  jsx('span', { style: text.faint, children: bucket.count }),
                  jsx('span', { style: { ...text.mono, minWidth: 78, textAlign: 'right' }, children: money(bucket.dollars) })
                ] })
              )
            ] })
            : null
        ] })
        : null,
      advice.length
        ? jsx('div', { style: { marginTop: 22 }, children: [
          jsx('div', { style: text.sectionTitle, children: 'What to fix so next year is cheaper' }),
          ...advice.map((item, index) =>
            jsx('div', { key: `${item.kind}-${index}`, style: text.card, children: [
              jsx('div', { style: { display: 'flex', alignItems: 'center', gap: 8 }, children: [
                jsx(Codicon, { name: 'lightbulb', style: { color: 'var(--ui-accent)' } }),
                jsx('span', { style: { fontWeight: 600 }, children: item.title })
              ] }),
              jsx('div', { style: { ...text.dim, marginTop: 4 }, children: item.evidence }),
              jsx('div', { style: { ...text.faint, marginTop: 4 }, children: item.fix })
            ] })
          )
        ] })
        : null,
      jsx('div', { style: { ...text.faint, marginTop: 18, paddingBottom: 12 }, children:
        'Receipts and charges stay on this machine. The only network call is to the Securo URL you set. Matched paper is not a tax determination — you and your accountant decide that.' })
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
        keywords: ['receipt', 'ledger', 'tax', 'receipts', 'securo'],
        run: async () => {
          try {
            const ledger = await call(ctx, '/scan', { method: 'POST', timeoutMs: 60000 })
            const counts = (ledger && ledger.counts) || {}
            host.notify({
              kind: 'success',
              message: `Receipt ledger: ${counts.with_paper || 0}/${counts.charges || 0} charges have paper.`
            })
          } catch (error) {
            host.notify({ kind: 'error', message: String(error.message || error) })
          }
        }
      }
    })
  }
}

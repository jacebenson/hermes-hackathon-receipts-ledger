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

function Money({ value, tone }) {
  return jsx('span', {
    style: { fontFamily: 'var(--ui-font-mono, monospace)', fontSize: 12, color: tone || 'var(--ui-text-primary)' },
    children: money(value)
  })
}

const DECISION_LABELS = {
  business: 'Business',
  personal: 'Personal',
  mixed: 'Mixed',
  owner_reimbursement: 'LLC owes me',
  owner_contribution: 'Money I put in'
}

function ClassificationPanel({ ctx, ledger, onChanged }) {
  const [busy, setBusy] = useState('')
  const [mixedFor, setMixedFor] = useState('')
  const [pct, setPct] = useState('25')
  const [basis, setBasis] = useState('')
  const [notice, setNotice] = useState('')
  const summary = ledger.classification_summary || {}
  const queue = (ledger.review_queue || []).slice(0, 8)

  const decide = async (row, decision, extra) => {
    setBusy(row.id)
    setNotice('')
    try {
      const result = await call(ctx, '/classification/rule', {
        method: 'POST',
        body: {
          decision,
          match: { vendor_contains: row.vendor },
          ...extra,
          note: `Set from the review queue for ${row.vendor}`
        }
      })
      if (!result.ok) throw new Error(result.error || 'The rule was refused.')
      setMixedFor('')
      setNotice(`Saved. ${row.vendor} now follows that rule.`)
      await onChanged()
    } catch (err) {
      setNotice(String(err.message || err))
    } finally {
      setBusy('')
    }
  }

  const rows = [
    ['Business', summary.business_dollars, 'var(--ui-text-primary)'],
    ['Personal', summary.personal_dollars, 'var(--ui-text-secondary)'],
    ['Mixed (business share)', summary.mixed_business_dollars, 'var(--ui-text-secondary)'],
    ['LLC owes you', summary.reimbursable_to_owner_dollars, 'var(--ui-warning, #b9791f)'],
    ['Unreviewed', summary.unreviewed_dollars, 'var(--ui-warning, #b9791f)']
  ]

  return jsx('div', { style: { marginTop: 22 }, children: [
    jsx('div', { style: text.sectionTitle, children: 'Business or personal' }),
    jsx('div', {
      style: { ...text.faint, marginBottom: 8 },
      children: summary.reconciles
        ? 'Every charge sits in exactly one bucket. Nothing is counted as business until you decide it.'
        : 'These totals do not reconcile yet, which means a record is incomplete. Treat them as provisional.'
    }),
    jsx('div', { style: { display: 'flex', gap: 22, flexWrap: 'wrap', marginBottom: 10 }, children:
      rows.map(([label, value, tone]) => jsx('div', {
        key: label,
        style: { display: 'flex', flexDirection: 'column', gap: 2 },
        children: [
          jsx('span', { style: text.faint, children: label }),
          jsx(Money, { value, tone })
        ]
      }))
    }),
    queue.length
      ? jsx('div', { children: [
        jsx('div', { style: { ...text.faint, marginBottom: 4 }, children: `${(ledger.review_queue || []).length} charges need a decision. The proposal is a suggestion, not a ruling.` }),
        ...queue.map(row => jsx('div', { key: row.id, style: { ...text.card, padding: '8px 10px' }, children: [
          jsx('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }, children: [
            jsx('span', { style: text.mono, children: row.date }),
            jsx('span', { style: { fontWeight: 600 }, children: row.vendor }),
            jsx(Money, { value: row.amount }),
            jsx('span', { style: text.faint, children: row.account || row.card || '' }),
            row.review && row.review.proposal && row.review.proposal.decision
              ? jsx(Badge, { variant: row.review.proposal.confidence === 'high' ? 'default' : 'muted', children: `suggests ${DECISION_LABELS[row.review.proposal.decision] || row.review.proposal.decision}` })
              : jsx(Badge, { variant: 'outline', children: 'no suggestion' })
          ] }),
          row.review && row.review.proposal && row.review.proposal.reasons
            ? jsx('div', { style: { ...text.faint, marginTop: 3 }, children: row.review.proposal.reasons.join(' ') })
            : null,
          mixedFor === row.id
            ? jsx('div', { style: { display: 'flex', gap: 6, marginTop: 6, alignItems: 'center', flexWrap: 'wrap' }, children: [
              jsx(Input, { value: pct, onChange: event => setPct(event.target.value), style: { width: 70 }, 'aria-label': 'Business percent' }),
              jsx('span', { style: text.faint, children: '% business, because' }),
              jsx(Input, { value: basis, placeholder: '26% of a 180 sq ft office', onChange: event => setBasis(event.target.value), style: { flex: 1, minWidth: 160 }, 'aria-label': 'Basis for the percentage' }),
              jsx(Button, {
                size: 'sm',
                disabled: busy === row.id || !basis,
                onClick: () => decide(row, 'mixed', { business_pct: Number(pct), basis })
              }, 'Save split')
            ] })
            : jsx('div', { style: { display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }, children: [
              jsx(Button, { size: 'xs', variant: 'outline', disabled: busy === row.id, onClick: () => decide(row, 'business', {}) }, 'Business'),
              jsx(Button, { size: 'xs', variant: 'outline', disabled: busy === row.id, onClick: () => decide(row, 'personal', {}) }, 'Personal'),
              jsx(Button, { size: 'xs', variant: 'outline', disabled: busy === row.id, onClick: () => setMixedFor(row.id) }, 'Mixed'),
              jsx(Button, { size: 'xs', variant: 'outline', disabled: busy === row.id, onClick: () => decide(row, 'owner_reimbursement', {}) }, 'LLC owes me')
            ] })
        ] }))
      ] })
      : jsx('div', { style: text.faint, children: summary.counts && summary.counts.charges
        ? 'Nothing waiting. Every charge follows a rule you set.'
        : 'No settled charges yet. Run a scan first.' }),
    notice ? jsx('div', { style: { ...text.dim, marginTop: 6 }, children: notice }) : null
  ] })
}

function JurisdictionPanel({ ctx, status }) {
  const initial = (status && status.settings && status.settings.business_state) || ''
  const [stateId, setStateId] = useState(initial ? `us-${String(initial).toLowerCase()}` : 'us-federal')
  const [query, setQuery] = useState(initial || 'MN')
  const [bundle, setBundle] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = async id => {
    setBusy('load')
    setError('')
    try {
      const data = await call(ctx, `/jurisdictions?state_id=${encodeURIComponent(id)}`)
      setBundle(data)
      setStateId(id)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy('')
    }
  }

  const go = () => {
    const code = String(query || '').trim().toLowerCase()
    const id = code.length === 2 ? `us-${code}` : code || 'us-federal'
    load(id)
  }

  const record = bundle && bundle.record
  const confidenceVariant = record && record.confidence === 'verified' ? 'success' : record && record.confidence === 'seeded' ? 'muted' : 'warn'

  return jsx('div', { style: { marginTop: 22 }, children: [
    jsx('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }, children: [
      jsx('div', { style: text.sectionTitle, children: 'Where your business answers to' }),
      jsx('div', { style: { ...text.faint, marginLeft: 'auto' }, children: bundle ? bundle.statement : 'federal plus fifty states and DC' })
    ] }),
    jsx('div', { style: { display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }, children: [
      jsx(Input, { value: query, placeholder: 'MN', onChange: event => setQuery(event.target.value), style: { width: 80 }, 'aria-label': 'State code' }),
      jsx(Button, { size: 'xs', variant: 'outline', onClick: go, disabled: busy !== '' }, busy === 'load' ? 'Loading…' : 'Show'),
      jsx(Button, { size: 'xs', variant: 'outline', onClick: () => { setQuery('US'); load('us-federal') } }, 'Federal')
    ] }),
    error ? jsx('div', { style: { color: 'var(--ui-danger, #c0392b)', fontSize: 12 }, children: error }) : null,
    record
      ? jsx('div', { style: text.card, children: [
        jsx('div', { style: { display: 'flex', gap: 8, alignItems: 'center' }, children: [
          jsx('span', { style: { fontWeight: 600 }, children: record.name }),
          jsx(Badge, { variant: confidenceVariant, children: record.confidence }),
          jsx('span', { style: { ...text.faint, marginLeft: 'auto' }, children: `checked ${record.updated_on}` })
        ] }),
        (record.obligations || []).length
          ? jsx('div', { style: { marginTop: 8 }, children: record.obligations.map(item => jsx('div', { key: item.id, style: { padding: '6px 0', borderTop: '1px solid var(--ui-stroke-secondary)' }, children: [
            jsx('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }, children: [
              jsx('span', { style: { fontWeight: 600 }, children: item.title }),
              jsx('span', { style: text.faint, children: item.form }),
              jsx(Badge, { variant: item.next_due ? 'default' : 'muted', children: item.next_due ? `next ${item.next_due}` : item.due_rule })
            ] }),
            jsx('div', { style: { ...text.faint, marginTop: 2 }, children: `"${item.quote}"` }),
            jsx('div', { style: { ...text.faint, marginTop: 2, wordBreak: 'break-all' }, children: item.source_url })
          ] })) })
          : jsx('div', { style: { ...text.dim, marginTop: 6 }, children: record.confidence === 'unknown'
            ? 'Sources could not be reached from here, so this record makes no claims. Open them in a browser.'
            : 'No rule has been read for this jurisdiction yet. The official pages are listed below; your agent can fill this in.' }),
        jsx('div', { style: { ...text.faint, marginTop: 8 }, children: Object.entries(record.sources || {}).map(([key, source]) =>
          jsx('div', { key, style: { display: 'flex', gap: 6, padding: '2px 0' }, children: [
            jsx('span', { style: { width: 74, flexShrink: 0 }, children: key }),
            jsx(Badge, { variant: source.status === 'ok' ? 'success' : source.status === 'blocked' ? 'warn' : 'destructive', children: source.status }),
            jsx('span', { style: { wordBreak: 'break-all' }, children: source.url })
          ] })
        ) })
      ] })
      : null
  ] })
}

function AuditPanel({ ctx }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')

  const load = async () => {
    setBusy('load')
    try {
      setData(await call(ctx, '/audit'))
    } catch {
      setData(null)
    } finally {
      setBusy('')
    }
  }

  const clear = async () => {
    setBusy('clear')
    try {
      await call(ctx, '/audit/clear', { method: 'POST' })
      setData(await call(ctx, '/audit'))
    } finally {
      setBusy('')
    }
  }

  if (!data) {
    return jsx('div', { style: { marginTop: 22 }, children: [
      jsx('div', { style: text.sectionTitle, children: 'What this plugin did' }),
      jsx(Button, { size: 'xs', variant: 'outline', onClick: load, disabled: busy !== '' }, busy === 'load' ? 'Loading…' : 'Show the journal')
    ] })
  }

  return jsx('div', { style: { marginTop: 22 }, children: [
    jsx('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }, children: [
      jsx('div', { style: text.sectionTitle, children: 'What this plugin did' }),
      jsx('div', { style: { ...text.faint, marginLeft: 'auto' }, children: `${data.entries} entries, ${data.failures} failures` }),
      jsx(Button, { size: 'xs', variant: 'outline', onClick: load, disabled: busy !== '' }, 'Refresh'),
      jsx(Button, { size: 'xs', variant: 'outline', onClick: clear, disabled: busy !== '' }, 'Clear')
    ] }),
    jsx('div', { style: { ...text.dim, marginBottom: 8 }, children: [
      data.last_scan ? `Last read: ${data.last_scan.at} (${data.last_scan.detail})` : 'Nothing read yet.',
      ' ',
      data.last_write ? `Last write: ${data.last_write.at} (${data.last_write.detail})` : 'Nothing written yet.'
    ].join('') }),
    (data.recent || []).slice(0, 8).map((entry, index) => jsx('div', { key: `${entry.at}-${index}`, style: { display: 'flex', gap: 8, padding: '2px 0', fontSize: 12 }, children: [
      jsx('span', { style: { ...text.mono, color: 'var(--ui-text-quaternary)' }, children: entry.at.replace('T', ' ') }),
      jsx(Badge, { variant: entry.ok ? 'muted' : 'destructive', children: entry.action }),
      jsx('span', { style: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: entry.detail || entry.target || '' })
    ] })),
    jsx('div', { style: { marginTop: 10 }, children: [
      jsx('div', { style: { ...text.faint, fontWeight: 600, marginBottom: 3 }, children: 'What it will not do' }),
      ...(data.limits || []).map(item => jsx('div', { key: item.title, style: { ...text.faint, padding: '2px 0' }, children: `${item.title} — ${item.detail}` }))
    ] })
  ] })
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
      jsx(ClassificationPanel, {
        ctx,
        ledger,
        onChanged: async () => {
          const [freshLedger, freshStatus] = await Promise.all([
            call(ctx, '/ledger'),
            call(ctx, '/status', { timeoutMs: 8000 })
          ])
          refresh(freshStatus, freshLedger)
        }
      }),
      jsx(JurisdictionPanel, { ctx, status }),
      jsx(AuditPanel, { ctx }),
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

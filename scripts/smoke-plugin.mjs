#!/usr/bin/env node
// Smoke test for the desktop half without the desktop app.
//
// The app loads plugin.js as a plain ESM file and hands it a real SDK. This
// script builds tiny stand-ins for `@hermes/plugin-sdk`, `react` and
// `react/jsx-runtime`, imports the plugin exactly as the app would, checks that
// it registers the page, nav row and palette command, then expands the page
// tree with fixture data and asserts the important text renders.
//
// What this proves: the file parses, imports only allowed modules, uses the
// hooks/components it imported, and renders without a ReferenceError on real
// shapes. What it cannot prove: pixels, theme contrast, or the app's own
// routing. Run `node scripts/smoke-plugin.mjs`.

import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginSource = join(here, '..', 'desktop', 'plugin.js')
const root = mkdtempSync(join(tmpdir(), 'receipt-ledger-smoke-'))

const write = (path, body) => {
  mkdirSync(dirname(join(root, path)), { recursive: true })
  writeFileSync(join(root, path), body)
}

write('package.json', JSON.stringify({ type: 'module' }))
write('node_modules/react/package.json', JSON.stringify({
  name: 'react', version: '0.0.0', type: 'module', main: 'index.js',
  exports: { '.': './index.js', './jsx-runtime': './jsx-runtime.js' }
}))
write('node_modules/react/index.js', `
export const useState = (initial) => [typeof initial === 'function' ? initial() : initial, () => {}]
export const useEffect = () => {}
export const useMemo = (factory) => factory()
export const useRef = (value) => ({ current: value })
export default { useState, useEffect, useMemo, useRef }
`)
write('node_modules/react/jsx-runtime.js', `
export const jsx = (type, props) => ({ type, props: props || {} })
export const jsxs = jsx
export const Fragment = 'Fragment'
`)

const ledger = {
  generated_at: '2026-11-09T15:40:00',
  source: { kind: 'securo', securo_url: 'http://securo.home', from: '2026-01-01', to: '2026-11-09', accounts: 2 },
  counts: { charges: 6, with_paper: 4, missing: 2, attached: 4, matched: 0, missing_dollars: 549, coverage_pct: 66.7 },
  transactions: [
    { id: 'securo:t1', date: '2026-08-27', vendor: 'Hetzner Online GmbH', description: 'Hetzner Online GmbH', amount: 63.44, status: 'attached', attachment_count: 1, category: 'Hosting', account: 'Business Checking', portal: '' },
    { id: 'securo:t2', date: '2026-08-19', vendor: 'Fireworks AI', description: 'Fireworks AI', amount: 50, status: 'missing', attachment_count: 0, category: 'AI', account: 'Business Checking', portal: 'fireworks.ai -> Settings -> Billing -> Invoices' }
  ],
  totals: {
    by_vendor: [{ key: 'Hetzner Online GmbH', count: 1, dollars: 63.44 }, { key: 'Fireworks AI', count: 1, dollars: 50 }],
    by_category: [{ key: 'Hosting', count: 1, dollars: 63.44 }],
    by_month: [], by_account: []
  },
  advice: [{ kind: 'capture-at-source', title: 'Attach paper as the charge lands', evidence: '2 of 6 charges have no attachment in Securo ($549.00).', fix: 'Forward receipts to Securo.' }],
  classification_summary: {
    business_dollars: 63.44, personal_dollars: 0, mixed_business_dollars: 0, mixed_personal_dollars: 0,
    reimbursable_to_owner_dollars: 0, unreviewed_dollars: 50, reconciles: true, counts: { charges: 2, business: 1, unreviewed: 1 }
  },
  review_queue: [{
    id: 'securo:t2', date: '2026-08-19', vendor: 'Fireworks AI', amount: 50, account: 'Business Checking', card: 'Business Visa',
    review: { needed: true, proposal: { decision: 'business', confidence: 'high', reasons: ['Business payment method and vendor precedent.'] } }
  }]
}

const status = {
  configured: true, mode: 'securo', credentials: true,
  settings: { securo_url: 'http://securo.home', securo_email: 'owner@example.com', securo_password_set: true, business_state: 'MN', business_entity: 'single_member_llc' },
  period: { from: '2026-01-01', to: '2026-11-09' }, source: ledger.source, counts: ledger.counts, has_advice: true
}

write('node_modules/@hermes/plugin-sdk/package.json', JSON.stringify({
  name: '@hermes/plugin-sdk', version: '0.0.0', type: 'module', main: 'index.js'
}))
write('node_modules/@hermes/plugin-sdk/index.js', `
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const PALETTE_AREA = 'palette'
// Fixtures are swapped through globalThis so one import can render several states.
globalThis.__RL_FIXTURES__ = { ledger: ${JSON.stringify(ledger)}, status: ${JSON.stringify(status)} }
export const useQuery = ({ queryKey }) => ({ data: (globalThis.__RL_FIXTURES__ || {})[queryKey[1]], error: null, isLoading: false })
export const useQueryClient = () => ({ setQueryData() {}, invalidateQueries() {} })
export const host = { notify() {}, navigate() {} }
const stub = (name) => (props) => ({ type: name, props })
export const Badge = stub('Badge'), Button = stub('Button'), Codicon = stub('Codicon'),
  Input = stub('Input'), SegmentedControl = stub('SegmentedControl'),
  Switch = stub('Switch')
export const EmptyState = ({ title, description }) => ({ type: 'div', props: { children: [title, description] } })
export const ErrorState = ({ title, description }) => ({ type: 'div', props: { children: [title, description] } })
`)

copyFileSync(pluginSource, join(root, 'plugin.js'))

const plugin = (await import(pathToFileURL(join(root, 'plugin.js')).href)).default

const registered = []
plugin.register({ register: (contribution) => registered.push(contribution) })

const areas = registered.map((entry) => entry.area)
const failures = []
const expect = (condition, message) => { if (!condition) failures.push(message) }

expect(plugin.id === 'receipt-ledger', 'plugin id is receipt-ledger')
expect(typeof plugin.name === 'string' && plugin.name.length > 0, 'plugin has a display name')
expect(areas.includes('routes'), 'registers a page route')
expect(areas.includes('sidebar.nav'), 'registers a sidebar nav row')
expect(areas.includes('palette'), 'registers a palette command')

const page = registered.find((entry) => entry.area === 'routes')
const tree = page.render()

// Expand function components down to their host nodes; collect all text.
const texts = []
const walk = (node, depth = 0) => {
  if (node == null || node === false || depth > 40) return
  if (Array.isArray(node)) { node.forEach((child) => walk(child, depth + 1)); return }
  if (typeof node === 'string' || typeof node === 'number') { texts.push(String(node)); return }
  if (typeof node !== 'object') return
  const { type, props } = node
  const next = typeof type === 'function' ? type(props || {}) : null
  if (next) { walk(next, depth + 1); return }
  walk(props && props.children, depth + 1)
}
walk(tree)

const rendered = texts.join(' | ')
expect(rendered.includes('Receipt ledger'), 'page renders its title')
expect(rendered.includes('Charges'), 'page renders the counts')
expect(rendered.includes('66.7') || rendered.includes('67%'), 'page renders coverage')
expect(rendered.includes('Hetzner Online GmbH'), 'page renders a charge row')
expect(rendered.includes('Fireworks AI'), 'page renders the missing charge')
expect(rendered.includes('fireworks.ai'), 'missing row names the portal')
expect(rendered.includes('Attach paper as the charge lands'), 'page renders advice')
expect(rendered.includes('Business or personal'), 'page renders the classification panel')
expect(rendered.includes('Fireworks AI'), 'review queue names the charge')
expect(rendered.includes('Where your business answers to'), 'page renders the jurisdiction panel')
expect(rendered.includes('What this plugin did'), 'page renders the audit panel')
expect(rendered.includes('http://securo.home'), 'page names the connected instance')
expect(!rendered.includes('owner@example.com'), 'page never renders the stored email as a secret')

// The setup form must render for a fresh install, and the page must survive an
// empty ledger without configuration.
globalThis.__RL_FIXTURES__ = {
  ledger: { generated_at: null, counts: {}, transactions: [], missing: [], totals: {}, advice: [] },
  status: { configured: false, mode: 'none', credentials: false, settings: {}, counts: {} }
}
texts.length = 0
walk(page.render())
const setup = texts.join(' | ')
expect(setup.includes('Instance URL'), 'fresh install renders the Securo setup form')
expect(setup.includes('Test connection'), 'fresh install offers a connection test')
expect(setup.includes('Not connected yet'), 'fresh install explains the empty state')
expect(!setup.includes('Hetzner'), 'fresh install does not invent charges')

if (failures.length) {
  console.error('FAILED')
  failures.forEach((message) => console.error('  - ' + message))
  process.exit(1)
}

console.log('ok — registered:', areas.join(', '))
console.log('ok — rendered', texts.length, 'text nodes')
console.log('ok — sample:', texts.filter((t) => t.length > 3).slice(0, 6).join(' / '))

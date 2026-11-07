# receipt-ledger

Find the paper. Show what's missing. Tell you how to stop chasing it next year.

A Hermes plugin that turns a folder of receipts plus a CSV of charges into a
ledger you can actually look at: every charge marked matched or missing, the
exact portal to go get each missing receipt, duplicates dropped, and a short
list of structural fixes worth more than any deduction.

## Why it exists

Gathering a year of receipts for an accountant takes hours of portal hunting and
eyeball matching. Almost none of that work is judgement — it is bookkeeping
archaeology. The plugin does the archaeology on demand instead of every February.

It also answers the question the paperwork can't: which of these charges are
making next year harder. Shared vendors billed to two different accounts, business
costs paid from personal cards, vendors that never send a receipt.

## What it does today

- Scans a receipts folder, hashes every file, drops byte-identical duplicates
- Parses a transactions CSV (loose header matching: date/vendor/amount/account/card)
- Matches receipts to charges by amount, vendor token and date proximity —
  best-first global assignment, so one receipt cannot be stolen by the wrong month
- Falls back to amount-only matching when the amount is unambiguous and the
  filename has no date
- Lists every missing charge with the vendor's actual billing portal
- Emits structural advice with the evidence attached
- Writes a dated zip for the accountant: receipts folder, receipt index CSV, and
  a still-missing CSV

## What it deliberately does not do

- No purchases, no payments, no seller contact
- No invented scam scores
- Nothing leaves the machine: no uploads, no third-party calls
- No claim that a matched file is a valid tax document. It matches paper to a
  charge; you and your accountant decide the rest.

## Install

The desktop half is one JavaScript file. The matching engine is a small Python
backend, because the renderer has no filesystem access.

```sh
./install.sh            # copies both halves, prints the enable step
./install.sh --enable   # same, and flips the plugin on in config.yaml
```

Then reload desktop plugins (Settings -> Plugins -> Reload, or restart the app).
A **Receipt Ledger** row appears in the sidebar nav.

Manual equivalent:

```sh
mkdir -p "$HERMES_HOME/desktop-plugins/receipt-ledger"
cp desktop/plugin.js "$HERMES_HOME/desktop-plugins/receipt-ledger/plugin.js"

mkdir -p "$HERMES_HOME/plugins/receipt-ledger"
cp -r dashboard plugin.yaml "$HERMES_HOME/plugins/receipt-ledger/"

# config.yaml
plugins:
  enabled:
    - receipt-ledger
```

## Use

1. Open **Receipt Ledger** in the sidebar.
2. Point it at a receipts folder and a transactions CSV.
3. Save and scan.
4. Read three things: the counts, the missing rows with their portals, the advice.

Sample data lives in `samples/` so the page is not empty on a fresh install:

```
samples/receipts/                 10 files, one deliberate duplicate
samples/transactions-2026.csv     16 charges
```

Expected first-run result: 16 charges, 9 matched, 7 missing, 1 duplicate dropped,
and advice that flags the Hover and OpenAI splits plus personal-card spend.

## Layout

```
desktop/plugin.js        desktop half: pane, sidebar row, palette command
dashboard/ledger_core.py matching engine (pure python, no web framework)
dashboard/plugin_api.py  http routes under /api/plugins/receipt-ledger
tests/test_ledger_core.py behavioural tests for the engine
samples/                 synthetic receipts, transactions, no real data
```

Backend routes:

| Route | What it does |
|---|---|
| `GET /status` | configured? last scan, counts |
| `GET /ledger` | last scan result |
| `POST /scan` | rescan now |
| `GET/POST /settings` | receipts dir, transactions csv |
| `POST /package` | build the accountant zip |

## Test

```sh
python3 -m pytest tests -q
```

Seven behavioural tests: duplicate bytes dropped, receipt matched to its own
month rather than a later one, amount-only fallback, portal carried into the gap
list, vendor split flagged, personal-card spend flagged, unmatched receipt never
counted as matched.

## Roadmap

- **Securo adapter.** [Securo](https://github.com/securo-finance/securo) is a
  self-hosted finance app with a REST API and an MCP server with scoped tokens.
  Point the plugin at a user's own instance and transactions stop coming from a
  CSV export. Deliberately not bundled: three containers, AGPL, and the plugin
  should work for someone who runs neither.
- Email receipt ingest: forward receipts to a mailbox, attach automatically.
- Per-entity workspaces (personal / LLC) instead of one flat ledger.

## Licence

MIT. Sample receipts are synthetic text stubs, not real documents.

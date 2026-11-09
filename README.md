# receipt-ledger

A Hermes desktop plugin that answers one question for every charge: **is the paper there?**

Charges come from your own [Securo](https://github.com/securo-finance/securo)
instance. Paper comes from Securo's attachments. A folder of old PDFs still
counts if that is all you have.

It ends the January ritual of opening twelve billing portals to find out which
receipts you never saved — and it names the structural fix that stops the ritual
from coming back.

## Why it exists

Collecting a year of receipts is bookkeeping archaeology, not judgement. Almost
all of the work is: list the charges, check which ones have a document, go get
the rest. Software can do that in a second; people do it once a year, badly.

Securo already stores the answer. A charge with an attachment has paper; a charge
without one does not. No filename guessing, no amount matching, no "probably
this one".

## What it does

- Reads charges and their attachment counts straight from Securo's API
- Marks every charge **paper** (attached in Securo), **file** (matched from a
  folder), or **missing**
- Names the exact portal for each missing charge (`console.hetzner.cloud ->
  Billing -> Invoices`, and so on)
- Rolls spend up by vendor, category, month and account
- Gives structural advice with the evidence attached: vendors that never send a
  receipt, vendors split across accounts, business costs on personal cards
- Writes one zip for the accountant: receipts, index, gap list, totals
- Works without Securo: a transactions CSV plus a folder of receipts

## Install

```sh
./install.sh            # copies the desktop half and the backend half
./install.sh --enable   # same, and enables the plugin in config.yaml
```

Then reload desktop plugins (Settings → Plugins, or restart the app). A
**Receipt Ledger** row appears in the sidebar; `Receipt Ledger: rescan` is in the
command palette.

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

The desktop half renders the page. The backend half (`dashboard/`) talks to
Securo, reads the filesystem and writes the package. Both are required: with the
backend missing, the page says so instead of pretending.

## Connect Securo

Open **Receipt Ledger → Setup** and fill in:

| Field | What to put there |
|---|---|
| Instance URL | The address you open Securo at, e.g. `http://securo.home` or `https://finance.example.com` |
| Email | The email you sign in with |
| Password | The same password. Used to obtain a bearer token each session; stored `0600` in the plugin's `data/settings.json` |
| Access token (optional) | A long-lived `sct_…` token. Preferred when your instance mints one: scoped, revocable, never expires mid-scan |
| Charges from / to | The window to read. Blank means this calendar year |

**Test connection** proves the URL, credential and workspace before anything is
saved: it returns your email, workspace name and account count. Nothing is
written until you press **Save and scan**.

Securo access tokens carry scopes. This plugin only reads, so it needs
`transactions:read` and `accounts:read` — nothing else. Attachments live under
`/api/transactions/…`, so `transactions:read` covers them too.

If your instance has no token UI yet, use email + password: the client logs in
for a bearer token and re-logs in once when that token expires.

## Without Securo

Switch the setup card to **CSV + folder** and give it a transactions export plus
a receipts folder. Header names are matched loosely (`date`/`posted`,
`vendor`/`description`/`payee`/`merchant`, `amount`/`debit`/`charge`, `account`,
`card`, `category`). Matching uses amount, vendor token and date proximity,
best-first, so one receipt cannot be stolen by the wrong month. Byte-identical
duplicates are dropped.

`samples/` ships sixteen synthetic charges and ten receipt files, one a
deliberate duplicate, so the page is not empty on a fresh install.

## What the accountant gets

`Accountant package` builds a dated folder and zip:

```
receipts/            one file per charge that has paper, named date vendor amount
receipt-index.csv    date, vendor, amount, category, account, source, paper, file
still-missing.csv    charges with no paper, and where to get it
totals.csv           spend by vendor, category, month and account
README.txt           counts, sources, date window
```

Securo attachments are downloaded on demand and cached under
`data/attachments/`, so rebuilding the same month costs no extra requests.

## Routes

All under `/api/plugins/receipt-ledger` — the plugin's own namespace, reachable
only through the app.

| Route | What it does |
|---|---|
| `GET /status` | configured?, which mode, last scan, counts, public settings |
| `GET /ledger` | last scan result (renders instantly on pane open) |
| `POST /scan` | rescan now |
| `GET/POST /settings` | read/save config; secrets are never echoed back |
| `POST /securo/test` | prove a URL + credential without saving it |
| `POST /package` | download receipts, write the zip |

## Test

```sh
python3 -m pytest tests -q      # 34 tests: engine, client over real HTTP, routes
node scripts/smoke-plugin.mjs   # desktop half: registers, renders both states
```

The client tests run the real `SecuroClient` against a stub HTTP server that
speaks Securo's shapes (paginated `/api/transactions`, attachment metadata,
`Content-Disposition` downloads), so pagination, auth headers, 401 handling and
error text are exercised over the wire rather than mocked.

What those tests do not prove: the app's pixels and theme contrast. The desktop
smoke test builds stub `@hermes/plugin-sdk`, `react` and `react/jsx-runtime`
modules, imports `plugin.js` the way the app does, and expands the page tree to
assert the real component bodies run and the expected text renders — configured
and fresh-install.

## Privacy

- The only network call is to the Securo URL you set.
- Nothing is uploaded, sold, or sent anywhere else.
- The renderer never sees your password or token; settings come back as
  `securo_password_set: true`.
- `settings.json` is written `0600`.
- Receipts stay where they are: your Securo instance, plus a local cache only
  when you build a package.

## Limits

- Matching paper to a charge is not a tax determination. You and your accountant
  decide what is deductible.
- The portal list is data (`VENDOR_PORTALS` in `dashboard/ledger_core.py`), not
  magic. Vendors it does not know show `no portal known`.
- There is no receipt text extraction: an attachment counts as paper whether it
  is an invoice, a screenshot or a photo of a napkin.
- Tested against Securo 0.1.x. An upstream endpoint change surfaces as a named
  error (`… does not exist on this Securo instance`), not a silent empty ledger.

## Roadmap

- Email-in capture: forward a receipt, it lands on the charge.
- Per-entity workspaces (personal / LLC) instead of one flat ledger.
- Category suggestions from last year's choices.
- Statement PDF fallback for vendors that only publish a monthly statement.

## Licence

MIT. Sample receipts are synthetic text stubs, not real documents.

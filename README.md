# receipt-ledger

A Hermes plugin that gets your business's paper, entity and filing facts into one
auditable place, so you — and your assistant — can answer them without opening
twelve portals.

Charges come from your own [Securo](https://github.com/securo-finance/securo)
instance. Paper comes from Securo's attachments; a folder of old PDFs still
counts if that is all you have. Every charge can carry a business-or-personal
decision you made, with the reason. Every jurisdiction fact ships with the
government page it came from and the day it was checked. And the assistant can
ask the plugin questions directly, so "what's missing paper over fifty bucks"
is a sentence, not a spreadsheet.

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
- Writes one zip for the accountant: receipts, index, gap list, totals, and the
  classification trail
- Records business-or-personal per charge as a standing rule with who decided,
  when, and why — proposed with evidence, never assumed
- Ships a jurisdiction bundle: federal plus all fifty states and DC, each with
  source links and a confidence tier, and quoted obligations where someone read
  the rule
- Attaches a downloaded receipt to the right Securo charge in two confirmed steps
- Exposes four read-only agent tools so Hermes answers from your ledger
- Journals every read and write so "it only reads" is something you can check
- Works without Securo: a transactions CSV plus a folder of receipts

## Ask it instead of opening it

The plugin registers four read-only tools. In a Hermes chat:

- *what did I spend on hosting this year* → `receipt_ledger_spend`
- *which receipts are missing over fifty bucks* → `receipt_ledger_missing_paper`
- *what's my receipt coverage for Q3* → `receipt_ledger_coverage`
- *is the ledger connected* → `receipt_ledger_status`

They read the last saved scan and never contact Securo, so an answer costs
nothing and cannot trigger a sync.

## Business or personal, on the record

A charge is `business`, `personal`, `mixed` with a business percentage and the
basis for it, `owner_reimbursement` when the LLC owes you, or
`owner_contribution` for money you put in. The engine proposes with reasons —
business card plus the same vendor all year suggests business — and you confirm.
A confirmed decision becomes a rule keyed to vendor, account, card or category,
so the next ninety charges do not ask again.

With no rules, everything is unreviewed and business spending is **zero**. That
is deliberate: a default is a claim you did not make.

## Where your business answers to

`jurisdictions/` holds one JSON record per jurisdiction: the revenue department,
the business registry, the sales-tax source and the withholding source, each
with a checked date and a status. Three tiers:

- **verified** — a person opened the government page, read the rule and copied the sentence
- **extracted** — a script copied the sentence from the live page and re-checked that it still appears there; nobody has read the rule yet
- **seeded** — official sources answered, but no rule has been read
- **unknown** — the hosts could not be reached at all

`scripts/extract_obligations.py` proposes candidate sentences and
`scripts/promote_candidates.py` promotes them — refusing any quote that is not a
literal substring of the page it cites, so a hallucinated deadline cannot reach
the bundle. `_candidates/` is the staging area, and it is not shipped as fact.

Minnesota's annual renewal and Delaware's annual entity tax are quoted as worked
examples, and the federal record carries ten quoted obligations. Every other
state is a seeded starting point: your own agent can fill in its rules, and the
record will say who filled it in and when.

## What it read, what it wrote

`data/audit.jsonl` keeps the newest 500 entries: every scan, package build,
attachment upload and settings change, with counts and outcomes. The pane shows
them, and the same page states the limits — not tax advice, an attachment is
paper rather than proof, the portal list is a table, classification is proposed
rather than assumed, jurisdiction facts are dated, and reads and writes are
visible.

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
| `POST /securo/attach/preview` | what an upload would do: target, file, existing attachments |
| `POST /securo/attach` | the one write: attach a receipt, only with `confirm: true` |
| `GET /classification` | per-entity summary, review queue, standing rules |
| `POST /classification/rule` | record your decision as a standing rule |
| `GET /jurisdictions` | the bundle: counts, catalog, and one record by id |
| `GET /audit` | what ran, what it wrote, and the plugin's stated limits |
| `POST /audit/clear` | drop the journal |
| `POST /package` | download receipts, write the zip |

## Test

```sh
python3 -m pytest tests -q      # 182 tests: engine, client over real HTTP, routes,
                               # classification, audit, jurisdictions, upload
node scripts/smoke-plugin.mjs   # desktop half: registers, renders both states
python3 scripts/validate_jurisdictions.py      # the data contract
python3 scripts/verify_jurisdiction_sources.py # every URL, with statuses
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
- Jurisdiction records are dated claims, not a subscription. Two states could not
  be reached from the machine that built the bundle, so they ship as unknown and
  say so; 47 carry sources but no read rule. Re-run the source checker before
  relying on a page that matters.
- A classification rule matches text, so `vendor_contains: "AI"` will match more
  than you meant. Prefer the vendor's distinctive word, and check the first scan
  after saving a rule.
- Attachment upload adds a file; it never replaces or deletes one, and it does
  not retry after a failure that might have succeeded. Check the transaction in
  Securo before trying again.

## Layout

```
desktop/plugin.js               desktop half: pane, sidebar row, palette command
__init__.py                     agent tools: register(ctx), four read-only tools
dashboard/ledger_core.py        matching engine, ledger build, totals, advice
dashboard/securo_client.py      read-only Securo HTTP client (stdlib)
dashboard/securo_write.py       the single confirmed write path
dashboard/classify.py           decision store, proposals, review queue, totals
dashboard/audit.py              bounded journal of reads and writes + limits
dashboard/jurisdiction_data.py  the bundle as data, next-due parsing only
dashboard/agent_tools.py        the queries behind the tools
dashboard/plugin_api.py         the routes, all under the plugin namespace
jurisdictions/                  52 records: federal, 50 states, DC
scripts/                        validator, source checker, desktop smoke test
tests/                          182 tests, no network except the local stub
```

## Roadmap

- Email-in capture: forward a receipt, it lands on the charge.
- Per-entity workspaces (personal / LLC) instead of one flat ledger.
- Category suggestions from last year's choices.
- Statement PDF fallback for vendors that only publish a monthly statement.

## Licence

MIT. Sample receipts are synthetic text stubs, not real documents.

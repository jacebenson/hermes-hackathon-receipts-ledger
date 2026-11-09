# US jurisdiction source bundle

This directory holds 52 versioned JSON records: US federal, the 50 states, and the District of Columbia. Rules are data. The validator and source checker contain no tax rates, filing thresholds, or deadline calculations. There are no plugin engine or UI changes.

This is a source directory with quoted obligations, not a complete compliance checklist. 42 records carry one extracted obligation each — normally the annual report, renewal or franchise filing every registered entity faces — and three carry a rule a person read. An empty `obligations` array means no rule was found here. It does not mean a business has no obligations.

## Confidence is evidence, not coverage

- `verified`: a person opened and read the cited rule and copied the quote. At least one obligation exists, and every listed source has status `ok`. Verification applies to the listed obligations only, not every tax administered by that jurisdiction.
- `extracted`: a script copied the sentence from the live page and re-checked that it still appears there (`scripts/promote_candidates.py` refuses any quote that is not a literal substring of the page it cites). Nobody has read the rule yet. The page a quote came from must have answered; provenance links that a host refuses may stay unconfirmed.
- `seeded`: every source URL belongs to an official directory and answered — either with a page (`ok`) or with a refusal (`blocked`). No obligation was read and verified, so obligations stay empty.
- `unknown`: at least one source could not be reached at all. Obligations stay empty. A blocked request is a finding, not permission to guess the rule.

Source `status` is separate from rule verification. `ok` means the HTTPS request returned a successful response. `blocked` means the host answered with an HTTP error (typically 403 from a bot filter) — the official domain is right, but the page could not be read automatically; open it in a browser. `unconfirmed` covers DNS failures, timeouts, connection resets, redirect loops and redirects onto plain HTTP. `unchecked` means no check has been recorded. An HTTP 200 response does not prove that a page contains a particular rule or that a JavaScript application rendered successfully.

**A record without a quote is not a fact.** Do not add an obligation unless its `source_url`, verbatim `quote`, `verified_on`, and `due_rule` support it. Copy quotations without rewriting their wording. Separate quoted excerpts with blank lines; PDF line wrapping and HTML whitespace may be collapsed. Never infer a date, threshold, rate, or form number.

## Provenance and scope

Every state's revenue URL comes from the [IRS state government websites directory](https://www.irs.gov/businesses/small-businesses-self-employed/state-government-websites), through its state-specific IRS page. Each record retains that page in `sources.directory`. Old HTTP directory links were explicitly tested over HTTPS. Registry links come from that directory's Secretary of State, corporations agency, or business portal. Michigan uses LARA, not its Secretary of State; DC uses the Department of Licensing and Consumer Protection, found through its own agency directory.

Additional topic links were followed from the official revenue sites or their linked government tax service centers. Some `withholding` entries remain explicitly labelled general research entry points. New Hampshire's sales-tax topic links to its general-information FAQ. Do not interpret a general portal as a rate file, a taxability determination, or proof that withholding or sales tax exists. Hawaii's topic is general excise tax and New Mexico's topic is gross receipts tax, not a claim that those taxes are ordinary sales taxes. The bundle does not determine local taxes or nexus.

Exactly two states have quoted obligations:

- Minnesota: annual LLC renewal by December 31, from the SOS renewal PDF, page 1. The PDF instructions establish the annual frequency.
- Delaware: annual LLC/LP/GP entity tax due on or before June 1 for the prior year. Delaware was chosen because the Division of Corporations publishes the entity coverage, deadline, and payment requirement together on a readable page. The quoted amount is what that page publishes, not a determination for earlier tax years.

Federal records cover all four individual estimated payment dates, Form 1099-NEC issuance and the threshold change, Forms W-2 and 941, Form 1120-S after an accepted S corporation election, and Schedule C with self-employment tax. The IRS says the 1099 threshold is $600 before 2026, $2,000 in 2026, and inflation-adjusted after 2026. Form 2553 makes the election; Form 1120-S is the return. General dates do not account for disaster relief or all special filing circumstances. Employer tax deposit dates differ from return filing dates.

## Add or fix a state

1. Keep one file per jurisdiction, named `us-<postal abbreviation>.json`, with a matching `id`. The federal record is `us-federal.json`. Use `schema_version: 1` and ISO `YYYY-MM-DD` dates.
2. Start from the IRS state-specific directory and follow links to the state's own revenue and business registry sites. Use only HTTPS and first-party government or official state sites. Never substitute a commercial tax summary. Preserve directory provenance, and note an obsolete or blocked link.
3. Check each URL. Keep unconfirmed records `unknown`, sources `unconfirmed`, and obligations empty. A `blocked` source still means the domain answered, so the record can be `seeded`. A successfully checked seed has `seeded` confidence and no obligations. Include a specific sales-tax rate or taxability page when located; omit `sales_tax` only when its absence is genuinely established, not because research failed. A clearly labelled general research entry point is incomplete topic coverage, not verified tax information.
4. To promote a record, open the rule, read its applicability and exceptions, and copy the supporting sentence or excerpts. Add the obligation's `id`, `title`, `applies_when`, `form`, `due_rule`, `frequency`, `source_url`, `quote`, and `verified_on`. Do not extrapolate from another state. Set `verified` only if every source is `ok`.
5. Explain limitations in `notes`, update `updated_on`, and run the validator and tests. Human review must check authority and quote fidelity; the offline validator cannot establish either.

## Commands

From the repository root, with Python 3.11 or later:

```sh
.venv/bin/python scripts/validate_jurisdictions.py --json
.venv/bin/python scripts/verify_jurisdiction_sources.py
.venv/bin/python -m pytest tests -q
```

Both scripts use only the Python standard library. The source checker is read-only unless `--write` is present. It checks each distinct source or obligation URL once, ignoring fragments, with up to 12 workers, an 8-second per-request timeout, bounded HTTPS redirects, and a browser-style User-Agent. It performs GET and reads only one byte rather than downloading whole pages. `--timeout` accepts a value greater than zero and at most 30 seconds. `--directory` selects a separate bundle for either script.

```sh
.venv/bin/python scripts/verify_jurisdiction_sources.py --write
.venv/bin/python scripts/validate_jurisdictions.py --json
```

`--write` changes only source `status` and `checked_on`, using the local check date. It never promotes confidence, changes quotes, removes obligations, or changes `verified_on`. If a verified source becomes unreachable, validation deliberately fails until a person resolves the source or reviews the record. Do not silently discard quoted evidence during a network outage. A failed seeded source likewise requires review of confidence.

Both commands exit nonzero on findings. The network summary separates URLs that answered from URLs that did not, and reports blocked separately from unconfirmed. A 403 is recorded as `blocked`: the host is real, the page was not readable by a script. Network results can vary between runs and environments; an earlier cached page or extraction-service success does not erase a direct-check failure.

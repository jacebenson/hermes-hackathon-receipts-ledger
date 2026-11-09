"""Auditable charge decisions. Suggestions are never decisions or tax advice.

Rules are human-authored standing decisions. Only unambiguous matching rules
populate decision fields; inferred treatments remain proposals pending review.
Amounts are allocated per row in cents, with the residual assigned to personal.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

DATA_DIR = Path(os.environ.get("RECEIPT_LEDGER_DATA", Path(__file__).resolve().parents[1] / "data"))
DECISIONS = {"business", "personal", "mixed", "owner_contribution", "owner_reimbursement"}
MATCH_KEYS = {"vendor_contains", "account_contains", "card_contains", "category_in"}
EXPORT_FIELDS = ["date", "vendor", "amount", "account", "card", "decision", "business_pct", "basis", "decided_by", "decided_at", "rule_id", "note"]


def _timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def validate_rule(rule):
    if not isinstance(rule, dict) or rule.get("decision") not in DECISIONS:
        raise ValueError("Invalid classification decision")
    for key in ("id", "created_by", "created_at"):
        if not isinstance(rule.get(key), str) or not rule[key].strip():
            raise ValueError(f"Rule requires {key}")
    _timestamp(rule["created_at"])
    match = rule.get("match")
    if not isinstance(match, dict) or not match or not set(match) <= MATCH_KEYS:
        raise ValueError("Rule requires supported, nonempty match keys")
    for key, value in match.items():
        values = value if key == "category_in" else [value]
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError("Match values must be nonempty strings")
    pct = rule.get("business_pct")
    if pct is not None and (type(pct) is not int or not 1 <= pct <= 99):
        raise ValueError("business_pct must be an integer from 1 to 99")
    if rule["decision"] == "mixed" and pct is None:
        raise ValueError("Mixed decisions require business_pct")
    for key in ("basis", "note"):
        if key in rule and not isinstance(rule[key], str):
            raise ValueError(f"{key} must be a string")
    return rule


def load_rules(path=None):
    """Missing, corrupt or invalid stores fail closed to no standing decisions."""
    try:
        rules = json.loads(Path(path or DATA_DIR / "classification.json").read_text())
        if not isinstance(rules, list):
            return []
        for rule in rules:
            validate_rule(rule)
        if len({r["id"] for r in rules}) != len(rules):
            return []
        return rules
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def save_rule(rule=None, *, path=None, **fields):
    """Explicit human action only. Atomically append or replace a standing rule.

    An existing corrupt file is not overwritten, preserving evidence for repair.
    Concurrent writers must serialize calls; this is a single-user JSON store.
    """
    target = Path(path or DATA_DIR / "classification.json")
    incoming = dict(rule or fields)
    incoming.setdefault("id", uuid4().hex)
    incoming.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    validate_rule(incoming)
    rules = load_rules(target)
    if target.exists() and not rules:
        try:
            if json.loads(target.read_text()) != []:
                raise ValueError("Repair invalid classification store before saving")
        except (ValueError, OSError) as exc:
            raise ValueError("Repair invalid classification store before saving") from exc
    rules = [r for r in rules if r["id"] != incoming["id"]] + [incoming]
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            name = handle.name
            json.dump(rules, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return incoming


def _matches(row, match):
    for key, value in match.items():
        if key == "category_in":
            if str(row.get("category", "")).casefold() not in {v.casefold() for v in value}:
                return False
        elif value.casefold() not in str(row.get(key.removesuffix("_contains"), "")).casefold():
            return False
    return True


def _winner(row, rules):
    candidates = []
    for rule in rules:
        try:
            validate_rule(rule)
        except (ValueError, TypeError, AttributeError):
            continue
        if _matches(row, rule["match"]):
            candidates.append(rule)
    return max(candidates, key=lambda r: (len(r["match"]), _timestamp(r["created_at"]), r["id"]), default=None)


def _funding(row):
    # Only explicit labels are signals. Unknown cards are not personal cards.
    words = set(re.findall(r"[a-z]+", f"{row.get('account', '')} {row.get('card', '')}".casefold()))
    result = set()
    if words & {"business", "biz", "llc"}:
        result.add("business")
    if "personal" in words:
        result.add("personal")
    return result


def propose(row, rules=None, rows=None, *, complete_year=None):
    """Propose with evidence. complete_year requires caller-verified full-year coverage.

    History cannot prove all-year exclusivity from a partial scan. It never makes
    a standing decision, even when coverage is explicitly supplied.
    """
    rules = load_rules() if rules is None else rules
    history = list(rows) if rows is not None else [row]
    rule = _winner(row, rules)
    reasons = []
    decision = rule["decision"] if rule else None
    confidence = "high" if rule else "low"
    if rule:
        reasons.append(f"Matches human rule {rule['id']} by {rule['created_by']}: {rule.get('note') or rule.get('basis') or rule['match']}")
    funding = _funding(row)
    # A vendor precedent may suggest treatment on another account, not authorize it.
    precedent = _winner({**row, "account": "", "card": ""}, rules)
    if precedent is None:
        vendor_rules = [r for r in rules if r.get("match", {}).get("vendor_contains") and
                        _matches(row, {"vendor_contains": r["match"]["vendor_contains"]})]
        precedent = _winner(row, [{**r, "match": {"vendor_contains": r["match"]["vendor_contains"]}} for r in vendor_rules])
    vendor_decision = (rule or precedent or {}).get("decision")
    # Account-specific human rules can explicitly confirm reimbursement/draw treatment.
    explicit = rule and any(k in rule["match"] for k in ("account_contains", "card_contains"))
    if vendor_decision == "business" and "personal" in funding:
        decision, confidence = "owner_reimbursement", "low"
        reasons.append("Business-vendor cost on a personal payment method; confirm the LLC owes the owner.")
    elif vendor_decision == "personal" and "business" in funding:
        decision = "personal"
        reasons.append("Personal-vendor spend on a business payment method; confirm booking as an owner draw/distribution.")
        if not explicit:
            confidence = "low"
    elif not rule and vendor_decision == "business" and funding == {"business"}:
        decision, confidence = "business", "high"
        reasons.append(f"Business payment method and vendor precedent {precedent['id']}; not a matching rule.")
    vendor = str(row.get("vendor", "")).strip().casefold()
    year = str(row.get("date", ""))[:4]
    peers = [r for r in history if str(r.get("vendor", "")).strip().casefold() == vendor
             and str(r.get("date", ""))[:4] == year and r.get("kind", "charge") == "charge"]
    signals = set().union(*(_funding(r) for r in peers)) if peers else funding
    if {"personal", "business"} <= signals:
        # A standing human rule is the decision; conflicting history is a reason
        # to look again, not a reason to void what the human already ruled. With
        # no rule, the conflict genuinely needs a decision.
        if rule is None:
            confidence = "low"
        reasons.append("Conflicting signals: this vendor appears on both personal and business payment methods.")
    if not rule and decision is None and complete_year is not None and str(complete_year) == year and peers and all(_funding(r) == {"business"} for r in peers):
        decision, confidence = "business", "high"
        reasons.append(f"Vendor seen only on business payment methods in caller-verified complete year {year}.")
    if rule and rule["decision"] == "mixed" and not rule.get("basis", "").strip():
        confidence = "low"
        reasons.append("Mixed allocation needs a human-provided basis.")
    if rule and rule["decision"] == "owner_contribution" and row.get("kind", "charge") != "credit":
        confidence = "low"
        reasons.append("Owner contribution is money into the business, but this row is a charge; confirm transaction direction.")
    if not reasons:
        reasons.append("No matching human rule or sufficient evidence; leave unreviewed.")
    return {"decision": decision, "confidence": confidence, "reasons": reasons}


def classify(rows, rules):
    """Return copies, never promoting a suggestion into a human decision."""
    rows = list(rows)
    result = []
    for row in rows:
        rule = _winner(row, rules)
        proposal = propose(row, rules, rows)
        accepted = bool(rule and proposal["confidence"] == "high" and proposal["decision"] == rule["decision"])
        classification = {"decision": rule["decision"] if accepted else None,
                          "business_pct": rule.get("business_pct") if accepted else None,
                          "basis": rule.get("basis") if accepted else None,
                          "decided_by": rule["created_by"] if accepted else None,
                          "decided_at": rule["created_at"] if accepted else None,
                          "source": "rule" if accepted else ("proposed" if proposal["decision"] else "unreviewed"),
                          "rule_id": rule["id"] if rule else None,
                          "note": rule.get("note", "") if accepted else ""}
        result.append({**row, "classification": classification,
                       "review": {"needed": not accepted, "proposal": proposal}})
    return result


def review_queue(rows, rules):
    return [r for r in classify(rows, rules) if r.get("kind", "charge") == "charge" and r["review"]["needed"]]


def _cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _summary(rows):
    keys = ("business", "personal", "mixed_business", "mixed_personal", "reimbursable_to_owner", "owner_contribution", "unreviewed")
    cents = dict.fromkeys(keys, 0)
    counts = dict.fromkeys((*DECISIONS, "unreviewed"), 0)
    distributions = total = charges = 0
    for row in rows:
        if row.get("kind", "charge") != "charge":
            continue
        amount = _cents(row["amount"])
        total += amount
        charges += 1
        c = row.get("classification") or {}
        decision = c.get("decision") if c.get("source") == "rule" and c.get("decided_by") and c.get("decided_at") else None
        if decision not in DECISIONS or (decision == "mixed" and (type(c.get("business_pct")) is not int or not 1 <= c["business_pct"] <= 99 or not (c.get("basis") or "").strip())):
            decision = "unreviewed"
        counts[decision] += 1
        if decision == "mixed":
            business = int((Decimal(amount) * c["business_pct"] / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            cents["mixed_business"] += business
            cents["mixed_personal"] += amount - business
        else:
            cents[{"owner_reimbursement": "reimbursable_to_owner"}.get(decision, decision)] += amount
        if decision == "personal" and _funding(row) == {"business"}:
            distributions += amount
    # Distributions are a subset of personal, not an extra reconciliation bucket.
    return {**{key + "_dollars": value / 100 for key, value in cents.items()},
            "total_charge_dollars": total / 100, "distributions_dollars": distributions / 100,
            "cents": {**cents, "total_charges": total, "distributions": distributions},
            "counts": {**counts, "charges": charges}, "reconciles": sum(cents.values()) == total}


def summary(rows):
    """Disjoint cent buckets reconcile charges; credits never masquerade as income.

    Entity groups use only explicit entity labels, never guessed LLC ownership.
    Owner contributions are a separate bucket, not business income or expenses.
    """
    rows = list(rows)
    entities = sorted({str(r.get("entity") or "Unassigned") for r in rows})
    contributions = [r for r in rows if r.get("kind") == "credit"
                     and (r.get("classification") or {}).get("decision") == "owner_contribution"
                     and (r.get("classification") or {}).get("source") == "rule"
                     and (r.get("classification") or {}).get("decided_by")
                     and (r.get("classification") or {}).get("decided_at")]
    return {**_summary(rows),
            "owner_contribution_inflow_dollars": sum(_cents(r["amount"]) for r in contributions) / 100,
            "owner_contribution_inflow_count": len(contributions),
            "by_entity": {entity: _summary([r for r in rows if str(r.get("entity") or "Unassigned") == entity]) for entity in entities}}


def export_row(row):
    """Accountant CSV record. Unconfirmed proposals stay out of decision columns."""
    c = row.get("classification") or {}
    confirmed = c.get("source") == "rule" and c.get("decision") in DECISIONS and c.get("decided_by") and c.get("decided_at")
    result = {key: row.get(key, "") for key in ("date", "vendor", "amount", "account", "card")}
    result.update({key: c.get(key) if confirmed else None for key in EXPORT_FIELDS[5:-1]})
    result["note"] = c.get("note", "") if confirmed else "Unreviewed: " + "; ".join((row.get("review") or {}).get("proposal", {}).get("reasons", []))
    return result

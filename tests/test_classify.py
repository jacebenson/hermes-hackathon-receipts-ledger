"""Public classification seams, synthetic data only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
import classify as engine
import pytest
import ledger_core as core
from decimal import Decimal


def row(**changes):
    return {"id": "a", "date": "2026-01-02", "vendor": "Printer", "amount": 100,
            "kind": "charge", "account": "Business checking", "card": "Business Visa", **changes}


def rule(**changes):
    return {"id": "r1", "match": {"vendor_contains": "Printer"}, "decision": "business",
            "created_by": "owner", "created_at": "2026-01-01T00:00:00+00:00", "note": "Office equipment", **changes}


def test_business_rule_is_an_audited_decision():
    result = engine.classify([row()], [rule()])[0]
    assert result["classification"]["decision"] == "business"
    assert result["classification"]["source"] == "rule"
    assert result["classification"]["decided_by"] == "owner"
    assert engine.review_queue([row()], [rule()]) == []


def test_reimbursement_is_only_a_proposal_until_explicit_rule():
    r = row(account="Personal checking", card="Personal Visa")
    result = engine.classify([r], [rule()])[0]
    assert result["classification"]["decision"] is None
    assert result["review"]["proposal"]["decision"] == "owner_reimbursement"
    assert engine.review_queue([r], [rule()])
    confirmed = rule(id="confirmed", match={"vendor_contains": "Printer", "card_contains": "Personal"}, decision="owner_reimbursement")
    assert engine.classify([r], [rule(), confirmed])[0]["classification"]["decision"] == "owner_reimbursement"
    assert engine.summary([result])["business_dollars"] == 0
    assert engine.export_row(result)["decision"] is None


def test_personal_vendor_on_business_card_requires_draw_review():
    result = engine.classify([row()], [rule(decision="personal")])[0]
    assert result["classification"]["decision"] is None
    assert "owner draw" in " ".join(result["review"]["proposal"]["reasons"])
    confirmed = rule(decision="personal", match={"vendor_contains": "Printer", "card_contains": "Business"})
    assert engine.summary(engine.classify([row()], [confirmed]))["distributions_dollars"] == 100


def test_mixed_allocation_and_entity_summaries_reconcile_at_cent_boundary():
    mixed = rule(decision="mixed", business_pct=26, basis="26% of 180 sq ft office")
    result = engine.classify([row(amount="10.01", entity="Example LLC")], [mixed])
    totals = engine.summary(result)
    assert totals["mixed_business_dollars"] == 2.60
    assert totals["mixed_personal_dollars"] == 7.41
    assert totals["cents"]["mixed_business"] + totals["cents"]["mixed_personal"] == 1001
    assert totals["by_entity"]["Example LLC"]["total_charge_dollars"] == 10.01
    assert totals["reconciles"]
    assert engine.review_queue([row()], [rule(decision="mixed", business_pct=26)])


def test_no_rules_never_defaults_to_business(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    samples = Path(__file__).resolve().parents[1] / "samples"
    ledger = core.build_ledger({"transactions_csv": str(samples / "transactions-2026.csv")})
    assert ledger["counts"]["charges"] == 16
    assert len(ledger["review_queue"]) == 16
    assert all(r["classification"]["decision"] is None for r in ledger["transactions"])
    assert ledger["classification_summary"]["business_dollars"] == 0
    assert ledger["classification_summary"]["reconciles"]


def test_precedence_specificity_then_actual_timestamp_then_id():
    specific = rule(id="specific", match={"vendor_contains": "printer", "category_in": ["Office"]}, decision="mixed", business_pct=50, basis="half office use")
    newer = rule(id="new", created_at="2027-01-01T00:00:00Z")
    result = engine.classify([row(category="Office")], [specific, newer])[0]
    assert result["classification"]["rule_id"] == "specific"
    assert engine.classify([row()], [rule(), newer])[0]["classification"]["rule_id"] == "new"


@pytest.mark.parametrize("pct", [0, 100, -1, 1.5, True, "26"])
def test_invalid_percent_rejected(tmp_path, pct):
    with pytest.raises(ValueError):
        engine.save_rule(rule(decision="mixed", business_pct=pct), path=tmp_path / "rules.json")


def test_rule_store_missing_corrupt_invalid_and_atomic_roundtrip(tmp_path):
    path = tmp_path / "classification.json"
    assert engine.load_rules(path) == []
    path.write_text("{broken")
    assert engine.load_rules(path) == []
    with pytest.raises(ValueError):
        engine.save_rule(rule(), path=path)
    path.write_text("[]")
    saved = engine.save_rule(rule(), path=path)
    assert engine.load_rules(path) == [saved]
    with pytest.raises(ValueError):
        engine.save_rule(rule(decision="deductible"), path=path)
    path.write_text('[{"decision": "business"}]')
    assert engine.load_rules(path) == []


def test_conflicting_vendor_signals_go_to_review_not_summary():
    rows = [row(), row(id="b", account="Personal checking", card="Personal Visa")]
    classified = engine.classify(rows, [rule()])
    business_row, personal_row = classified[0], classified[1]

    # The standing rule still decides the charge paid from the business account;
    # the vendor's mixed history is noted, not used to void the human decision.
    assert business_row["classification"]["source"] == "rule"
    assert business_row["classification"]["decision"] == "business"
    assert any("Conflicting signals" in reason for reason in business_row["review"]["proposal"]["reasons"])

    # A personal card paying a business-vendor charge is a different treatment,
    # and that one does need the owner's confirmation.
    assert personal_row["classification"]["decision"] is None
    assert personal_row["review"]["needed"] is True

    totals = engine.summary(classified)
    assert totals["business_dollars"] == 100
    assert totals["unreviewed_dollars"] == 100


def test_unknown_funding_is_not_personal_and_history_requires_full_coverage():
    unknown = row(account="Checking", card="1234")
    assert engine.propose(unknown, [rule()])["decision"] == "business"
    assert engine.propose(row(), [], [row()])["decision"] is None
    assert engine.propose(row(), [], [row()], complete_year=2026)["confidence"] == "high"
    precedent = rule(match={"vendor_contains": "Printer", "account_contains": "Business savings"})
    assert engine.propose(row(), [precedent])["confidence"] == "high"
    assert engine.classify([row()], [precedent])[0]["classification"]["decision"] is None


def test_all_disjoint_buckets_and_credits():
    rows, rules = [], []
    for i, decision in enumerate(["business", "personal", "mixed", "owner_reimbursement", "owner_contribution"]):
        rows.append(row(id=str(i), vendor=str(i), account="Unknown", card="", amount="10.01"))
        rules.append(rule(id=str(i), match={"vendor_contains": str(i)}, decision=decision, **({"business_pct": 26, "basis": "Office allocation"} if decision == "mixed" else {})))
    rows.append(row(id="credit", kind="credit", amount=99))
    totals = engine.summary(engine.classify(rows, rules))
    assert totals["total_charge_dollars"] == 50.05
    assert totals["reimbursable_to_owner_dollars"] == 10.01
    assert totals["counts"]["charges"] == 5
    assert totals["reconciles"]


def test_classification_route_and_package_export_read_current_rules(tmp_path, monkeypatch):
    import csv
    import json
    import zipfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import plugin_api

    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(core, "effective_settings", lambda: {})
    core.save_ledger({"transactions": [row(status="missing")], "missing": [], "counts": {}})
    engine.save_rule(rule(), path=tmp_path / "classification.json")
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as client:
        response = client.get("/classification")
        assert response.status_code == 200
        assert response.json()["summary"]["business_dollars"] == 100
        assert response.json()["queue"] == []
        result = client.post("/package", json={"output_dir": str(tmp_path / "package")}).json()
    with zipfile.ZipFile(result["archive"]) as archive:
        records = list(csv.DictReader(archive.read("classification.csv").decode().splitlines()))
    assert len(records) == 1
    assert list(records[0]) == engine.EXPORT_FIELDS
    assert records[0]["decision"] == "business"
    assert records[0]["decided_by"] == "owner"
    assert records[0]["note"] == "Office equipment"


def test_owner_contribution_inflow_is_not_income_or_charge_spend():
    confirmed = engine.classify([row(kind="credit", amount=99)], [rule(decision="owner_contribution")])
    totals = engine.summary(confirmed)
    assert totals["owner_contribution_inflow_dollars"] == 99
    assert totals["total_charge_dollars"] == 0
    assert totals["business_dollars"] == 0
    assert engine.classify([row()], [rule(decision="owner_contribution")])[0]["classification"]["decision"] is None


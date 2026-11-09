"""Python tool half of the hybrid Receipt Ledger desktop/dashboard plugin."""


def _schema(name, description, properties):
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": [], "additionalProperties": False}}


def _date(description):
    return {"type": "string", "format": "date", "description": description + " Inclusive YYYY-MM-DD; omit for all saved dates."}


DATES = {"from_date": _date("First charge date."), "to_date": _date("Last charge date.")}
SCHEMAS = {
    "spend": _schema("receipt_ledger_spend",
        "Read saved charge spend grouped by vendor, category, month or account. Returns dollars, count, totals and evidence rows with date, vendor, amount, category, account and receipt status. Credits excluded; no fetch, writes or currency conversion. Only the last scan is available.",
        {"by": {"type": "string", "enum": ["vendor", "category", "month", "account"], "default": "vendor", "description": "Grouping dimension; month is YYYY-MM."},
         **DATES,
         "vendor": {"type": "string", "description": "Case-insensitive substring of the vendor field."},
         "category": {"type": "string", "description": "Case-insensitive substring of category; falls back to category_kind when category is empty, e.g. hosting."}}),
    "missing_paper": _schema("receipt_ledger_missing_paper",
        "Read charges whose receipt status is missing, newest date first. Returns rows with date, vendor, amount, category, account and the saved vendor portal, total_count, missing_dollars and has_more. Attached or matched receipts and credits are excluded. Cached data only; never downloads receipts.",
        {"min_amount": {"type": "number", "minimum": 0, "default": 0, "description": "Include amounts at or above this threshold, in ledger dollars."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50, "description": "Maximum newest missing charges to return. total_count reports all matches."}}),
    "receipt_coverage": _schema("receipt_ledger_coverage",
        "Count saved charges with and without paper over inclusive dates. Returns charges, with_paper, missing, missing_dollars and coverage_pct by charge count. attached and matched mean paper; credits excluded. Never scans or contacts Securo.", DATES),
    "status": _schema("receipt_ledger_status",
        "Read Receipt Ledger configuration readiness, source (securo or files), scanned period and last_scan time. Explains how to configure or scan if unavailable. Does not expose credentials or contact Securo.", {}),
}


def _handler(function):
    def handle(args=None, **_runtime):
        if function is None:
            return {"ok": False, "summary": "Receipt Ledger's dashboard Python files are unavailable. Restore dashboard/agent_tools.py in the plugin installation."}
        if args is not None and not isinstance(args, dict):
            return {"ok": False, "summary": "Receipt Ledger tool arguments must be an object."}
        try:
            return function(**(args or {}))
        except Exception:
            return {"ok": False, "summary": "Invalid Receipt Ledger arguments. Check the tool's documented filters."}
    return handle


def register(ctx):
    """Hermes calls this once after importing this directory as a package."""
    try:
        from .dashboard import agent_tools
    except Exception:
        # The desktop and dashboard remain usable even on an incomplete install.
        agent_tools = None
    for function_name, schema in SCHEMAS.items():
        function = getattr(agent_tools, function_name, None)
        ctx.register_tool(name=schema["name"], toolset="receipt_ledger", schema=schema,
                          handler=_handler(function), description=schema["description"])

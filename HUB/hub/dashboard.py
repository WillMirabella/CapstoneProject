"""Aggregates for the trends dashboard.

Defect trends use production runs only: validation runs contain deliberately
defective cans, so they are reported separately. A can counts on the day it
reached the exit sensor (when the reject decision was made), in the HUB's
local time zone.
"""

import time
from datetime import datetime, timedelta

from . import reports
from .db import CAMERA_NAMES, CAMERAS

# A shipment line is flagged when its reject rate is this many times the
# period's overall rate, with enough cans for the rate to mean something.
OUTLIER_RATIO = 1.75
OUTLIER_MIN_CANS = 300


def _period(days, now):
    """(start, end, prev_start) as Unix times; start is local midnight."""
    today = datetime.fromtimestamp(now).date()
    first = today - timedelta(days=days - 1)
    start = time.mktime(first.timetuple())
    prev_start = time.mktime((first - timedelta(days=days)).timetuple())
    return start, now, prev_start, first, today


def _filters(supplier, sku):
    sql, args = "", []
    if supplier:
        sql += " AND s.supplier = ?"
        args.append(supplier)
    if sku:
        sql += " AND l.sku = ?"
        args.append(sku)
    return sql, args


PRODUCTION_CANS = """
    FROM can c
    JOIN run r ON r.id = c.run_id
    JOIN shipment_line l ON l.id = r.shipment_line_id
    JOIN shipment s ON s.id = l.shipment_id
    WHERE r.kind = 'production' AND c.exited_at >= ? AND c.exited_at < ?
"""

COUNTS = """
    count(*) AS cans,
    coalesce(sum(c.rejected), 0) AS rejected,
    coalesce(sum(c.c1), 0) AS c1,
    coalesce(sum(c.keyence), 0) AS keyence,
    coalesce(sum(c.c2), 0) AS c2
"""


def _rate(n, d):
    return n / d if d else None


def _counts(row):
    out = {k: row[k] for k in ("cans", "rejected", *CAMERAS)}
    out["reject_rate"] = _rate(row["rejected"], row["cans"])
    out["flag_rates"] = {cam: _rate(row[cam], row["cans"]) for cam in CAMERAS}
    return out


def dashboard_data(conn, days=30, supplier=None, sku=None, now=None):
    now = now or time.time()
    days = max(1, min(int(days), 3650))
    start, end, prev_start, first, today = _period(days, now)
    fsql, fargs = _filters(supplier, sku)

    def q(select, extra="", args=(), window=(start, end)):
        return conn.execute(
            f"SELECT {select} {PRODUCTION_CANS} {fsql} {extra}",
            (*window, *fargs, *args)).fetchall()

    totals = _counts(q(COUNTS)[0])
    previous = _counts(q(COUNTS, window=(prev_start, start))[0])
    totals["runs"] = conn.execute(
        "SELECT count(*) FROM run r LEFT JOIN shipment_line l ON l.id = r.shipment_line_id"
        " LEFT JOIN shipment s ON s.id = l.shipment_id"
        f" WHERE r.kind = 'production' AND r.started_at >= ? AND r.started_at < ? {fsql}",
        (start, end, *fargs)).fetchone()[0]

    # Daily series, with every day present (None rates on days with no cans).
    by_day = {row["day"]: row for row in q(
        f"date(c.exited_at, 'unixepoch', 'localtime') AS day, {COUNTS}", "GROUP BY day")}
    daily = []
    d = first
    while d <= today:
        key = d.isoformat()
        row = by_day.get(key)
        entry = _counts(row) if row else {"cans": 0, "rejected": 0, "reject_rate": None,
                                          "flag_rates": dict.fromkeys(CAMERAS), **dict.fromkeys(CAMERAS, 0)}
        entry["day"] = key
        daily.append(entry)
        d += timedelta(days=1)

    overall = totals["reject_rate"] or 0
    shipments = []
    for row in q(
        "l.id AS line_id, s.id AS shipment_id, s.supplier, s.arrival_date, s.po_number,"
        f" s.bill_of_lading, l.sku, l.lot_code, count(DISTINCT r.id) AS runs, {COUNTS}",
        "GROUP BY l.id",
    ):
        item = dict(_counts(row), **{k: row[k] for k in (
            "line_id", "shipment_id", "supplier", "arrival_date", "po_number",
            "bill_of_lading", "sku", "lot_code", "runs")})
        item["vs_overall"] = _rate(item["reject_rate"], overall) if overall else None
        item["outlier"] = bool(item["cans"] >= OUTLIER_MIN_CANS and item["vs_overall"]
                               and item["vs_overall"] >= OUTLIER_RATIO)
        shipments.append(item)
    shipments.sort(key=lambda s: -(s["reject_rate"] or 0))

    suppliers = [dict(_counts(row), supplier=row["supplier"]) for row in q(
        f"s.supplier, {COUNTS}", "GROUP BY s.supplier")]
    suppliers.sort(key=lambda s: -(s["reject_rate"] or 0))

    # Camera agreement: of the cans each camera flagged, how many did no other
    # camera flag? A camera that is usually alone may be over-sensitive, or
    # may be the only one that can see a certain defect.
    agree = q(
        "sum(c.c1 AND NOT c.keyence AND NOT c.c2) AS c1_only,"
        " sum(c.keyence AND NOT c.c1 AND NOT c.c2) AS keyence_only,"
        f" sum(c.c2 AND NOT c.c1 AND NOT c.keyence) AS c2_only, {COUNTS}")[0]
    agreement = []
    for cam in CAMERAS:
        flags = agree[cam] or 0
        alone = agree[f"{cam}_only"] or 0
        agreement.append({"camera": cam, "flags": flags, "alone": alone,
                          "shared": flags - alone, "alone_share": _rate(alone, flags)})

    return {
        "period": {"days": days, "start": first.isoformat(), "end": today.isoformat()},
        "filters": {"supplier": supplier, "sku": sku},
        "options": _options(conn),
        "cameras": [{"key": k, "name": CAMERA_NAMES[k]} for k in CAMERAS],
        "totals": totals,
        "previous": previous,
        "daily": daily,
        "shipments": shipments,
        "suppliers": suppliers,
        "agreement": agreement,
        "outlier_ratio": OUTLIER_RATIO,
        "validation": _validation(conn, start, end),
        "runs": _runs(conn, start, end, fsql, fargs),
        "health": _health(conn, start, end),
    }


def _options(conn):
    return {
        "suppliers": [r[0] for r in conn.execute(
            "SELECT DISTINCT supplier FROM shipment ORDER BY supplier")],
        "skus": [r[0] for r in conn.execute(
            "SELECT DISTINCT sku FROM shipment_line ORDER BY sku")],
    }


def _validation(conn, start, end):
    """Validation runs in the period (not filtered by supplier/SKU)."""
    out = []
    for row in conn.execute(
        "SELECT id, started_at, operator FROM run WHERE kind = 'validation'"
        " AND started_at >= ? AND started_at < ? ORDER BY started_at DESC",
        (start, end),
    ):
        report = reports.validation_report(conn, row["id"])
        out.append({
            "run_id": row["id"], "started_at": row["started_at"],
            "operator": row["operator"], "status": report["status"],
            "entered": report["entered"], "planned_cans": report["planned_cans"],
            "caught": report["caught"], "seeded": len(report["seeds"]),
            "false_rejects": report["false_rejects"],
            "cameras": {cam: {k: report["cameras"][cam][k] for k in
                              ("expected", "detected", "detection_rate", "false_flags")}
                        for cam in CAMERAS},
            "warnings": report["warnings"],
        })
    return out


def _runs(conn, start, end, fsql, fargs, limit=200):
    return [dict(r) for r in conn.execute(
        "SELECT r.id, r.kind, r.status, r.operator, r.started_at, r.stopped_at,"
        " r.finished_at, s.supplier, s.arrival_date, s.po_number, l.sku, l.lot_code,"
        " count(c.id) AS cans, coalesce(sum(c.rejected), 0) AS rejected,"
        " coalesce(sum(c.c1), 0) AS c1, coalesce(sum(c.keyence), 0) AS keyence,"
        " coalesce(sum(c.c2), 0) AS c2,"
        " coalesce(sum(c.recovered), 0) AS recovered, coalesce(sum(c.lost), 0) AS lost"
        " FROM run r LEFT JOIN shipment_line l ON l.id = r.shipment_line_id"
        " LEFT JOIN shipment s ON s.id = l.shipment_id"
        " LEFT JOIN can c ON c.run_id = r.id"
        f" WHERE r.started_at >= ? AND r.started_at < ? {fsql}"
        " GROUP BY r.id ORDER BY r.started_at DESC LIMIT ?",
        (start, end, *fargs, limit))]


def _health(conn, start, end):
    """One row per Rj-Pi session (power-up) active in the period."""
    late = {}
    for row in conn.execute(
        "SELECT session, json_extract(raw, '$.src') AS src, count(*) AS n FROM event_log"
        " WHERE e = 'late' AND received_at >= ? AND received_at < ? GROUP BY session, src",
        (start, end),
    ):
        late.setdefault(row["session"], {})[row["src"]] = row["n"]
    out = []
    for row in conn.execute(
        "SELECT s.session, s.first_seen, s.last_seen, s.missed,"
        " (SELECT count(*) FROM can WHERE session = s.session) AS cans,"
        " (SELECT count(*) FROM can WHERE session = s.session AND recovered = 1) AS recovered,"
        " (SELECT count(*) FROM can WHERE session = s.session AND lost = 1) AS lost,"
        " (SELECT count(*) FROM can WHERE session = s.session AND run_id IS NULL) AS unassigned"
        " FROM rj_session s WHERE s.last_seen >= ? AND s.first_seen < ?"
        " ORDER BY s.first_seen DESC",
        (start, end),
    ):
        item = dict(row)
        item["late"] = {cam: late.get(row["session"], {}).get(cam, 0) for cam in CAMERAS}
        out.append(item)
    return out


RUN_CSV_COLUMNS = ["id", "kind", "status", "started", "finished", "operator", "supplier",
                   "arrival_date", "po_number", "sku", "lot_code", "cans", "rejected",
                   "reject_rate", "telodyne_flags", "keyence_flags", "elp_flags",
                   "recovered", "lost"]


def runs_csv_rows(conn, days=30, supplier=None, sku=None, now=None):
    now = now or time.time()
    start, end, *_ = _period(max(1, min(int(days), 3650)), now)
    fsql, fargs = _filters(supplier, sku)

    def ts(t):
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else ""

    yield RUN_CSV_COLUMNS
    for r in _runs(conn, start, end, fsql, fargs, limit=100000):
        yield [r["id"], r["kind"], r["status"], ts(r["started_at"]), ts(r["finished_at"]),
               r["operator"] or "", r["supplier"] or "", r["arrival_date"] or "",
               r["po_number"] or "", r["sku"] or "", r["lot_code"] or "", r["cans"],
               r["rejected"], f"{r['rejected'] / r['cans']:.4f}" if r["cans"] else "",
               r["c1"], r["keyence"], r["c2"], r["recovered"], r["lost"]]

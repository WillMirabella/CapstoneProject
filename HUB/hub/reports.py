"""Read-only summaries for the operator screen and dashboard."""

import json
from collections import Counter

from .db import CAMERA_NAMES, CAMERAS


def run_summary(conn, run_id):
    """Counts for one run. Works while the run is live or after it finishes."""
    run = conn.execute(
        "SELECT r.*, l.sku, l.lot_code, s.supplier, s.arrival_date, s.po_number"
        " FROM run r LEFT JOIN shipment_line l ON l.id = r.shipment_line_id"
        " LEFT JOIN shipment s ON s.id = l.shipment_id WHERE r.id = ?", (run_id,),
    ).fetchone()
    if run is None:
        return None
    c = conn.execute(
        "SELECT count(*) AS entered,"
        "  count(*) FILTER (WHERE exited_at IS NOT NULL) AS exited,"
        "  count(*) FILTER (WHERE exited_at IS NULL AND lost = 0) AS on_belt,"
        "  count(*) FILTER (WHERE rejected = 1) AS rejected,"
        "  count(*) FILTER (WHERE rejected = 0) AS passed,"
        "  count(*) FILTER (WHERE recovered = 1) AS recovered,"
        "  count(*) FILTER (WHERE lost = 1) AS lost,"
        "  coalesce(sum(c1), 0) AS c1, coalesce(sum(keyence), 0) AS keyence,"
        "  coalesce(sum(c2), 0) AS c2"
        " FROM can WHERE run_id = ?", (run_id,),
    ).fetchone()
    combos = Counter()
    for row in conn.execute(
        "SELECT c1, keyence, c2 FROM can WHERE run_id = ? AND rejected = 1", (run_id,)
    ):
        combos[tuple(cam for cam in CAMERAS if row[cam])] += 1
    return {
        "run": dict(run),
        "entered": c["entered"],
        "exited": c["exited"],
        "on_belt": c["on_belt"],
        "rejected": c["rejected"],
        # Reached the exit sensor and was not rejected (rejected is NULL until exit).
        "passed": c["passed"],
        "reject_rate": c["rejected"] / c["exited"] if c["exited"] else None,
        "recovered": c["recovered"],
        "lost": c["lost"],
        "flags": {cam: c[cam] for cam in CAMERAS},
        # Which cameras flagged each rejected can, most common first, e.g.
        # [{"cameras": ["c1"], "count": 4}, {"cameras": ["c1", "c2"], "count": 1}].
        # A camera that is often the *only* one flagging is worth a look.
        "flag_combos": [{"cameras": list(k), "count": n} for k, n in combos.most_common()],
    }


def validation_report(conn, run_id):
    """Score each camera against the seeded (known-defective) cans of a run."""
    run = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["kind"] != "validation":
        return None

    seeds = []
    for s in conn.execute(
        "SELECT s.position, s.expected, s.defect, c.acn, c.exited_at, c.rejected,"
        "       c.c1, c.keyence, c.c2, c.recovered, c.lost"
        " FROM seeded_can s LEFT JOIN can c"
        "   ON c.run_id = s.run_id AND c.position = s.position"
        " WHERE s.run_id = ? ORDER BY s.position", (run_id,),
    ):
        expected = [cam for cam in s["expected"].split(",") if cam]
        flagged = [cam for cam in CAMERAS if s["acn"] is not None and s[cam]]
        if s["acn"] is None:
            result = "not run"  # the run ended before this position
        elif s["lost"]:
            result = "lost"
        elif s["exited_at"] is None:
            result = "pending"
        else:
            result = "caught" if s["rejected"] else "missed"
        seeds.append({
            "position": s["position"], "acn": s["acn"], "defect": s["defect"],
            "expected": expected, "flagged": flagged, "result": result,
            "recovered": bool(s["recovered"]),
        })

    scored = [s for s in seeds if s["result"] in ("caught", "missed")]
    cameras = {}
    for cam in CAMERAS:
        should = [s for s in scored if cam in s["expected"]]
        cameras[cam] = {
            "expected": len(should),
            "detected": sum(cam in s["flagged"] for s in should),
            # Seeded cans this camera flagged though only other cameras were
            # expected to (seeds with no expected list accept any camera).
            "extra": sum(bool(cam in s["flagged"] and s["expected"]
                              and cam not in s["expected"]) for s in scored),
        }
        cameras[cam]["detection_rate"] = (
            cameras[cam]["detected"] / cameras[cam]["expected"]
            if cameras[cam]["expected"] else None)

    seeded_positions = {s["position"] for s in seeds}
    false_flags = Counter()
    false_rejects = 0
    for c in conn.execute(
        "SELECT position, rejected, c1, keyence, c2 FROM can"
        " WHERE run_id = ? AND exited_at IS NOT NULL", (run_id,),
    ):
        if c["position"] in seeded_positions:
            continue
        false_rejects += c["rejected"] or 0
        for cam in CAMERAS:
            false_flags[cam] += c[cam]
    for cam in CAMERAS:
        cameras[cam]["false_flags"] = false_flags[cam]

    summary = run_summary(conn, run_id)
    warnings = []
    if run["status"] in ("complete", "incomplete") and summary["entered"] != run["planned_cans"]:
        warnings.append(
            f"{summary['entered']} cans entered but {run['planned_cans']} were planned;"
            " seeded positions may not line up with the real defective cans")
    if summary["recovered"]:
        warnings.append(
            f"{summary['recovered']} can(s) recovered from lost entry events;"
            " positions near them may be off")
    if summary["lost"]:
        warnings.append(f"{summary['lost']} can(s) lost when the Rj-Pi restarted")

    return {
        "run_id": run_id,
        "status": run["status"],
        "planned_cans": run["planned_cans"],
        "entered": summary["entered"],
        "seeds": seeds,
        "caught": sum(s["result"] == "caught" for s in seeds),
        "scored": len(scored),
        "false_rejects": false_rejects,
        "cameras": cameras,
        "warnings": warnings,
    }


# --- Operator screen -----------------------------------------------------------

RJPI_OFFLINE_AFTER = 12.0  # seconds without any event (health is every 5 s)


def shipments(conn, limit=50):
    """Most recent shipments, newest arrival first, each with its SKU lines."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM shipment ORDER BY coalesce(arrival_date, '') DESC, id DESC"
        " LIMIT ?", (limit,))]
    by_id = {r["id"]: dict(r, lines=[]) for r in rows}
    if by_id:
        marks = ",".join("?" * len(by_id))
        for line in conn.execute(
            "SELECT l.*, count(r.id) AS runs FROM shipment_line l"
            " LEFT JOIN run r ON r.shipment_line_id = l.id"
            f" WHERE l.shipment_id IN ({marks}) GROUP BY l.id ORDER BY l.id",
            list(by_id),
        ):
            by_id[line["shipment_id"]]["lines"].append(dict(line))
    return [by_id[r["id"]] for r in rows]


def rjpi_status(conn, now):
    row = conn.execute(
        "SELECT * FROM rj_session ORDER BY last_seen DESC LIMIT 1").fetchone()
    if row is None:
        return {"online": False, "seen": False}
    health = json.loads(row["health"]) if row["health"] else {}
    return {
        "online": now - row["last_seen"] < RJPI_OFFLINE_AFTER,
        "seen": True,
        "session": row["session"],
        "last_seen_ago": round(now - row["last_seen"], 1),
        "missed": row["missed"],
        "cans_on_belt": health.get("cans_on_belt"),
        "late_packets": health.get("late_packets", 0),
        "invalid_packets": health.get("invalid_packets", 0),
        "unexpected_exits": health.get("unexpected_exits", 0),
        "overflows": health.get("overflows", 0),
    }


def recent_runs(conn, limit=10):
    return [dict(r) for r in conn.execute(
        "SELECT r.*, l.sku, s.supplier FROM run r"
        " LEFT JOIN shipment_line l ON l.id = r.shipment_line_id"
        " LEFT JOIN shipment s ON s.id = l.shipment_id"
        " ORDER BY r.id DESC LIMIT ?", (limit,))]


def run_detail(conn, run_id):
    summary = run_summary(conn, run_id)
    if summary is None:
        return None
    if summary["run"]["kind"] == "validation":
        summary["validation"] = validation_report(conn, run_id)
    return summary


def live_state(conn, now):
    """Everything the operator screen shows, in one snapshot."""
    active = conn.execute("SELECT id FROM run WHERE status = 'active'").fetchone()
    finishing = conn.execute(
        "SELECT id FROM run WHERE status = 'finishing' ORDER BY id").fetchall()
    unassigned = conn.execute(
        "SELECT count(*) FROM can WHERE run_id IS NULL AND entered_at > ?",
        (now - 300,)).fetchone()[0]
    return {
        "now": now,
        "cameras": [{"key": k, "name": CAMERA_NAMES[k]} for k in CAMERAS],
        "rjpi": rjpi_status(conn, now),
        "active": run_detail(conn, active["id"]) if active else None,
        "finishing": [run_summary(conn, r["id"]) for r in finishing],
        "unassigned_recent": unassigned,
    }

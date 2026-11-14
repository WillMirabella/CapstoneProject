"""HUB server: one process for the Rj-Pi listener, the API and the web pages.

    UDP :5001  <- Rj-Pi events (Rejector/notes.md)   -> Tracker -> SQLite
    HTTP :8000 -> operator page, JSON API, live updates (Server-Sent Events)

Run it with:  python3 -m hub --db hub.db
Everything runs on the asyncio event loop, so the tracker is never used from
two threads at once. Each database call takes about a millisecond.
"""

import asyncio
import csv
import io
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import dashboard, db, reports
from .tracker import RunError, Tracker

log = logging.getLogger("hub")
WEB = Path(__file__).resolve().parent.parent / "web"
UDP_PORT = 5001
STALE_RUN_SECONDS = 60.0  # a stopped run whose cans stop exiting becomes incomplete


class Hub:
    def __init__(self, db_path):
        self.conn = db.connect(db_path)
        self.tracker = Tracker(self.conn)
        self.version = 0  # bumped on every change; live streams watch it

    def changed(self):
        self.version += 1

    def on_datagram(self, data):
        try:
            self.tracker.handle_datagram(data)
        except Exception:  # one bad event must never stop the listener
            log.exception("failed to handle datagram %r", data[:200])
        self.changed()

    def state(self):
        return reports.live_state(self.conn, time.time())


class RjPiProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub):
        self.hub = hub

    def datagram_received(self, data, addr):
        self.hub.on_datagram(data)


# --- Request bodies ----------------------------------------------------------------


def _blank_to_none(model):
    for name, value in model:
        if isinstance(value, str) and not value.strip():
            setattr(model, name, None)
    return model


class LineIn(BaseModel):
    sku: str
    lot_code: Optional[str] = None
    quantity: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None


class ShipmentIn(BaseModel):
    supplier: str
    arrival_date: Optional[str] = None
    po_number: Optional[str] = None
    bill_of_lading: Optional[str] = None
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    notes: Optional[str] = None
    lines: list[LineIn] = []


class ProductionIn(BaseModel):
    shipment_line_id: int
    operator: Optional[str] = None
    notes: Optional[str] = None


class SeedIn(BaseModel):
    position: int
    expected: list[str] = []
    defect: Optional[str] = None


class ValidationIn(BaseModel):
    planned_cans: int
    seeds: list[SeedIn]
    operator: Optional[str] = None
    notes: Optional[str] = None
    shipment_line_id: Optional[int] = None


# --- App ---------------------------------------------------------------------------


def create_app(db_path="hub.db", udp_host="0.0.0.0", udp_port=UDP_PORT):
    hub = Hub(db_path)

    async def expire_loop():
        while True:
            await asyncio.sleep(5)
            hub.tracker.expire_stale_runs(timeout=STALE_RUN_SECONDS)
            hub.changed()  # also refreshes "Rj-Pi offline" on open screens

    @asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        transport = None
        if udp_port is not None:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: RjPiProtocol(hub), local_addr=(udp_host, udp_port))
            log.info("listening for Rj-Pi events on udp %s:%s", udp_host, udp_port)
        expirer = asyncio.create_task(expire_loop())
        yield
        expirer.cancel()
        if transport is not None:
            transport.close()
        hub.conn.close()

    app = FastAPI(title="Defect Detection HUB", lifespan=lifespan)
    app.state.hub = hub

    def act(fn, *args, **kwargs):
        """Run an operator action; RunError becomes a 400 the screen can show."""
        try:
            result = fn(*args, **kwargs)
        except RunError as error:
            raise HTTPException(status_code=400, detail=str(error))
        hub.changed()
        return result

    # Pages
    @app.get("/", include_in_schema=False)
    async def operator_page():
        return FileResponse(WEB / "operator.html")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_page():
        return FileResponse(WEB / "dashboard.html")

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    # State
    @app.get("/api/state")
    async def get_state():
        return hub.state()

    @app.get("/api/stream")
    async def stream(request: Request):
        """Server-Sent Events: a full state snapshot whenever something changes."""

        async def events():
            sent_version, sent_at = None, 0.0
            while not await request.is_disconnected():
                now = time.monotonic()
                if hub.version != sent_version or now - sent_at >= 2.0:
                    sent_version, sent_at = hub.version, now
                    yield f"data: {json.dumps(hub.state())}\n\n"
                await asyncio.sleep(0.25)  # at most 4 updates a second

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Dashboard
    @app.get("/api/dashboard")
    async def get_dashboard(days: int = 30, supplier: Optional[str] = None,
                            sku: Optional[str] = None):
        return dashboard.dashboard_data(hub.conn, days, supplier or None, sku or None)

    @app.get("/api/export/runs.csv")
    async def export_runs(days: int = 30, supplier: Optional[str] = None,
                          sku: Optional[str] = None):
        out = io.StringIO()
        csv.writer(out).writerows(
            dashboard.runs_csv_rows(hub.conn, days, supplier or None, sku or None))
        return Response(out.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="runs-last-{days}-days.csv"'})

    # Shipments
    @app.get("/api/shipments")
    async def list_shipments():
        return reports.shipments(hub.conn)

    @app.post("/api/shipments")
    async def add_shipment(body: ShipmentIn):
        body = _blank_to_none(body)
        lines = [_blank_to_none(line) for line in body.lines]
        if not body.supplier:
            raise HTTPException(400, "A shipment needs a supplier")
        if any(not line.sku for line in lines):
            raise HTTPException(400, "Every SKU line needs a SKU")
        fields = body.model_dump(exclude={"lines"})
        shipment_id = act(hub.tracker.add_shipment, **fields)
        for line in lines:
            act(hub.tracker.add_shipment_line, shipment_id, **line.model_dump())
        return {"id": shipment_id}

    @app.post("/api/shipments/{shipment_id}/lines")
    async def add_line(shipment_id: int, body: LineIn):
        body = _blank_to_none(body)
        if not body.sku:
            raise HTTPException(400, "A shipment line needs a SKU")
        return {"id": act(hub.tracker.add_shipment_line, shipment_id, **body.model_dump())}

    # Runs
    @app.get("/api/runs")
    async def list_runs(limit: int = 10):
        return reports.recent_runs(hub.conn, limit)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: int):
        detail = reports.run_detail(hub.conn, run_id)
        if detail is None:
            raise HTTPException(404, f"No run {run_id}")
        return detail

    @app.post("/api/runs/production")
    async def start_production(body: ProductionIn):
        body = _blank_to_none(body)
        return {"id": act(hub.tracker.start_production_run, body.shipment_line_id,
                          operator=body.operator, notes=body.notes)}

    @app.post("/api/runs/validation")
    async def start_validation(body: ValidationIn):
        body = _blank_to_none(body)
        positions = [s.position for s in body.seeds]
        if len(positions) != len(set(positions)):
            raise HTTPException(400, "Each seeded position can only be listed once")
        seeds = {s.position: {"expected": s.expected, "defect": s.defect}
                 for s in body.seeds}
        return {"id": act(hub.tracker.start_validation_run, seeds, body.planned_cans,
                          operator=body.operator, notes=body.notes,
                          shipment_line_id=body.shipment_line_id)}

    @app.post("/api/runs/stop")
    async def stop_run():
        return {"id": act(hub.tracker.stop_run)}

    return app

"""HTTP API tests. Needs the packages in requirements.txt (skipped otherwise)."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

try:
    from fastapi.testclient import TestClient
    from hub.main import create_app
except ImportError as error:  # pragma: no cover
    raise unittest.SkipTest(f"API tests need requirements.txt installed ({error})")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(":memory:", udp_port=None)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.hub = self.app.state.hub

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def ok(self, method, path, body=None):
        res = self.client.request(method, path, json=body)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def error(self, method, path, body=None):
        res = self.client.request(method, path, json=body)
        self.assertEqual(res.status_code, 400, res.text)
        return res.json()["detail"]

    def add_shipment(self):
        ship = self.ok("POST", "/api/shipments", {
            "supplier": "Northline Can Co.", "arrival_date": "2026-11-12", "po_number": "",
            "lines": [{"sku": "12OZ-STD", "lot_code": "L1", "quantity": 40000},
                      {"sku": "16OZ-TALL"}],
        })
        return self.ok("GET", "/api/shipments")[0], ship["id"]

    def send(self, **event):
        event.setdefault("session", "s1")
        event.setdefault("seq", self.hub.version)
        self.hub.on_datagram(json.dumps(event).encode())

    def test_pages_are_served(self):
        self.assertIn("Line Operator", self.client.get("/").text)
        self.assertEqual(self.client.get("/static/operator.js").status_code, 200)
        self.assertEqual(self.client.get("/static/operator.css").status_code, 200)

    def test_shipment_with_lines(self):
        shipment, ship_id = self.add_shipment()
        self.assertEqual(shipment["id"], ship_id)
        self.assertIsNone(shipment["po_number"])  # blank -> NULL
        self.assertEqual([l["sku"] for l in shipment["lines"]], ["12OZ-STD", "16OZ-TALL"])

    def test_shipment_validation(self):
        self.assertIn("supplier", self.error("POST", "/api/shipments", {"supplier": " "}))
        self.assertIn("SKU", self.error("POST", "/api/shipments",
                                        {"supplier": "X", "lines": [{"sku": ""}]}))

    def test_production_run_lifecycle(self):
        shipment, _ = self.add_shipment()
        line = shipment["lines"][0]["id"]
        run = self.ok("POST", "/api/runs/production",
                      {"shipment_line_id": line, "operator": "Sam"})["id"]
        self.assertIn("Stop the current run", self.error(
            "POST", "/api/runs/production", {"shipment_line_id": line}))

        self.send(e="entry", acn=1)
        self.send(e="entry", acn=2)
        self.send(e="flag", src="c1", acn=2)
        state = self.ok("GET", "/api/state")
        self.assertEqual(state["active"]["run"]["id"], run)
        self.assertEqual(state["active"]["entered"], 2)
        self.assertEqual(state["active"]["run"]["sku"], "12OZ-STD")
        self.assertTrue(state["rjpi"]["online"])

        self.ok("POST", "/api/runs/stop")
        state = self.ok("GET", "/api/state")
        self.assertIsNone(state["active"])
        self.assertEqual(state["finishing"][0]["on_belt"], 2)

        self.send(e="exit", acn=1, rejected=False, c1=False, keyence=False, c2=False)
        self.send(e="exit", acn=2, rejected=True, c1=True, keyence=False, c2=False)
        detail = self.ok("GET", f"/api/runs/{run}")
        self.assertEqual(detail["run"]["status"], "complete")
        self.assertEqual(detail["rejected"], 1)
        self.assertEqual(detail["flag_combos"], [{"cameras": ["c1"], "count": 1}])
        self.assertEqual(self.ok("GET", "/api/runs")[0]["id"], run)

    def test_validation_run(self):
        run = self.ok("POST", "/api/runs/validation", {
            "planned_cans": 3,
            "seeds": [{"position": 2, "expected": ["keyence"], "defect": "bottom scratch"}],
        })["id"]
        for acn in (1, 2, 3):
            self.send(e="entry", acn=acn)
        self.send(e="flag", src="keyence", acn=2)
        state = self.ok("GET", "/api/state")
        self.assertEqual(state["active"]["validation"]["seeds"][0]["acn"], 2)
        self.ok("POST", "/api/runs/stop")
        for acn in (1, 2, 3):
            self.send(e="exit", acn=acn, rejected=acn == 2, keyence=acn == 2)
        report = self.ok("GET", f"/api/runs/{run}")["validation"]
        self.assertEqual(report["seeds"][0]["result"], "caught")
        self.assertEqual(report["cameras"]["keyence"]["detection_rate"], 1.0)

    def test_validation_errors(self):
        self.assertIn("outside", self.error("POST", "/api/runs/validation", {
            "planned_cans": 10, "seeds": [{"position": 11}]}))
        self.assertIn("once", self.error("POST", "/api/runs/validation", {
            "planned_cans": 10, "seeds": [{"position": 1}, {"position": 1}]}))
        self.assertIn("No run is active", self.error("POST", "/api/runs/stop"))

    def test_bad_datagram_does_not_break_server(self):
        self.hub.on_datagram(b"\xff\xfe garbage")
        self.assertEqual(self.client.get("/api/state").status_code, 200)

    def test_dashboard(self):
        shipment, _ = self.add_shipment()
        self.ok("POST", "/api/runs/production", {"shipment_line_id": shipment["lines"][0]["id"]})
        for acn in (1, 2, 3, 4):
            self.send(e="entry", acn=acn)
        self.ok("POST", "/api/runs/stop")
        for acn in (1, 2, 3, 4):
            self.send(e="exit", acn=acn, rejected=acn == 4, c1=acn == 4, keyence=False,
                      c2=acn == 4)
        self.send(e="late", src="keyence", racn=9)

        d = self.ok("GET", "/api/dashboard?days=7")
        self.assertEqual(len(d["daily"]), 7)
        self.assertEqual(d["totals"]["cans"], 4)
        self.assertEqual(d["totals"]["reject_rate"], 0.25)
        self.assertEqual(d["daily"][-1]["flag_rates"]["c1"], 0.25)
        self.assertEqual(d["shipments"][0]["sku"], "12OZ-STD")
        self.assertEqual(d["suppliers"][0]["supplier"], "Northline Can Co.")
        c1 = next(a for a in d["agreement"] if a["camera"] == "c1")
        self.assertEqual((c1["flags"], c1["alone"], c1["shared"]), (1, 0, 1))
        self.assertEqual(d["health"][0]["late"]["keyence"], 1)
        self.assertEqual(d["runs"][0]["cans"], 4)

        other = self.ok("GET", "/api/dashboard?days=7&supplier=Nobody")
        self.assertEqual(other["totals"]["cans"], 0)
        self.assertIsNone(other["totals"]["reject_rate"])

        res = self.client.get("/api/export/runs.csv?days=7")
        self.assertEqual(res.status_code, 200)
        lines = res.text.strip().splitlines()
        self.assertTrue(lines[0].startswith("id,kind,status"))
        self.assertIn("0.2500", lines[1])
        self.assertIn("Defect Trends", self.client.get("/dashboard").text)

    def test_unknown_run_404(self):
        self.assertEqual(self.client.get("/api/runs/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()

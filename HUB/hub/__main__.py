"""python3 -m hub [--db hub.db] [--port 8000] [--udp-port 5001]"""

import argparse
import logging

import uvicorn

from .main import UDP_PORT, create_app


def main():
    parser = argparse.ArgumentParser(description="Defect detection HUB server")
    parser.add_argument("--db", default="hub.db", help="SQLite file (created if missing)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address for the web pages (0.0.0.0 = every network)")
    parser.add_argument("--port", type=int, default=8000, help="web port")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT,
                        help="port the Rj-Pi sends events to")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = create_app(args.db, udp_port=args.udp_port)
    print(f"Operator screen: http://localhost:{args.port}/")
    print(f"Dashboard:       http://localhost:{args.port}/dashboard")
    print("Press Ctrl-C to stop.")

    class HubServer(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # Open browser pages hold live-update streams; end them so the
            # server can stop instead of waiting for every tab to close.
            app.state.hub.stopping = True
            super().handle_exit(sig, frame)

    HubServer(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning",
                             timeout_graceful_shutdown=5)).run()
    print("HUB stopped.")


if __name__ == "__main__":
    main()

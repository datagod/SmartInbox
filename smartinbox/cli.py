"""SmartInbox CLI."""

from __future__ import annotations

import argparse

import uvicorn

from smartinbox.config import load_settings
from smartinbox.core import SmartInboxCore
from smartinbox.web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartInbox — Gmail voice alerts")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = load_settings()
    host = args.host or str(settings.get("host", "127.0.0.1"))
    port = args.port or int(settings.get("port", 8090))

    core = SmartInboxCore(settings)
    app = create_app(core)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
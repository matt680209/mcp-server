from __future__ import annotations

import os

import uvicorn

from web.server import SETTINGS, app


def main() -> None:
    certificate = os.getenv("MCP_SSL_CERTFILE")
    private_key = os.getenv("MCP_SSL_KEYFILE")
    if bool(certificate) != bool(private_key):
        raise SystemExit("Set both MCP_SSL_CERTFILE and MCP_SSL_KEYFILE to enable HTTPS.")
    uvicorn.run(
        app,
        host=SETTINGS.settings["network"]["host"],
        port=SETTINGS.settings["network"]["port"],
        workers=1,
        ssl_certfile=certificate,
        ssl_keyfile=private_key,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()

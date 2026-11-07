"""`python -m anjeo` (or `anjeo`) - serve the engine."""

from __future__ import annotations

import logging
import os


def main() -> None:
    import uvicorn

    from anjeo.api import create_app

    logging.basicConfig(level=os.environ.get("ANJEO_LOG", "INFO"))
    host = os.environ.get("ANJEO_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("ANJEO_TOKEN"):
        raise SystemExit("Refusing to listen on a non-local address without ANJEO_TOKEN set.")
    uvicorn.run(create_app(), host=host, port=int(os.environ.get("ANJEO_PORT", "8710")))


if __name__ == "__main__":
    main()

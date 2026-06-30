from __future__ import annotations

import argparse

import uvicorn

from web.server import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the DeepseekAss local web frontend.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address, use 0.0.0.0 for LAN phone access.")
    parser.add_argument("--port", default=8765, type=int, help="HTTP port.")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for development.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()

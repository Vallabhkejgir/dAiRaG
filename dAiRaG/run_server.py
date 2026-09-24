from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


SERVICE_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = SERVICE_ROOT / "frontend" / "data" / "graph_data.json"
DEFAULT_FRONTEND_INDEX = SERVICE_ROOT / "frontend" / "dist" / "index.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the dAiRaG FastAPI service.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"), help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")), help="Port to listen on.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload while editing the FastAPI app.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve_frontend = os.getenv("DAIRAG_SERVE_FRONTEND", "true").strip().lower() not in {"0", "false", "no"}
    if not DEFAULT_DATA_PATH.exists():
        raise SystemExit(
            "Frontend graph data file is missing from the service bundle."
        )
    if serve_frontend and not DEFAULT_FRONTEND_INDEX.exists():
        raise SystemExit(
            "React frontend build is missing. Run `npm install` and `npm run build` in dAiRaG/frontend first."
        )

    print(f"Serving dAiRaG at http://{args.host}:{args.port}/")
    print(f"Service root: {SERVICE_ROOT}")
    print(f"Graph data: {DEFAULT_DATA_PATH}")
    if serve_frontend:
        print(f"Frontend build: {DEFAULT_FRONTEND_INDEX}")
    else:
        print("Frontend build: skipped (API-only mode)")
    print("Press Ctrl+C to stop.")

    uvicorn.run(
        "backend.api:app",
        app_dir=str(SERVICE_ROOT),
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

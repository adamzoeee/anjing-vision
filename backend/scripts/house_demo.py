"""Generate, parse and serve the isolated synthetic household demo."""
from __future__ import annotations

import argparse
import os
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.house_demo import build_demo, infer_rooms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "infer", "serve"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spacing", type=float, default=0.05)
    parser.add_argument("--profile", choices=("rich", "lived", "risk"), default="rich")
    parser.add_argument("--rooms", nargs="+")
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    if args.action == "generate":
        os.environ["HOUSE_DEMO_PROFILE"] = args.profile
        result = build_demo(args.output, spacing=args.spacing)
        print(f'Generated {len(result["rooms"])} rooms; {result["complete_points"]} unique house points')
    elif args.action == "infer":
        result = infer_rooms(args.output, args.rooms)
        for name, room in result["rooms"].items():
            print(name, room["status"], len(room["objects"]), room.get("error", ""))
        if any(room["status"] == "failed" for room in result["rooms"].values()):
            raise SystemExit(1)
    else:
        if not (args.output / "manifest.json").is_file():
            parser.error("Generate the demo first")
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(args.output.resolve()))
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
        print(f"Synthetic demo: http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()

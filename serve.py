#!/usr/bin/env python3
"""Serve the static STAC site on this machine only (127.0.0.1).

    python serve.py              # http://127.0.0.1:8000/
    python serve.py --port 8080

Serves the site/ folder:
    /stac/collection.json   the STAC catalog
    /browser/            STAC Browser (after running ./setup_browser.sh)

Binds to 127.0.0.1, so it is not reachable from other machines. Adds a CORS
header so a STAC browser on another local port or origin can read the catalog.
"""
import argparse
import functools
import http.server
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".json": "application/json", ".js": "text/javascript"}

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--dir", default=str(Path(__file__).parent / "site"))
    args = p.parse_args()
    handler = functools.partial(Handler, directory=args.dir)
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Serving {args.dir} at http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
        print(f"  catalog: http://127.0.0.1:{args.port}/stac/collection.json")
        print(f"  browser: http://127.0.0.1:{args.port}/browser/")
        httpd.serve_forever()


if __name__ == "__main__":
    main()

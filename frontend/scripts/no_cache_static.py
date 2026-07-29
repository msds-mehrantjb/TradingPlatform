from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class NoCacheStaticHandler(SimpleHTTPRequestHandler):
    def _disable_conditional_cache(self) -> None:
        for header in ("If-Modified-Since", "If-None-Match"):
            if header in self.headers:
                del self.headers[header]

    def do_GET(self) -> None:
        self._disable_conditional_cache()
        super().do_GET()

    def do_HEAD(self) -> None:
        self._disable_conditional_cache()
        super().do_HEAD()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve static files with no-cache headers.")
    parser.add_argument("--directory", default="dist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5173, type=int)
    args = parser.parse_args()

    handler = partial(NoCacheStaticHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {args.directory} on http://{args.host}:{args.port} with no-cache headers", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

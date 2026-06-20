#!/usr/bin/env python3
"""
Local static server with TiTiler / CTOD / S3 tile proxies so Cesium works from any
localhost origin without remote CORS mismatch. S3 responses are disk-cached for
faster repeat loads.

Usage:
  python dev_server.py
  python dev_server.py 8765
"""
from __future__ import annotations

import hashlib
import http.server
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

DEFAULT_PORT = 8765
TITILER_UPSTREAM = "https://titiler2.cbstack.online"
CTOD_UPSTREAM = "https://ctod2.cbstack.online"
S3_UPSTREAM = "https://test-uday123.s3.ap-south-1.amazonaws.com"

PROXIES = {
    "/titiler-proxy": TITILER_UPSTREAM,
    "/ctod-proxy": CTOD_UPSTREAM,
    "/s3-proxy": S3_UPSTREAM,
}

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".tile_cache"
MAX_CACHE_FILE_BYTES = 96 * 1024 * 1024

# TiTiler/CTOD/S3 certs may fail strict verification on some Windows Python builds; dev-only.
SSL_CTX = ssl._create_unverified_context()


def cors_headers(handler: http.server.BaseHTTPRequestHandler) -> list[tuple[str, str]]:
    origin = handler.headers.get("Origin", "*") or "*"
    return [
        ("Access-Control-Allow-Origin", origin),
        ("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization, Range"),
        ("Access-Control-Max-Age", "86400"),
        ("Vary", "Origin"),
    ]


def cache_file_path(target_url: str) -> Path:
    digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:40]
    suffix = Path(target_url.split("?", 1)[0]).suffix or ".bin"
    return CACHE_DIR / f"{digest}{suffix}"


def read_cache(target_url: str) -> Optional[bytes]:
    path = cache_file_path(target_url)
    if path.is_file():
        return path.read_bytes()
    return None


def write_cache(target_url: str, body: bytes) -> None:
    if len(body) > MAX_CACHE_FILE_BYTES:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_file_path(target_url)
    path.write_bytes(body)


class DevHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def do_OPTIONS(self) -> None:
        if self._proxy_prefix() is not None:
            self.send_response(204)
            for k, v in cors_headers(self):
                self.send_header(k, v)
            self.end_headers()
            return
        super().do_OPTIONS()

    def do_GET(self) -> None:
        prefix = self._proxy_prefix()
        if prefix is not None:
            self._proxy_get(prefix, PROXIES[prefix])
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        prefix = self._proxy_prefix()
        if prefix is not None:
            self._proxy_get(prefix, PROXIES[prefix], head_only=True)
            return
        super().do_HEAD()

    def _proxy_prefix(self) -> Optional[str]:
        path = self.path.split("?", 1)[0]
        for prefix in PROXIES:
            if path == prefix or path.startswith(prefix + "/"):
                return prefix
        return None

    def _proxy_get(self, prefix: str, upstream: str, head_only: bool = False) -> None:
        path, _, query = self.path.partition("?")
        suffix = path[len(prefix) :]
        target = upstream.rstrip("/") + suffix + ("?" + query if query else "")
        use_cache = prefix == "/s3-proxy" and "Range" not in self.headers

        if use_cache and not head_only:
            cached = read_cache(target)
            if cached is not None:
                self.send_response(200)
                self.send_header("Content-Type", self._guess_type(suffix))
                for k, v in cors_headers(self):
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(cached)))
                self.send_header("X-Tile-Cache", "HIT")
                self.end_headers()
                self.wfile.write(cached)
                return

        fwd_headers = {
            "User-Agent": self.headers.get(
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ),
            "Accept": self.headers.get("Accept", "*/*"),
        }
        for name in ("Authorization", "Referer", "Range"):
            val = self.headers.get(name)
            if val:
                fwd_headers[name] = val
        req = urllib.request.Request(target, method="HEAD" if head_only else "GET", headers=fwd_headers)
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=120) as resp:
                body = b"" if head_only else resp.read()
                self.send_response(resp.status)
                skip = {"transfer-encoding", "connection", "content-encoding", "content-length"}
                for key, val in resp.headers.items():
                    if key.lower() in skip:
                        continue
                    self.send_header(key, val)
                for k, v in cors_headers(self):
                    self.send_header(k, v)
                if use_cache and not head_only:
                    write_cache(target, body)
                    self.send_header("X-Tile-Cache", "MISS")
                if not head_only:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            for k, v in cors_headers(self):
                self.send_header(k, v)
            self.send_header("Content-Type", e.headers.get("Content-Type", "text/plain"))
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            msg = json.dumps({"error": repr(e), "target": target}).encode()
            self.send_response(502)
            for k, v in cors_headers(self):
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    @staticmethod
    def _guess_type(suffix: str) -> str:
        low = suffix.lower()
        if low.endswith(".json"):
            return "application/json"
        if low.endswith(".pnts"):
            return "application/octet-stream"
        if low.endswith(".b3dm"):
            return "application/octet-stream"
        return "application/octet-stream"


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    httpd = http.server.ThreadingHTTPServer(("", port), DevHandler)
    print(f"Serving compare on http://localhost:{port}/")
    print(f"  TiTiler proxy: http://localhost:{port}/titiler-proxy/")
    print(f"  CTOD proxy:    http://localhost:{port}/ctod-proxy/")
    print(f"  S3 proxy:      http://localhost:{port}/s3-proxy/  (cache: {CACHE_DIR.name}/)")
    print("Hub:")
    print(f"  http://localhost:{port}/")
    print("Survey viewer:")
    print(f"  http://localhost:{port}/globe_polygons.html")
    print("Tip: point cloud loads fastest from ./pointcloud_3dtiles/ (local)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

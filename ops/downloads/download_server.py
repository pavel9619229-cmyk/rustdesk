from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path("/opt/masha-downloads")
FILE_NAME = "masha-stage-1.8-windows-x64-e93bf0d15.zip"
SHA256 = "DF86C4D9C970AB65DBA5DBB00852E63E414FFAC298EA5A0D43A1D124189820E1"


class DownloadHandler(BaseHTTPRequestHandler):
    server_version = "MashaDownload/1.0"

    def do_HEAD(self):
        self._send_file(send_body=False)

    def do_GET(self):
        self._send_file(send_body=True)

    def _send_file(self, send_body):
        requested = unquote(urlparse(self.path).path).lstrip("/")
        if requested != FILE_NAME:
            self.send_error(404)
            return

        file_path = ROOT / FILE_NAME
        if not file_path.is_file():
            self.send_error(404)
            return

        size = file_path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{FILE_NAME}"')
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("ETag", f'"sha256:{SHA256}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not send_body:
            return

        with file_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 80), DownloadHandler)
    server.serve_forever()

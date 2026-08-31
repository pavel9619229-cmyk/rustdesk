from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path("/opt/masha-downloads")
FILES = {
    "masha-stage-1.8-windows-x64-e93bf0d15.zip":
        "DF86C4D9C970AB65DBA5DBB00852E63E414FFAC298EA5A0D43A1D124189820E1",
    "masha-build-69-stage-2.0-windows-x64-63271488f.zip":
        "0B256931D01D77D958BD3FEE957A9CE982615026729103AFCE9CC2E48B32CB7C",
    "masha-stage-2.0-frontend-windows-x64-63271488f.zip":
        "0B256931D01D77D958BD3FEE957A9CE982615026729103AFCE9CC2E48B32CB7C",
    "masha-build-72-windows-x64-b28e77862.zip":
        "2B4121446EBAF35AFE167DB03A0CAACCAD349F686C75997D8A553241A0ABBE90",
}


class DownloadHandler(BaseHTTPRequestHandler):
    server_version = "MashaDownload/1.0"

    def do_HEAD(self):
        self._send_file(send_body=False)

    def do_GET(self):
        self._send_file(send_body=True)

    def _send_file(self, send_body):
        requested = unquote(urlparse(self.path).path).lstrip("/")
        sha256 = FILES.get(requested)
        if sha256 is None:
            self.send_error(404)
            return

        file_path = ROOT / requested
        if not file_path.is_file():
            self.send_error(404)
            return

        size = file_path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{requested}"')
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("ETag", f'"sha256:{sha256}"')
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

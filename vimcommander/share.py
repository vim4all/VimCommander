""":share: serve files to a phone (or any device) on the local network, and take
uploads from it. Pure Python, no Qt; sharedialog.py shows the QR code.

Every URL starts with a random token, so only someone who has the link (the QR
code) can reach the files. Files are streamed; a folder is sent as a zip made
on the fly. Uploads are plain HTTP PUTs, one per file, from the page's script.
"""

import html
import json
import os
import secrets
import shutil
import socket
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK = 256 * 1024


def lan_addresses() -> list[str]:
    """This computer's IPv4 addresses, the one with the default route first."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # sends nothing; picks the outgoing interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
            if addr not in found and not addr.startswith("127."):
                found.append(addr)
    except OSError:
        pass
    return found or ["127.0.0.1"]


def safe_name(name: str) -> str:
    """A file name from an upload, without folders or characters Windows refuses."""
    name = os.path.basename(name.replace("\\", "/")).strip().strip(".")
    name = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name)
    return name[:200] or "upload"


def unique_path(folder: str, name: str) -> str:
    path = os.path.join(folder, name)
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.lexists(path):
        path = os.path.join(folder, f"{stem} ({i}){ext}")
        i += 1
    return path


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VimCommander share</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0 auto; padding: 16px; max-width: 640px; }}
h1 {{ font-size: 1.3em; }} a.item {{ display: block; padding: 12px; margin: 6px 0; border-radius: 8px;
background: #2f6fd014; color: inherit; text-decoration: none; word-break: break-all; }}
a.item small {{ color: #888; }} .box {{ border: 2px dashed #2f6fd0; border-radius: 8px; padding: 16px; margin-top: 18px; }}
button, label.pick {{ font-size: 1em; padding: 10px 16px; border-radius: 8px; border: 0; background: #2f6fd0;
color: #fff; display: inline-block; }} input[type=file] {{ display: none; }}
.row {{ margin: 6px 0; font-size: .9em; word-break: break-all; }} progress {{ width: 100%; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #1e1e1e; color: #ddd; }} }}
</style></head><body>
<h1>{title}</h1>
{items}
{upload}
<script>
const files = document.getElementById("files");
if (files) files.onchange = async () => {{
  for (const f of files.files) {{
    const row = document.createElement("div"); row.className = "row";
    row.innerHTML = f.name + '<progress max="1" value="0"></progress>';
    document.getElementById("log").appendChild(row);
    const bar = row.querySelector("progress");
    await new Promise(done => {{
      const x = new XMLHttpRequest();
      x.open("PUT", "u/" + encodeURIComponent(f.name));
      x.upload.onprogress = e => {{ if (e.lengthComputable) bar.value = e.loaded / e.total; }};
      x.onload = () => {{ row.firstChild.textContent = (x.status == 200 ? "✓ " : "✗ ") + f.name; bar.value = 1; done(); }};
      x.onerror = () => {{ row.firstChild.textContent = "✗ " + f.name; done(); }};
      x.send(f);
    }});
  }}
  files.value = "";
}};
</script></body></html>"""


class ShareServer:
    """Serves `items` (files or folders) and, if `upload_dir` is set, takes uploads into it.
    `on_event(text)` is called (from server threads) for each download and upload."""

    def __init__(self, items: list[str], upload_dir: str | None, port: int = 0, on_event=None):
        self.items = [os.path.abspath(p) for p in items]
        self.upload_dir = upload_dir
        self.token = secrets.token_urlsafe(9)
        self.on_event = on_event or (lambda text: None)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # no console output

            def do_GET(self):
                server._get(self)

            def do_PUT(self):
                server._put(self)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, address: str) -> str:
        return f"http://{address}:{self.port}/{self.token}/"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # Requests

    def _route(self, handler) -> list[str] | None:
        path = urllib.parse.urlsplit(handler.path).path
        parts = path.split("/")
        if len(parts) < 3 or parts[1] != self.token:
            handler.send_error(404)
            return None
        return [urllib.parse.unquote(p) for p in parts[2:]]

    def _get(self, h) -> None:
        parts = self._route(h)
        if parts is None:
            return
        if parts == [""]:
            self._page(h)
        elif len(parts) >= 2 and parts[0] in ("f", "z") and parts[1].isdigit() and int(parts[1]) < len(self.items):
            path = self.items[int(parts[1])]
            try:
                if os.path.isdir(path):
                    self._send_zip(h, path)
                else:
                    self._send_file(h, path)
            except (OSError, ConnectionError):
                pass
        else:
            h.send_error(404)

    def _page(self, h) -> None:
        rows = []
        for i, path in enumerate(self.items):
            name = os.path.basename(path.rstrip("/\\")) or path
            if os.path.isdir(path):
                link, what = f"z/{i}/{urllib.parse.quote(name)}.zip", "folder, as a zip"
            else:
                try:
                    what = _size(os.path.getsize(path))
                except OSError:
                    what = "missing"
                link = f"f/{i}/{urllib.parse.quote(name)}"
            rows.append(f'<a class="item" href="{link}">⬇ {html.escape(name)} <small>{what}</small></a>')
        upload = ""
        if self.upload_dir:
            upload = ('<div class="box"><b>Send files to this computer</b><p>They go into '
                      f'<code>{html.escape(self.upload_dir)}</code>.</p>'
                      '<label class="pick">Choose files…<input id="files" type="file" multiple></label>'
                      '<div id="log"></div></div>')
        title = f"{len(self.items)} shared item(s)" if self.items else "Send files"
        body = PAGE.format(title=title, items="".join(rows), upload=upload).encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    @staticmethod
    def _disposition(name: str) -> str:
        ascii_name = name.encode("ascii", "replace").decode().replace('"', "_")
        return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}"

    def _send_file(self, h, path: str) -> None:
        size = os.path.getsize(path)
        name = os.path.basename(path)
        with open(path, "rb") as f:
            h.send_response(200)
            h.send_header("Content-Type", "application/octet-stream")
            h.send_header("Content-Length", str(size))
            h.send_header("Content-Disposition", self._disposition(name))
            h.end_headers()
            shutil.copyfileobj(f, h.wfile, CHUNK)
        self.on_event(f"Sent {name} ({_size(size)}) to {h.client_address[0]}")

    def _send_zip(self, h, folder: str) -> None:
        name = os.path.basename(folder.rstrip("/\\")) or "folder"
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Disposition", self._disposition(name + ".zip"))
        h.end_headers()  # no length: HTTP/1.0, the zip ends when the connection closes
        with zipfile.ZipFile(h.wfile, "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(folder):
                for f in files:
                    path = os.path.join(root, f)
                    try:
                        z.write(path, os.path.join(name, os.path.relpath(path, folder)))
                    except OSError:
                        pass
        self.on_event(f"Sent {name}.zip to {h.client_address[0]}")

    def _put(self, h) -> None:
        parts = self._route(h)
        if parts is None:
            return
        if not self.upload_dir or len(parts) != 2 or parts[0] != "u":
            h.send_error(403 if parts and parts[0] == "u" else 404)
            return
        try:
            length = int(h.headers.get("Content-Length", ""))
        except ValueError:
            h.send_error(411)
            return
        dest = unique_path(self.upload_dir, safe_name(parts[1]))
        tmp = f"{dest}.{secrets.token_hex(4)}.vcpart"  # uploads of the same name don't clash
        try:
            with open(tmp, "wb") as f:
                left = length
                while left > 0:
                    chunk = h.rfile.read(min(CHUNK, left))
                    if not chunk:
                        raise ConnectionError("upload cut off")
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, unique_path(self.upload_dir, os.path.basename(dest)))
        except (OSError, ConnectionError) as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            self.on_event(f"Upload of {os.path.basename(dest)} failed: {e}")
            try:
                h.send_error(500)
            except OSError:
                pass
            return
        body = json.dumps({"ok": True}).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
        self.on_event(f"Received {os.path.basename(dest)} ({_size(length)}) from {h.client_address[0]}")

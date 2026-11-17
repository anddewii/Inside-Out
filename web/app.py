"""Serve the relief explorer and fetch its newest verified release from Google Drive."""

import argparse
from functools import partial
from hashlib import md5
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

WEB_DIR = Path(__file__).resolve().parent
DATA_FOLDER = "InsideOut_GEBCO"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
MAX_BYTES = 2_000_000
RELEASE_PATTERN = re.compile(r"terrain-(\d{8}T\d{6}Z-[0-9a-f]{8})\.json")
ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/plotly.min.js": ("plotly.min.js", "text/javascript; charset=utf-8"),
}


def validate_payload(raw, release_id=None):
    if len(raw) > MAX_BYTES:
        raise ValueError("The web data exceeds the 2 MB limit.")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict):
        raise ValueError("Invalid web grid metadata.")
    meta = data["metadata"]
    if (meta.get("schema_version") != 1 or meta.get("dataset") != "GEBCO_2026"
            or not isinstance(meta.get("region"), str)
            or not 1 <= len(meta["region"]) <= 100
            or (release_id and meta.get("run_id") != release_id)):
        raise ValueError("Unsupported or mismatched web grid metadata.")

    for axis, limit in (("longitude", 180), ("latitude", 90)):
        coords = data.get(axis)
        if (not isinstance(coords, list) or not 2 <= len(coords) <= 360
                or any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > limit for v in coords)
                or any(a >= b for a, b in zip(coords, coords[1:]))):
            raise ValueError(f"Invalid {axis} coordinates.")

    cols, rows = len(data["longitude"]), len(data["latitude"])
    grid = data.get("elevation_m")
    if (meta.get("web_shape") != [rows, cols] or not isinstance(grid, list) or len(grid) != rows
            or any(not isinstance(row, list) or len(row) != cols for row in grid)):
        raise ValueError("The elevation grid does not match its coordinates.")
    ocean = [v for row in grid for v in row if v is not None]
    if not ocean or any(type(v) is not int or not -12000 <= v < 0 for v in ocean):
        raise ValueError("Invalid underwater elevation values.")
    if meta.get("web_deepest_m") != min(ocean):
        raise ValueError("The grid does not match its reported minimum.")
    native = meta.get("native_deepest_m")
    if type(native) is not int or not -12000 <= native <= meta["web_deepest_m"]:
        raise ValueError("Invalid native minimum.")
    if not any(all(grid[i + di][j + dj] is not None for di, dj in ((0, 0), (0, 1), (1, 0), (1, 1)))
               for i in range(rows - 1) for j in range(cols - 1)):
        raise ValueError("The grid has no complete ocean surface cell.")
    return data


def drive_service():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    import httplib2

    client_path = WEB_DIR / "credentials.json"
    token_path = WEB_DIR / "token.json"
    creds = None
    if token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), [SCOPE])
        except ValueError:
            print("The saved OAuth token is invalid; signing in again.")
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            write_atomic(token_path, creds.to_json().encode())
        except RefreshError:
            print("The saved OAuth authorization expired; signing in again.")
            creds = None
    if not creds or not creds.valid:
        if not client_path.is_file():
            raise FileNotFoundError("Place your Google Desktop OAuth credentials.json beside app.py.")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=[SCOPE])
        except ValueError as error:
            raise ValueError("credentials.json must be an OAuth client of type Desktop app.") from error
        creds = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=180)
        write_atomic(token_path, creds.to_json().encode())
    return build("drive", "v3", http=AuthorizedHttp(creds, http=httplib2.Http(timeout=30)),
                 cache_discovery=False)


def find_folder(service):
    folders = service.files().list(
        q=f"'root' in parents and name = '{DATA_FOLDER}' and trashed = false and "
          "mimeType = 'application/vnd.google-apps.folder'",
        fields="nextPageToken,files(id)", pageSize=2,
    ).execute(num_retries=2)
    matches = folders.get("files", [])
    if not matches:
        raise FileNotFoundError(f"{DATA_FOLDER} was not found in My Drive. Run Colab and sign in with the same account.")
    if len(matches) != 1 or folders.get("nextPageToken"):
        raise ValueError(f"Multiple folders are named {DATA_FOLDER}. Pass --folder-id with the intended folder ID.")
    return matches[0]["id"]


def newest_release(service, folder_id):
    page_token = None
    while True:
        page = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false and name contains 'terrain-'",
            orderBy="name desc", fields="nextPageToken,files(id,name,size,md5Checksum)",
            pageToken=page_token, pageSize=100,
        ).execute(num_retries=2)
        for entry in page.get("files", []):
            match = RELEASE_PATTERN.fullmatch(entry["name"])
            if match:
                return entry, match[1]
        page_token = page.get("nextPageToken")
        if not page_token:
            raise FileNotFoundError("No completed terrain release in Drive. Run the final Colab cell.")


def write_atomic(path, raw):
    # A unique temporary file keeps overlapping launches from corrupting the cache.
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        staged = Path(tmp.name)
        tmp.write(raw)
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def load_drive_payload(folder_id, allow_cache):
    cache = WEB_DIR / ".cache" / f"{folder_id or 'my-drive'}.json"
    try:
        service = drive_service()
        resolved_folder = folder_id or find_folder(service)
        entry, release_id = newest_release(service, resolved_folder)
        if not 0 < int(entry.get("size", 0)) <= MAX_BYTES:
            raise ValueError("The newest terrain grid is empty or too large.")
        raw = service.files().get_media(fileId=entry["id"]).execute(num_retries=2)
        if len(raw) != int(entry["size"]):
            raise OSError("The Drive file download was incomplete.")
        if entry.get("md5Checksum") and md5(raw, usedforsecurity=False).hexdigest() != entry["md5Checksum"]:
            raise OSError("The downloaded data failed the Drive checksum.")
        validate_payload(raw, release_id)
    except Exception as error:
        # A cached release is displayed only when explicitly requested.
        if not allow_cache or not cache.is_file():
            raise
        print(f"Drive sync failed: {error}\nOpening the previously verified local release.")
        raw = cache.read_bytes()
        validate_payload(raw)
        return raw, "Local cache · Drive sync failed"

    try:
        write_atomic(cache, raw)
    except OSError as error:
        print(f"Drive data loaded. Could not update the local cache: {error}")
    return raw, "Google Drive"


class TerrainHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, payload, **kwargs):
        self.payload = payload
        super().__init__(*args, **kwargs)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/api/terrain":
            raw, content_type = self.payload, "application/json; charset=utf-8"
        elif route in ROUTES:
            filename, content_type = ROUTES[route]
            raw = (WEB_DIR / "static" / filename).read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store" if route == "/api/terrain" else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--folder-id", help="Optional My Drive project folder ID")
    parser.add_argument("--allow-cache", action="store_true", help="Use a previous release if Drive sync fails")
    parser.add_argument("--local-file", type=Path, help="Open a previously downloaded web grid for offline testing")
    args = parser.parse_args()
    if args.folder_id and not re.fullmatch(r"[A-Za-z0-9_-]{5,150}", args.folder_id):
        parser.error("Pass only the folder ID, the part after /folders/ in its Drive URL.")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 to 65535.")
    if args.local_file and (args.folder_id or args.allow_cache):
        parser.error("--local-file cannot be combined with Drive options.")
    try:
        if args.local_file:
            raw, source = args.local_file.read_bytes(), "Local file"
        else:
            raw, source = load_drive_payload(args.folder_id, args.allow_cache)
        data = validate_payload(raw)
        data["connection"] = source
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        handler = partial(TerrainHandler, payload=payload)
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"{source} | {data['metadata']['region']}", flush=True)
            print(f"Open http://127.0.0.1:{args.port}  (Ctrl+C to stop)", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nWebGIS stopped.")
    except Exception as error:
        print(f"Could not start WebGIS: {error}", file=sys.stderr)
        print("See the README troubleshooting section. Use --port 8001 if the port is busy.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

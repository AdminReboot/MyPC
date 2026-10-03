"""Kiểm tra và cài bản mới của NetWatchdog từ GitHub Releases.

Mỗi bản phát hành (tag vX.Y.Z) có file NetWatchdog-Setup-X.Y.Z.exe do CI build. Cập nhật =
tải file đó về thư mục tạm rồi chạy im lặng; bộ cài tự dừng bản cũ, chép đè và chạy lại.
"""
import json
import os
import re
import subprocess
import tempfile
import urllib.request

from version import __version__

REPO = "AdminReboot/MyPC"
RELEASES_URL = f"https://github.com/{REPO}/releases"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(v):
    """'v1.2.3' → (1, 2, 3). Bản dev/không hợp lệ → (0,)."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", str(v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else (0,)


def is_newer(latest, current=__version__):
    return parse_version(latest) > parse_version(current)


def latest_release(timeout=10):
    """Trả về {version, url, notes, page} của bản mới nhất, hoặc None nếu không có file cài đặt."""
    req = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"NetWatchdog/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    asset = next((a for a in data.get("assets") or []
                  if re.match(r"^NetWatchdog-Setup-.*\.exe$", a.get("name", ""), re.I)), None)
    if not asset:
        return None
    return {"version": data.get("tag_name", "").lstrip("v"), "url": asset["browser_download_url"],
            "notes": data.get("body") or "", "page": data.get("html_url") or RELEASES_URL}


def download(url, progress=None, timeout=30):
    """Tải file cài đặt về thư mục tạm. progress(done_bytes, total_bytes) được gọi trong lúc tải."""
    dest = os.path.join(tempfile.gettempdir(), os.path.basename(url.split("?")[0]))
    req = urllib.request.Request(url, headers={"User-Agent": f"NetWatchdog/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest + ".part", "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(256 * 1024):
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(dest + ".part", dest)
    return dest


def run_installer(path, reopen=True):
    """Chạy bộ cài ở chế độ im lặng. Bộ cài sẽ đóng NetWatchdog đang chạy nên gọi xong hãy thoát."""
    args = [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    if reopen:
        args.append("/reopen=1")
    subprocess.Popen(args, close_fds=True)

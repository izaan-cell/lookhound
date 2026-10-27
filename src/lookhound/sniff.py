"""Decide what a file is and pull out what the models need from it."""
import hashlib
import re
from pathlib import Path

from PIL import Image

from .settings import ICON_CACHE, IS_MAC, IS_WIN

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC = {".heic", ".heif"}
except ImportError:
    HEIC = set()

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico"} | HEIC
DOC_EXT = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".rtf", ".csv", ".html", ".htm"}
CODE_EXT = {
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cs", ".go", ".rs", ".rb", ".php", ".m", ".sh", ".zsh",
    ".ps1", ".bat", ".sql", ".lua", ".dart", ".scala", ".vue", ".svelte", ".css",
    ".scss", ".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".r", ".jl", ".ex",
}
# Settings and data files: worth finding by name and gist, not worth the code model.
DATA_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".css", ".scss"}
APP_EXT = {".app"} if IS_MAC else ({".lnk", ".exe"} if IS_WIN else set())

SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv", "env", "site-packages", "build",
    "dist", "target", "out", "Pods", "DerivedData", "vendor", "bower_components",
}
# Folder-shaped things that aren't really folders.
SKIP_BUNDLE_EXT = {
    ".photoslibrary", ".xcodeproj", ".xcworkspace", ".framework", ".bundle",
    ".musiclibrary", ".tvlibrary", ".imovielibrary", ".lrdata", ".git",
}

MAX_BYTES = 60 * 1024 * 1024
DOC_CHUNK, DOC_CHUNKS = 1000, 6
CODE_CHUNK = 1000  # the code model reads the opening of each file


def _finder_date_added(path):
    """Finder's "Date Added" (when the file landed in its folder), via getattrlist."""
    import ctypes
    import struct

    class AttrList(ctypes.Structure):
        _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                    ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                    ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                    ("forkattr", ctypes.c_uint32)]

    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    request = AttrList(5, 0, 0x10000000)  # ATTR_CMN_ADDEDTIME
    buf = ctypes.create_string_buffer(64)
    if _libc.getattrlist(str(path).encode(), ctypes.byref(request), buf, 64, 0) != 0:
        return None
    length, seconds, nanos = struct.unpack_from("<Iqq", buf.raw)
    return seconds + nanos / 1e9 if length >= 20 and seconds > 0 else None


_libc = None


def added_time(path: Path):
    """When the file arrived on this computer: created, downloaded or copied in.
    On a Mac that's Finder's Date Added; Windows reports creation time as st_ctime."""
    if IS_MAC:
        try:
            added = _finder_date_added(path)
            if added:
                return added
        except Exception:
            pass
    st = path.stat()
    if hasattr(st, "st_birthtime"):
        return st.st_birthtime
    return st.st_ctime if IS_WIN else st.st_mtime


def kind_of(path: Path):
    ext = path.suffix.lower()
    if ext in APP_EXT:
        return "app"
    if ext in IMAGE_EXT:
        return "image"
    if ext in DOC_EXT:
        return "document"
    if ext in CODE_EXT:
        return "code"
    return None


def skip_dir(path: Path):
    name = path.name
    return (
        name.startswith(".")
        or name in SKIP_DIRS
        or path.suffix.lower() in SKIP_BUNDLE_EXT
        or path.suffix.lower() in APP_EXT  # an .app is a result, not a place to look
    )


def _cache_path(path: Path, ext=".jpg"):
    return ICON_CACHE / (hashlib.sha1(str(path).encode()).hexdigest() + ext)


def _chunks(text, size, limit):
    text = re.sub(r"[ \t]+", " ", text).strip()
    return [text[i : i + size] for i in range(0, min(len(text), size * limit), size)] or [""]


def _read_text(path: Path):
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:12])
    if ext == ".docx":
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    if ext == ".pptx":
        from pptx import Presentation

        lines = []
        for slide in Presentation(str(path)).slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    lines.append(shape.text_frame.text)
        return "\n".join(lines)
    raw = path.read_text(errors="ignore")[: DOC_CHUNK * DOC_CHUNKS * 2]
    if ext in {".html", ".htm"}:
        raw = re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", raw, flags=re.S | re.I)
    elif ext == ".rtf":
        raw = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
    return raw


def _readable_name(path: Path):
    return re.sub(r"[_\-.]+", " ", path.stem)


def sniff(path: Path, icon_for=None):
    """Returns (kind, preview, payloads) or None to skip.
    payloads maps a model space to the list of things to embed in it."""
    kind = kind_of(path)
    if kind is None:
        return None
    if kind != "app" and path.stat().st_size > MAX_BYTES:
        return None
    name = _readable_name(path)

    if kind == "image":
        with Image.open(path) as im:
            im.seek(0)
            im = im.convert("RGB")
            thumb = _cache_path(path)
            small = im.copy()
            small.thumbnail((256, 256))
            small.save(thumb, "JPEG", quality=82)
        return kind, str(thumb), {"clip": [small]}

    if kind == "app":
        payloads = {"text": [f"{name} application"]}
        preview = None
        icon_file = icon_for(path) if icon_for else None
        if icon_file:
            with Image.open(icon_file) as im:
                payloads["clip"] = [im.convert("RGB")]
            preview = str(icon_file)
        return kind, preview, payloads

    text = _read_text(path)
    if kind == "document":
        chunks = _chunks(f"{name}\n{text}", DOC_CHUNK, DOC_CHUNKS)
        return kind, text.strip()[:300], {"text": chunks}
    snippet = "\n".join(text.strip().splitlines()[:8])
    head = _chunks(f"# {path.name}\n{text}", CODE_CHUNK, 1)
    if path.suffix.lower() in DATA_EXT:
        return kind, snippet, {"text": head}
    return kind, snippet, {"code": head, "text": [name] + head}

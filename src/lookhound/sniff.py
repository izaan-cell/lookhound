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

# Only small thumbnails are ever made, so Pillow's guard against huge images
# (it refuses anything over ~179 million pixels) would just skip big upscales.
Image.MAX_IMAGE_PIXELS = None

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
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".3gp", ".flv"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aif", ".aiff", ".alac"}
VIDEO_FRAMES = 4  # spread through each video, read by the picture model
# .gz, .bz2 and .xz are usually tarballs; a lone compressed file is found by name.
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".tgz", ".tbz2", ".txz", ".gz", ".bz2", ".xz"}
APP_EXT = {".app"} if IS_MAC else ({".lnk", ".exe"} if IS_WIN else set())

SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv", "site-packages", "Pods",
    "DerivedData", "bower_components",
}
# Generated output, skipped only inside a code project; a folder of your own
# called "build" or "out" is searched like any other.
PROJECT_OUTPUT_DIRS = {"build", "dist", "out", "target", "vendor", "env"}
PROJECT_MARKERS = (
    "package.json", "pyproject.toml", "setup.py", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "Gemfile", "composer.json", "CMakeLists.txt", "Makefile", ".git",
)
# Folder-shaped things that aren't really folders.
SKIP_BUNDLE_EXT = {
    ".photoslibrary", ".xcodeproj", ".xcworkspace", ".framework", ".bundle",
    ".musiclibrary", ".tvlibrary", ".imovielibrary", ".lrdata", ".git",
}

DOC_CHUNK = 1000
# How much of a file the meaning models read, spread across the whole file so
# something deep inside still counts. Exact words are kept for all of it.
DOC_CHUNKS, CODE_TEXT_CHUNKS, CODE_CHUNKS = 12, 8, 2
READ_CAP = 500_000  # characters of text kept for exact-word search
PDF_PAGES = 400


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
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
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
        or (name in PROJECT_OUTPUT_DIRS and any((path.parent / m).exists() for m in PROJECT_MARKERS))
    )


def _cache_path(path: Path, ext=".jpg"):
    return ICON_CACHE / (hashlib.sha1(str(path).encode()).hexdigest() + ext)


def _chunks(text, size, limit):
    text = re.sub(r"[ \t]+", " ", text).strip()
    return [text[i : i + size] for i in range(0, min(len(text), size * limit), size)] or [""]


def _spread(text, size, count):
    """Up to `count` pieces of `size` characters covering the whole text: all of
    it when short, otherwise evenly spaced from the start to the very end."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size * count:
        return _chunks(text, size, count)
    step = (len(text) - size) / (count - 1)
    return [text[round(i * step): round(i * step) + size] for i in range(count)]


def words_of(text):
    """Text for exact-word search, with camelCase and snake_case split so that
    "handle login" finds handleLogin and handle_login."""
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return text if split == text else f"{text}\n{split}"


def _read_text(path: Path):
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:PDF_PAGES])
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
    with open(path, encoding="utf-8", errors="ignore") as f:
        raw = f.read(READ_CAP)  # a big file's first half a million characters
    if ext in {".html", ".htm"}:
        raw = re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", raw, flags=re.S | re.I)
    elif ext == ".rtf":
        raw = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
    return raw


PEEK_FILES, PEEK_BYTES = 4, 64 * 1024  # small text files read from inside an archive
TAR_MAX_MEMBERS = 2000  # a tarball has to be unpacked to list it, so stop early


def _peekable(filename, size):
    ext = Path(filename).suffix.lower()
    return (ext in DOC_EXT - {".pdf", ".docx", ".pptx"} or ext in CODE_EXT) and 0 < size <= PEEK_BYTES


def _hidden(filename):
    return filename.startswith(("__MACOSX/", ".")) or "/." in filename


def _list_zip(path):
    import zipfile

    with zipfile.ZipFile(path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and not _hidden(m.filename)]
        texts = [zf.read(m)[:DOC_CHUNK].decode("utf-8", "ignore")
                 for m in members if _peekable(m.filename, m.file_size)][:PEEK_FILES]
        return [m.filename for m in members], texts


def _list_tar(path):
    import tarfile

    names, texts = [], []
    with tarfile.open(path, "r:*") as tf:
        for m in tf:  # streams through the archive, so it can stop part way
            if len(names) >= TAR_MAX_MEMBERS:
                break
            if not m.isfile() or _hidden(m.name):
                continue
            names.append(m.name)
            if len(texts) < PEEK_FILES and _peekable(m.name, m.size):
                texts.append(tf.extractfile(m).read(DOC_CHUNK).decode("utf-8", "ignore"))
    return names, texts


def _list_7z(path):
    """Names only: reading a file inside a solid 7z means unpacking all before it."""
    import py7zr

    with py7zr.SevenZipFile(path) as z:
        return [f.filename for f in z.list() if not f.is_directory and not _hidden(f.filename)], []


def _list_rar(path):
    """Names only: rarfile reads the listing itself but needs unrar to unpack."""
    import rarfile

    with rarfile.RarFile(path) as r:
        return [i.filename for i in r.infolist() if not i.is_dir() and not _hidden(i.filename)], []


def _sniff_archive(path: Path, name):
    """An archive is found by its name, the names inside it, and (for zips and
    tarballs) the opening of a few small text files it holds. Nothing is
    extracted to disk. One that can't be read (encrypted, damaged, or just a
    gzipped file) is still found by its name."""
    ext = path.suffix.lower()
    reader = {".zip": _list_zip, ".7z": _list_7z, ".rar": _list_rar}.get(ext, _list_tar)
    try:
        inside, texts = reader(path)
    except Exception:
        inside, texts = [], []
    listing = ", ".join(_readable_name(Path(f)) for f in inside[:200])
    chunks = _chunks(f"{name} archive containing {listing}", DOC_CHUNK, 2)
    chunks += [c for t in texts for c in _chunks(t, DOC_CHUNK, 1) if c]
    words = words_of(" ".join([name] + [Path(f).name for f in inside[:5000]] + texts))
    count = len(inside)
    preview = (f"{count} file{'s' if count != 1 else ''}: " + ", ".join(Path(f).name for f in inside[:8])
               if inside else "")
    return "archive", preview, {"text": chunks, "_words": words}


def _clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def _tags(path):
    """Title, artist, album, genre and so on, from any audio or video file's tags."""
    try:
        import mutagen

        media = mutagen.File(path, easy=True)
    except Exception:
        return {}, None
    if media is None:
        return {}, None
    tags = {}
    for key in ("title", "artist", "albumartist", "album", "genre", "date", "comment", "description"):
        value = (media.tags or {}).get(key) if media.tags else None
        if value:
            tags[key] = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    length = getattr(getattr(media, "info", None), "length", None)
    return tags, length


def _cover_art(path):
    """The picture embedded in an audio file (album art), if it has one."""
    import io

    import mutagen

    try:
        media = mutagen.File(path)
    except Exception:
        return None
    if media is None:
        return None
    data = None
    tags = media.tags or {}
    for key in getattr(tags, "keys", lambda: [])():
        if key.startswith("APIC"):  # MP3
            data = tags[key].data
            break
    if data is None and "covr" in tags:  # M4A
        data = bytes(tags["covr"][0])
    if data is None and getattr(media, "pictures", None):  # FLAC
        data = media.pictures[0].data
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _sniff_video(path: Path, name):
    """A few frames spread through the video for the picture model, plus its
    name, tags and length for words. Only those frames are decoded."""
    import av

    frames = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        length = float(container.duration / av.time_base) if container.duration else 0.0
        meta = {k.lower(): v for k, v in (container.metadata or {}).items()}
        for i in range(VIDEO_FRAMES):
            at = length * (i + 0.5) / VIDEO_FRAMES
            try:
                container.seek(int(at / stream.time_base), stream=stream, any_frame=False)
                frame = next(container.decode(stream))
            except Exception:
                continue
            image = frame.to_image()
            image.thumbnail((384, 384))
            frames.append(image.convert("RGB"))
    if not frames:
        raise ValueError("no frames could be read")
    tags, _ = _tags(path)
    about = ", ".join(v for v in [meta.get("title"), tags.get("title"), meta.get("comment"),
                                  tags.get("artist")] if v)
    thumb = _cache_path(path)
    frames[len(frames) // 2].copy().save(thumb, "JPEG", quality=82)
    words = words_of(" ".join([name, path.name, about, "video"]))
    return "video", str(thumb), {"clip": frames, "text": [f"{name} video {about}".strip()], "_words": words,
                                 "_summary": " · ".join(p for p in [_clock(length), about] if p)}


def _sniff_audio(path: Path, name):
    """Sound is found by its name and tags (title, artist, album, genre), and by
    its cover art if it has some."""
    tags, length = _tags(path)
    who = tags.get("artist") or tags.get("albumartist")
    parts = [tags.get("title"), f"by {who}" if who else None,
             f"from {tags['album']}" if tags.get("album") else None, tags.get("genre")]
    about = " ".join(p for p in parts if p)
    payloads = {"text": [f"{name} {about} sound audio".strip()],
                "_words": words_of(" ".join([name, path.name] + list(tags.values())))}
    preview = None
    cover = _cover_art(path)
    if cover is not None:
        cover.thumbnail((256, 256))
        preview = str(_cache_path(path))
        cover.save(preview, "JPEG", quality=82)
        payloads["clip"] = [cover]
    summary = " · ".join(p for p in [_clock(length) if length else None, who, tags.get("album")] if p)
    payloads["_summary"] = summary
    return "audio", preview, payloads


def _readable_name(path: Path):
    return re.sub(r"[_\-.]+", " ", path.stem)


def _in_cloud_only(path: Path):
    """iCloud's "Optimise Storage" leaves a placeholder on disk; reading it would
    start a download, so such files are found by name until they're here."""
    return bool(getattr(path.stat(), "st_flags", 0) & 0x40000000)  # SF_DATALESS


def _descriptive(name):
    """Names like magnific_can-you-bring-minar-pakis are worth reading as words;
    IMG_2041 or DSC00012 are not."""
    return len([w for w in name.split() if len(w) > 2 and w.isalpha()]) >= 3


def sniff(path: Path, icon_for=None):
    """Returns (kind, preview, payloads) or None to skip.
    payloads maps a model space to the list of things to embed in it.
    A file that can't be read is still returned, to be found by its name."""
    kind = kind_of(path)
    if kind is None:
        return None
    name = _readable_name(path)
    # "_by_name" tells the indexer to try reading it again after Lookhound updates.
    by_name = (kind, None, {"text": [f"{name} {kind}"], "_by_name": True,
                            "_words": words_of(f"{name} {path.name}")})
    if kind != "app" and _in_cloud_only(path):
        return by_name
    try:
        found = _sniff(path, kind, name, icon_for)
        found[2].setdefault("_words", words_of(f"{name} {path.name}"))
        return found
    except Exception as exc:
        print(f"[lookhound] read {path.name} by name only: {exc}")
        return by_name


def _sniff(path, kind, name, icon_for):
    # No size limits: only the opening of a big file, an archive's contents list,
    # or a picture's thumbnail is ever kept.

    if kind == "image":
        with Image.open(path) as im:
            im.seek(0)
            if im.format == "JPEG":
                im.draft("RGB", (512, 512))  # decode at a fraction of full size: fast, little memory
            im = im.convert("RGB")
            thumb = _cache_path(path)
            small = im.copy()
            small.thumbnail((256, 256))
            small.save(thumb, "JPEG", quality=82)
        payloads = {"clip": [small]}
        if _descriptive(name):
            payloads["text"] = [name]  # AI tools often put the prompt in the file name
        return kind, str(thumb), payloads

    if kind == "app":
        payloads = {"text": [f"{name} application"]}
        preview = None
        icon_file = icon_for(path) if icon_for else None
        if icon_file:
            with Image.open(icon_file) as im:
                payloads["clip"] = [im.convert("RGB")]
            preview = str(icon_file)
        return kind, preview, payloads

    if kind == "archive":
        return _sniff_archive(path, name)
    if kind == "video":
        return _sniff_video(path, name)
    if kind == "audio":
        return _sniff_audio(path, name)

    text = _read_text(path)[:READ_CAP]
    words = words_of(f"{name}\n{text}")
    if kind == "document":
        chunks = _spread(f"{name}\n{text}", DOC_CHUNK, DOC_CHUNKS)
        return kind, text.strip()[:300], {"text": chunks, "_words": words}
    snippet = "\n".join(text.strip().splitlines()[:8])
    body = f"# {path.name}\n{text}"
    if path.suffix.lower() in DATA_EXT:
        return kind, snippet, {"text": _spread(body, DOC_CHUNK, 4), "_words": words}
    return kind, snippet, {"code": _spread(body, DOC_CHUNK, CODE_CHUNKS),
                           "text": [name] + _spread(body, DOC_CHUNK, CODE_TEXT_CHUNKS), "_words": words}

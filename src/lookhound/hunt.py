"""Turn a query into a ranked list of files.

Each model scores on its own scale (CLIP text-to-image similarities sit around
0.2-0.35, MiniLM's around 0.2-0.8), so raw scores are stretched onto a shared
0-1 range before results from different models are compared."""
from .shelf import Shelf, query_terms

# (floor, ceiling) of "meaningful" similarity per space, measured by hand.
TEXT_QUERY_SCALE = {"clip": (0.21, 0.33), "text": (0.15, 0.70), "code": (0.25, 0.75)}
IMAGE_QUERY_SCALE = {"clip": (0.55, 0.95)}
# A whole document compared with other documents scores much higher than a short query.
LIKE_SCALE = {"text": (0.35, 0.90), "code": (0.45, 0.95)}

FILTERS = {
    "all": None,
    "image": {"image"},
    "document": {"document"},
    "code": {"code"},
    "app": {"app"},
    "archive": {"archive"},
    "video": {"video"},
    "audio": {"audio"},
}
# Exact words: a file whose name holds every word you typed comes first; words
# found in its contents (code, deep in a long document) add on top of meaning.
# Words that name a kind of file ("video", "song", "photo"...) lift that kind,
# so "video" lists your videos and "beach video" prefers videos of beaches.
KIND_TERMS = {
    "video": "video", "videos": "video", "movie": "video", "movies": "video", "clip": "video",
    "clips": "video", "film": "video", "mp4": "video", "mov": "video",
    "song": "audio", "songs": "audio", "music": "audio", "audio": "audio", "sound": "audio",
    "sounds": "audio", "track": "audio", "tracks": "audio", "mp3": "audio", "podcast": "audio",
    "voice": "audio", "memo": "audio",
    "photo": "image", "photos": "image", "picture": "image", "pictures": "image", "image": "image",
    "images": "image", "pic": "image", "pics": "image", "screenshot": "image", "screenshots": "image",
    "zip": "archive", "zips": "archive", "archive": "archive", "archives": "archive", "rar": "archive",
    "app": "app", "apps": "app", "application": "app",
    "pdf": "document", "document": "document", "documents": "document", "doc": "document", "docs": "document",
}
KIND_BONUS = 0.5
NAME_ALL_BONUS = 0.8
NAME_SOME_BONUS = 0.3
WORDS_WEIGHT = 0.45
MIN_SCORE = 0.15
RELATIVE_FLOOR = 0.6


def _stretch(sim, lo, hi):
    return max(0.0, min(1.0, (sim - lo) / (hi - lo)))


class Hunter:
    """Open one per thread (it owns a DB connection)."""

    def __init__(self, brain):
        self.brain = brain
        self.shelf = Shelf()

    def search(self, text="", image=None, kinds=None, limit=60, like=None):
        """Returns (results, still_waking). Models that haven't finished loading
        are skipped rather than waited for, so a search never hangs."""
        with self.brain.urgent():
            return self._search(text, image, kinds, limit, like)

    def _search(self, text, image, kinds, limit, like):
        scores = {}
        waking = False

        def merge(found, lo, hi):
            for fid, sim in found.items():
                scores[fid] = max(scores.get(fid, 0.0), _stretch(sim, lo, hi))

        if image is not None and not self.brain.ready("clip"):
            waking = True
        elif image is not None:
            vec = self.brain.embed_one("clip", image)
            merge(self.shelf.nearest("clip", vec), *IMAGE_QUERY_SCALE["clip"])
        skip = None  # the picked file, shown first
        if like is not None:
            like_path, payloads = like
            for space, items in payloads.items():
                if not self.brain.ready(space):
                    waking = True
                    continue
                for item in items:
                    vec = self.brain.embed_one(space, item)
                    merge(self.shelf.nearest(space, vec), *LIKE_SCALE[space])
            skip = str(like_path)
        text = text.strip()
        if text:
            spaces = ["clip", "text", "code"]
            if kinds and "code" not in kinds:
                spaces.remove("code")
            if kinds and not kinds & {"image", "app", "video", "audio"}:
                spaces.remove("clip")
            waking = waking or not all(self.brain.ready(s) for s in spaces)
            spaces = [s for s in spaces if self.brain.ready(s)]
            for space in spaces:
                vec = self.brain.embed_one(space, text)
                merge(self.shelf.nearest(space, vec), *TEXT_QUERY_SCALE[space])
            for fid, (in_text, in_name) in self.shelf.word_hits(text).items():
                bonus = WORDS_WEIGHT * in_text
                bonus += NAME_ALL_BONUS if in_name == 1 else NAME_SOME_BONUS * in_name
                scores[fid] = scores.get(fid, 0.0) + bonus

        info = self.shelf.describe(scores.keys())
        # Only on Everything: a tab you picked already says which kind you want.
        wanted_kinds = set() if kinds else {KIND_TERMS[t] for t in query_terms(text) if t in KIND_TERMS}
        if wanted_kinds:
            # Bring in files of that kind that didn't match otherwise, then lift them all.
            for fid, kind in self.shelf.of_kinds(wanted_kinds):
                scores.setdefault(fid, 0.0)
            info = self.shelf.describe(scores.keys())
            for fid, row in info.items():
                if row["kind"] in wanted_kinds:
                    scores[fid] += KIND_BONUS
        results = []
        for fid, score in scores.items():
            row = info.get(fid)
            if row and row["path"] == skip:
                score = max(score, 10.0)  # the file you picked is the best match for itself
            if not row or score < MIN_SCORE:
                continue
            if kinds and row["kind"] not in kinds:
                continue
            row["score"] = score
            results.append(row)
        results.sort(key=lambda r: -r["score"])
        terms = query_terms(text)
        just_kinds = bool(wanted_kinds) and all(t in KIND_TERMS for t in terms)  # "video", "songs"
        if just_kinds:
            results = [r for r in results if r["kind"] in wanted_kinds]
        elif results:
            # Drop stragglers that trail far behind the best match.
            floor = results[0]["score"] * RELATIVE_FLOOR
            results = [r for r in results if r["score"] >= floor]
        return results[:limit], waking

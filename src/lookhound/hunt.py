"""Turn a query into a ranked list of files.

Each model scores on its own scale (CLIP text-to-image similarities sit around
0.2-0.35, MiniLM's around 0.2-0.8), so raw scores are stretched onto a shared
0-1 range before results from different models are compared."""
from .shelf import Shelf

# (floor, ceiling) of "meaningful" similarity per space, measured by hand.
TEXT_QUERY_SCALE = {"clip": (0.18, 0.33), "text": (0.15, 0.70), "code": (0.25, 0.75)}
IMAGE_QUERY_SCALE = {"clip": (0.55, 0.95)}

FILTERS = {
    "all": None,
    "image": {"image"},
    "document": {"document"},
    "code": {"code"},
    "app": {"app"},
}
NAME_BONUS = 0.35
MIN_SCORE = 0.1
RELATIVE_FLOOR = 0.6


def _stretch(sim, lo, hi):
    return max(0.0, min(1.0, (sim - lo) / (hi - lo)))


class Hunter:
    """Open one per thread (it owns a DB connection)."""

    def __init__(self, brain):
        self.brain = brain
        self.shelf = Shelf()

    def search(self, text="", image=None, kinds=None, limit=60):
        scores = {}

        def merge(found, lo, hi):
            for fid, sim in found.items():
                scores[fid] = max(scores.get(fid, 0.0), _stretch(sim, lo, hi))

        if image is not None:
            vec = self.brain.embed_one("clip", image)
            merge(self.shelf.nearest("clip", vec), *IMAGE_QUERY_SCALE["clip"])
        text = text.strip()
        if text:
            spaces = ["clip", "text", "code"]
            if kinds and "code" not in kinds:
                spaces.remove("code")
            if kinds and not kinds & {"image", "app"}:
                spaces.remove("clip")
            for space in spaces:
                vec = self.brain.embed_one(space, text)
                merge(self.shelf.nearest(space, vec), *TEXT_QUERY_SCALE[space])
            for fid in self.shelf.name_hits(text):
                scores[fid] = scores.get(fid, 0.0) + NAME_BONUS

        info = self.shelf.describe(scores.keys())
        results = []
        for fid, score in scores.items():
            row = info.get(fid)
            if not row or score < MIN_SCORE:
                continue
            if kinds and row["kind"] not in kinds:
                continue
            row["score"] = score
            results.append(row)
        results.sort(key=lambda r: -r["score"])
        if results:
            # Drop stragglers that trail far behind the best match.
            floor = results[0]["score"] * RELATIVE_FLOOR
            results = [r for r in results if r["score"] >= floor]
        return results[:limit]

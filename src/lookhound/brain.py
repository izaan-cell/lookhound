"""The three embedding models. Each loads on first use and is shared by the
indexer and the search box, so every call goes through a per-model lock."""
import os
import threading
import time
from contextlib import contextmanager

from .settings import MODELS_DIR

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Plain HTTPS downloads resume after a dropped connection; the newer Xet transfer
# can hang forever on a flaky line. Give up on a stalled read and retry instead.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

MODEL_IDS = {
    "clip": "sentence-transformers/clip-ViT-B-32",
    "text": "sentence-transformers/all-MiniLM-L6-v2",
    "code": "jinaai/jina-embeddings-v2-base-code",
}
DIMS = {"clip": 512, "text": 384, "code": 768}
# Smallest first, so plain-words searches work within seconds of opening.
WARM_ORDER = ("text", "clip", "code")
JINA_REVISION = "516f4baf13dec4ddddda8631e019b5737c8bc250"
JINA_CODE_REVISION = "3baf9e3ac750e76e8edd3019170176884695fb94"


def _device():
    """Apple silicon's GPU makes indexing several times faster; elsewhere use the CPU."""
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


class Brain:
    def __init__(self):
        self._models = {}
        self._locks = {name: threading.Lock() for name in MODEL_IDS}
        self._load_locks = {name: threading.Lock() for name in MODEL_IDS}
        # Searches go first: indexing pauses while anyone is searching.
        self._searching = 0
        self._count_lock = threading.Lock()
        self._calm = threading.Event()
        self._calm.set()

    def ready(self, name):
        return name in self._models

    @contextmanager
    def urgent(self):
        with self._count_lock:
            self._searching += 1
            self._calm.clear()
        try:
            yield
        finally:
            with self._count_lock:
                self._searching -= 1
                if not self._searching:
                    self._calm.set()

    def _model(self, name):
        if name not in self._models:
            with self._load_locks[name]:
                if name not in self._models:
                    from sentence_transformers import SentenceTransformer

                    kwargs = {"cache_folder": str(MODELS_DIR), "device": _device()}
                    if name == "code":
                        # Jina runs its own modelling code; pin it so an upstream
                        # change can never slip new code onto users' machines.
                        kwargs["trust_remote_code"] = True
                        kwargs["revision"] = JINA_REVISION
                        kwargs["model_kwargs"] = {"code_revision": JINA_CODE_REVISION}
                    try:
                        # Once downloaded, never touch the network again: no update
                        # checks, no stalls when offline.
                        model = SentenceTransformer(MODEL_IDS[name], local_files_only=True, **kwargs)
                    except Exception:
                        model = SentenceTransformer(MODEL_IDS[name], **kwargs)  # first run
                    if kwargs["device"] == "mps":
                        model.half()  # twice as fast on the GPU, same answers to 4 decimals
                    if name == "code":
                        # Jina allows 8k tokens; a file's opening is plenty and far faster.
                        model.max_seq_length = 256
                    self._models[name] = model
        return self._models[name]

    def warm_up(self, on_step=None, on_retry=None):
        """Load every model, downloading on first run and retrying until it works."""
        for name in WARM_ORDER:
            if on_step:
                on_step(name)
            delay = 5
            while True:
                try:
                    self._model(name)
                    break
                except Exception as exc:
                    print(f"[lookhound] couldn't load {name}, retrying: {exc}")
                    if on_retry:
                        on_retry(name)
                    time.sleep(delay)
                    delay = min(delay * 2, 60)

    def embed(self, name, items, urgent=False):
        """Embed a list of strings (or PIL images for clip) into unit vectors."""
        model = self._model(name)
        if not urgent:
            self._calm.wait()  # indexing steps aside for a search
        with self._locks[name]:
            vectors = model.encode(
                items, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
                show_progress_bar=False,
            )
        return vectors.astype("float32")

    def embed_one(self, name, item):
        return self.embed(name, [item], urgent=True)[0]

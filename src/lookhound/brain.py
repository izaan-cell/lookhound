"""The three embedding models. Each loads on first use and is shared by the
indexer and the search box, so every call goes through a per-model lock."""
import os
import threading

from .settings import MODELS_DIR

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

MODEL_IDS = {
    "clip": "sentence-transformers/clip-ViT-B-32",
    "text": "sentence-transformers/all-MiniLM-L6-v2",
    "code": "jinaai/jina-embeddings-v2-base-code",
}
DIMS = {"clip": 512, "text": 384, "code": 768}
JINA_REVISION = "516f4baf13dec4ddddda8631e019b5737c8bc250"
JINA_CODE_REVISION = "3baf9e3ac750e76e8edd3019170176884695fb94"


class Brain:
    def __init__(self):
        self._models = {}
        self._locks = {name: threading.Lock() for name in MODEL_IDS}
        self._load_lock = threading.Lock()

    def _model(self, name):
        if name not in self._models:
            with self._load_lock:
                if name not in self._models:
                    from sentence_transformers import SentenceTransformer

                    kwargs = {"cache_folder": str(MODELS_DIR), "device": "cpu"}
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
                    if name == "code":
                        # Jina allows 8k tokens; a file's opening is plenty and far faster.
                        model.max_seq_length = 512
                    self._models[name] = model
        return self._models[name]

    def warm_up(self, on_step=None):
        for name in MODEL_IDS:
            if on_step:
                on_step(name)
            self._model(name)

    def embed(self, name, items):
        """Embed a list of strings (or PIL images for clip) into unit vectors."""
        model = self._model(name)
        with self._locks[name]:
            vectors = model.encode(
                items, batch_size=8, normalize_embeddings=True, convert_to_numpy=True
            )
        return vectors.astype("float32")

    def embed_one(self, name, item):
        return self.embed(name, [item])[0]

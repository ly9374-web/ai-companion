"""Prepare the local-only RAG model during first-run setup."""

from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "models" / "rag_embedding_model"
STAGING_DIR = PROJECT_ROOT / "models" / ".rag_embedding_model.download"
MODEL_REPOSITORY = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REQUIRED_FILES = ("modules.json", "model.safetensors", "config.json")


def main() -> None:
    if all((MODEL_DIR / filename).is_file() for filename in REQUIRED_FILES):
        print("[INFO] RAG model already present; keeping local files.")
        return
    if MODEL_DIR.exists():
        raise RuntimeError(
            f"RAG model directory is incomplete: {MODEL_DIR}. "
            "Keep it for inspection or move it aside before retrying."
        )

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    print("[INFO] First run: downloading the RAG model. This may take several minutes.")
    snapshot_download(
        repo_id=MODEL_REPOSITORY,
        local_dir=STAGING_DIR,
        ignore_patterns=["*.bin", "*.h5", "*.ot", "*.msgpack", "*.onnx"],
    )
    missing = [name for name in REQUIRED_FILES if not (STAGING_DIR / name).is_file()]
    if missing:
        raise RuntimeError("RAG model download is incomplete: " + ", ".join(missing))
    STAGING_DIR.rename(MODEL_DIR)
    print("[OK] RAG model is ready.")


if __name__ == "__main__":
    main()

"""Central configuration. Secrets come from a .env file or the environment; never hard-coded."""
from __future__ import annotations
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


def _load_env() -> None:
    # search repo dir and its parent for a .env (the project keeps it one level above the repo)
    for d in (BACKEND_DIR, REPO_DIR, REPO_DIR.parent):
        f = d / ".env"
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
            return


_load_env()

DATASET_DIR = Path(os.getenv("DATASET_DIR", REPO_DIR / "StockSense_AI_Retail_Dataset"))
PGDATA_DIR = BACKEND_DIR / ".pgdata"
UPLOAD_DIR = BACKEND_DIR / "uploads"

# --- LLM (Sarvam, OpenAI-compatible) ---
LLM_API_KEY = os.getenv("llm_api_key") or os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.sarvam.ai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "sarvam-105b")
# Sarvam: reasoning_effort=null turns thinking OFF (~1-2s/call); "low" still thinks thousands of tokens (10-50s).
# Values: off | low | medium | high.  SQL generation can be given its own level.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "off")
SQL_REASONING_EFFORT = os.getenv("SQL_REASONING_EFFORT", "off")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8000"))  # reasoning model: thinking tokens count

# --- NL2SQL limits ---
SQL_MAX_ROWS = 500
SQL_TIMEOUT_MS = 5000
SQL_MAX_CORRECTIONS = 2

# --- RAG ---
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = 384
RAG_TOP_K = 5
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.50"))  # cosine similarity floor

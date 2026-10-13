"""Settings from .env (git-ignored). Secrets are never logged."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def get(name: str, default: str | None = None, required: bool = True) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Missing {name} in .env (see .env.example)")
    return value


def output_dir(stage: str) -> Path:
    path = ROOT / "data" / "output" / stage
    path.mkdir(parents=True, exist_ok=True)
    return path

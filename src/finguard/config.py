from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


@dataclass(slots=True)
class Settings:
    base_dir: Path
    data_dir: Path
    demo_dir: Path
    upload_dir: Path
    runtime_dir: Path
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_completions_path: str = ""
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1200
    llm_trust_env: bool = False
    max_upload_mb: int = 20
    max_files: int = 8
    debug: bool = True

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> Settings:
        root = (base_dir or Path(__file__).resolve().parents[2]).resolve()
        data_dir = Path(os.getenv("FINGUARD_DATA_DIR", str(root / "data"))).resolve()
        return cls(
            base_dir=root,
            data_dir=data_dir,
            demo_dir=data_dir / "demo",
            upload_dir=data_dir / "uploads",
            runtime_dir=data_dir / "runtime",
            # LLM_* is the canonical prefix. OPENAI_* / DEEPSEEK_* are accepted as
            # compatibility aliases so an existing .env does not need renaming.
            llm_base_url=_first_env("LLM_BASE_URL", "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL"),
            llm_api_key=_first_env("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"),
            llm_model=_first_env("LLM_MODEL", "OPENAI_MODEL", "DEEPSEEK_MODEL"),
            llm_completions_path=_first_env("LLM_COMPLETIONS_PATH"),
            llm_timeout_seconds=float(_first_env("LLM_TIMEOUT_SECONDS", default="30")),
            llm_temperature=float(_first_env("LLM_TEMPERATURE", default="0.2")),
            llm_max_tokens=int(_first_env("LLM_MAX_TOKENS", default="1200")),
            llm_trust_env=_env_bool("LLM_TRUST_ENV", False),
            max_upload_mb=int(os.getenv("FINGUARD_MAX_UPLOAD_MB", "20")),
            max_files=int(os.getenv("FINGUARD_MAX_FILES", "8")),
            debug=_env_bool("FINGUARD_DEBUG", True),
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def frontend_dir(self) -> Path:
        return self.base_dir / "design_preview"

    @property
    def store_file(self) -> Path:
        return self.runtime_dir / "store.json"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.demo_dir, self.upload_dir, self.runtime_dir):
            path.mkdir(parents=True, exist_ok=True)

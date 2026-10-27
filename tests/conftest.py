from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finguard.config import Settings
from finguard.main import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def temp_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    demo_dir = data_dir / "demo"
    demo_dir.mkdir(parents=True)
    for source in (ROOT / "data" / "demo").glob("*.csv"):
        shutil.copy2(source, demo_dir / source.name)
    settings = Settings(
        base_dir=ROOT,
        data_dir=data_dir,
        demo_dir=demo_dir,
        upload_dir=data_dir / "uploads",
        runtime_dir=data_dir / "runtime",
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        llm_timeout_seconds=1.0,
        max_upload_mb=20,
        max_files=8,
        debug=True,
    )
    settings.ensure_dirs()
    return settings


@pytest.fixture()
def app(temp_settings: Settings):
    return create_app(temp_settings)


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def demo_dataset(temp_settings: Settings):
    from finguard.demo import build_demo_dataset

    return build_demo_dataset(temp_settings)


@pytest.fixture()
def sample_xlsx(temp_settings: Settings, client) -> Path:
    # The client lifespan ensures the sample file exists.
    from finguard.demo import SAMPLE_FILE_NAME

    return temp_settings.demo_dir / SAMPLE_FILE_NAME

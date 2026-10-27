from __future__ import annotations

import json
from threading import RLock

from pydantic import ValidationError

from finguard.config import Settings
from finguard.models import AnalysisState, Dataset


class Store:
    """Small local store for a single-user classroom demo.

    Demo data is reconstructed at startup. Uploaded datasets and their analyses
    are persisted to a local JSON file so a browser refresh does not lose state.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = RLock()
        self.datasets: dict[str, Dataset] = {}
        self.analyses: dict[str, AnalysisState] = {}
        self.active_analysis_id: str | None = None
        self._load()

    def _load(self) -> None:
        path = self.settings.store_file
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for raw in payload.get("datasets", []):
                dataset = Dataset.model_validate(raw)
                if not dataset.is_demo:
                    self.datasets[dataset.id] = dataset
            for raw in payload.get("analyses", []):
                analysis = AnalysisState.model_validate(raw)
                dataset = self.datasets.get(analysis.dataset_id)
                if dataset is not None:
                    self.analyses[analysis.id] = analysis
            active = payload.get("active_analysis_id")
            if active in self.analyses:
                self.active_analysis_id = active
        except (json.JSONDecodeError, ValidationError, OSError, ValueError):
            # Corrupt local state should not prevent the demo from starting.
            self.datasets = {}
            self.analyses = {}
            self.active_analysis_id = None

    def save(self) -> None:
        with self.lock:
            self.settings.ensure_dirs()
            payload = {
                "datasets": [
                    dataset.model_dump(mode="json")
                    for dataset in self.datasets.values()
                    if not dataset.is_demo
                ],
                "analyses": [
                    analysis.model_dump(mode="json")
                    for analysis in self.analyses.values()
                    if not self._is_demo_analysis(analysis)
                ],
                "active_analysis_id": (
                    self.active_analysis_id
                    if self.active_analysis_id in {
                        analysis_id for analysis_id, analysis in self.analyses.items() if not self._is_demo_analysis(analysis)
                    }
                    else None
                ),
            }
            temp = self.settings.store_file.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.settings.store_file)

    def _is_demo_analysis(self, analysis: AnalysisState) -> bool:
        dataset = self.datasets.get(analysis.dataset_id)
        return bool(dataset and dataset.is_demo)

    def upsert_dataset(self, dataset: Dataset) -> None:
        with self.lock:
            self.datasets[dataset.id] = dataset
            self.save()

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        return self.datasets.get(dataset_id)

    def upsert_analysis(self, analysis: AnalysisState) -> None:
        with self.lock:
            self.analyses[analysis.id] = analysis
            self.save()

    def get_analysis(self, analysis_id: str) -> AnalysisState | None:
        return self.analyses.get(analysis_id)

    def set_active(self, analysis_id: str | None) -> None:
        with self.lock:
            self.active_analysis_id = analysis_id
            self.save()

    def latest_analysis(self) -> AnalysisState | None:
        if self.active_analysis_id and self.active_analysis_id in self.analyses:
            return self.analyses[self.active_analysis_id]
        if not self.analyses:
            return None
        return max(self.analyses.values(), key=lambda analysis: analysis.updated_at)

    def list_analyses(self) -> list[AnalysisState]:
        return sorted(self.analyses.values(), key=lambda analysis: analysis.updated_at, reverse=True)

    def remove_analysis(self, analysis_id: str) -> None:
        with self.lock:
            self.analyses.pop(analysis_id, None)
            if self.active_analysis_id == analysis_id:
                self.active_analysis_id = None
            self.save()

    def remove_dataset(self, dataset_id: str) -> None:
        with self.lock:
            self.datasets.pop(dataset_id, None)
            doomed = [analysis_id for analysis_id, analysis in self.analyses.items() if analysis.dataset_id == dataset_id]
            for analysis_id in doomed:
                self.analyses.pop(analysis_id, None)
            if self.active_analysis_id in doomed:
                self.active_analysis_id = None
            self.save()

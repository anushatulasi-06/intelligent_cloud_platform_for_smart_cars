"""Public provenance for imported demonstration history; never includes local paths/keys."""
from typing import Literal

from pydantic import BaseModel


class HistoricalImportProvenance(BaseModel):
    source: Literal["historical-alerts-v1"]
    prediction_origin: Literal["historical_fixture_not_reinferred"]
    fixture_sha256: str
    audio_sha256: str
    source_object_key: str

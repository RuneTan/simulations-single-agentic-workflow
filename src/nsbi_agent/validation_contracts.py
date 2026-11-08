"""Intent stays open; the evidence envelope has explicit, typed fields."""
from typing import Any, Annotated, Literal
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from nsbi_agent.contracts import StrictModel


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    simulation: dict[str, Any]
    production: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def finite_json(self):
        # Inspect raw values before JSON serialization can turn infinity into null.
        json.dumps({'simulation': self.simulation, 'production': self.production,
                    'analysis': self.analysis, **(self.model_extra or {})}, allow_nan=False)
        return self

    def original(self) -> dict:
        return self.model_dump(mode='json', exclude_unset=True)


FilePath = Annotated[str, Field(min_length=1)]


class CardPaths(StrictModel):
    param_card: FilePath
    run_card: FilePath
    process_card: FilePath
    reweight_card: FilePath | None = None
    banner: FilePath | None = None


class CardValidationRequest(StrictModel):
    request: IntentRequest
    artifacts: CardPaths
    expected_intent_sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')] | None = None
    phase: Literal['configuration', 'run_record'] = 'configuration'
    backend_version: str = '2.9.27'


Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
SHA256 = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class SampleRequest(StrictModel):
    event_file: FilePath
    expected_events: int | None = Field(default=None, gt=0)
    required_weight_ids: list[Text] = Field(default_factory=list)
    run_exit_code: int | None = None
    log_file: FilePath | None = None


class EvidenceRef(StrictModel):
    path: FilePath
    sha256: SHA256 | None = None
    role: Literal['observation', 'validation'] = 'observation'


class ExperienceReview(StrictModel):
    by: Text
    verdict: Literal['accept', 'revise']
    reason: Text


class ExperienceRequest(StrictModel):
    lesson: Text
    applies_to: dict[Text, Text] = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)
    review: ExperienceReview | None = None

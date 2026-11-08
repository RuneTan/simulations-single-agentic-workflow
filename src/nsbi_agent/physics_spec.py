"""Model-source query; legacy execution-oriented schemas are archived."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from nsbi_agent.contracts import StrictModel
ModelID = Annotated[str, Field(min_length=1)]


class ModelQuery(StrictModel):
    model_id: ModelID | None = None
    version: str | None = None
    basis: str | None = None
    flavour_scheme: str | None = None
    ew_input_scheme: str | None = None
    collection: Literal['official', 'tutorial'] = 'official'

    @model_validator(mode='after')
    def reject_null_selection(self):
        for name in self.model_fields_set - {'version'}:
            if getattr(self, name) is None:
                raise ValueError(name + ': omit the field instead of assigning null')
        return self


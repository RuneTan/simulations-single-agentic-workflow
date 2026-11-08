"""Shared result contract; success always refers to the declared check scope."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PackageName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
Version = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnvironmentRequest(StrictModel):
    # Versions are supplied by the caller, never inferred from a tutorial.
    packages: dict[PackageName, Version] = Field(default_factory=dict)
    backends: dict[Literal['madgraph', 'gfortran', 'make'], Version] = Field(default_factory=dict)
    package_names: list[PackageName] = Field(default_factory=list)
    backend_names: list[Literal['madgraph', 'gfortran', 'make']] = Field(default_factory=list)


class Check(StrictModel):
    name: str
    status: Literal["pass", "fail", "not_checked", "unverified"]
    expected: str
    actual: str


class ToolError(StrictModel):
    code: str
    message: str


class ToolResult(StrictModel):
    tool: str
    status: Literal["succeeded", "failed", "needs_review", "needs_configuration", "no_available_model", "unverified"]
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    errors: list[ToolError] = Field(default_factory=list)

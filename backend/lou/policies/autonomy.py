"""Local policy ceiling; deliberately grants no execution or publishing capability."""

from typing import Annotated

from pydantic import ConfigDict, Field

from contracts.models import ContractModel

AutonomyLevel = Annotated[int, Field(ge=0, le=3, strict=True)]


class AutonomyPolicy(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: str = Field(default="local-v1", min_length=1)
    max_autonomy: AutonomyLevel = 2

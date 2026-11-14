from typing import Literal

from pydantic import BaseModel, Field


class EvaluationCriterion(BaseModel):
    name: str
    score: int = Field(ge=0, le=100)
    weight: int = Field(ge=0, le=100)
    feedback: str = ""


class ResponseEvaluation(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    passed: bool
    criteria: list[EvaluationCriterion]
    critical_issues: list[str] = Field(default_factory=list)  # any => fail regardless of score
    revision_instructions: list[str] = Field(default_factory=list)
    judged_by: Literal["rules", "rules+llm"] = "rules"


class JudgeVerdict(BaseModel):
    """LLM judge output, used only for borderline rule scores."""

    meets_query: bool = Field(description="Does the reply correctly and fully answer the user's query using only the evidence?")
    score: int = Field(ge=0, le=100)
    feedback: str = Field(default="", description="What to fix, if anything")

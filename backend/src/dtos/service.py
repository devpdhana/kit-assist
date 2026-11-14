from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiArg(BaseModel):
    name: str
    value: str


class ApiCall(BaseModel):
    api: str = Field(description="API id from the catalog")
    args: list[ApiArg] = Field(default_factory=list)

    def arg_dict(self) -> dict[str, str]:
        return {a.name: a.value for a in self.args}


class ServicePlan(BaseModel):
    """The Service Agent's plan: which catalog APIs to call, in order."""

    calls: list[ApiCall] = Field(default_factory=list, max_length=5)
    missing_info: str = Field(default="", description="If a write needs details the user has not given, the question to ask; otherwise empty")
    reason: str = ""


ServiceStatus = Literal["ok", "error", "needs_info", "blocked", "staged", "executed", "cancelled"]


class ServiceResult(BaseModel):
    status: ServiceStatus
    calls: list[dict[str, Any]] = Field(default_factory=list)  # [{api, args, ok, data|error}]
    pending: dict[str, Any] | None = None  # staged write awaiting confirmation
    policy: dict[str, Any] | None = None  # Policy Guard verdict for writes
    message: str = ""  # human-readable summary (blockers, missing info, execution result)
    error: dict[str, Any] | None = None  # {message, retryable}

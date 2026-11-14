from typing import Literal

from pydantic import BaseModel, Field

AgentName = Literal["service", "knowledge", "response", "evaluation", "human_confirmation", "finish"]


class QueryContext(BaseModel):
    """What the Supervisor understood about the user's query (LLM output)."""

    intent: Literal[
        "account_data",  # read personal data: attendance, timetable, requests, profile, calendar...
        "action_request",  # leave / on-duty / cancel / placement registration / approve / reject / admission application
        "rules_or_documents",  # handbook rules, circulars, fees, scholarships, brochure facts
        "lesson_doubt",  # subject questions answered from lesson notes
        "admissions_info",  # courses, eligibility, seats, fees (visitor questions)
        "small_talk",  # greetings, thanks, "what can you do"
        "out_of_scope",  # unrelated to KIT
    ]
    needs_service: bool = Field(description="True if college system APIs must be called")
    needs_knowledge: bool = Field(description="True if official documents (rules, circulars, brochure, notes) must be searched")
    task: str = Field(description="Self-contained restatement of what the user wants, resolving follow-ups from history, with exact YYYY-MM-DD dates")
    knowledge_query: str = Field(default="", description="Search keywords for the documents, if needs_knowledge")
    reason: str = Field(default="", description="One short sentence on why")


class SupervisorDecision(BaseModel):
    next_agent: AgentName
    reason: str

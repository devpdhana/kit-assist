"""Supervisor-controlled LangGraph for KIT Assist.

        ┌──────────────► supervisor ◄───────────────┐
        │   (understands query, routes, finishes)    │
        │      │        │         │          │       │
     service  knowledge  response  evaluation  human_confirmation
        └────────┴──────────┴──────────┴──────────────┘
                  every worker reports back to the supervisor
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph

# Our Pydantic DTOs stored in checkpoints (explicit allow-list, no pickle).
CHECKPOINT_TYPES = [
    ("src.dtos.supervisor", "QueryContext"),
    ("src.dtos.supervisor", "SupervisorDecision"),
    ("src.dtos.service", "ServiceResult"),
    ("src.dtos.evaluation", "ResponseEvaluation"),
    ("src.dtos.evaluation", "EvaluationCriterion"),
]

from src.workflows.nodes.nodes import (
    evaluation_node,
    human_confirmation_node,
    knowledge_node,
    response_node,
    service_node,
    supervisor_node,
)
from src.workflows.routing.routing import route_from_supervisor
from src.workflows.state.assistant_state import AssistantState


def build_assistant_workflow(checkpointer=None):
    workflow = StateGraph(AssistantState)

    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("service", service_node)
    workflow.add_node("knowledge", knowledge_node)
    workflow.add_node("response", response_node)
    workflow.add_node("evaluation", evaluation_node)
    workflow.add_node("human_confirmation", human_confirmation_node)

    workflow.set_entry_point("supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "service": "service",
            "knowledge": "knowledge",
            "response": "response",
            "evaluation": "evaluation",
            "human_confirmation": "human_confirmation",
            END: END,
        },
    )
    for worker in ("service", "knowledge", "response", "evaluation", "human_confirmation"):
        workflow.add_edge(worker, "supervisor")

    # Thread-scoped memory: one thread per conversation, so a staged action
    # survives until the user's next voice turn answers it.
    if checkpointer is None:
        serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
        checkpointer = MemorySaver(serde=serde)
    return workflow.compile(checkpointer=checkpointer)


assistant_graph = build_assistant_workflow()

from langgraph.graph import END, StateGraph

from app.agents.domain import domain_node
from app.agents.guardrail import guardrail_node
from app.agents.router import router_node
from app.state import ChatState


def build_graph() -> StateGraph:
    graph = StateGraph(ChatState)
    graph.add_node("router", router_node)
    graph.add_node("domain", domain_node)
    graph.add_node("guardrail", guardrail_node)

    graph.set_entry_point("router")
    graph.add_edge("router", "domain")
    graph.add_edge("domain", "guardrail")
    graph.add_edge("guardrail", END)

    return graph


graph = build_graph().compile()

"""
Flowchart Validation

Final structural check on a flowchart before it is serialised to the client.
`study_generator._build_flowchart` already filters model output; this runs
again on the assembled object so that any future code path that constructs a
Flowchart directly still gets the same guarantees.

Invariants enforced: unique node IDs, every edge endpoint resolves to a real
node, no self-loops, no duplicate edges, and hard caps on both counts.
"""

from __future__ import annotations

from app.models.schemas import Flowchart, FlowchartEdge, FlowchartNode

MAX_NODES = 40
MAX_EDGES = 80


def validate_flowchart(flowchart: Flowchart) -> Flowchart:
    """
    Return a structurally sound copy of the flowchart.

    Anything that would render as a broken or dangling connector is dropped
    rather than passed through, so the client renderer never has to guess.
    """
    if not flowchart or not flowchart.nodes:
        return Flowchart()

    seen_ids: set[str] = set()
    valid_nodes: list[FlowchartNode] = []

    for node in flowchart.nodes:
        node_id = (node.id or "").strip()
        if not node_id or node_id in seen_ids:
            continue
        if not (node.label or "").strip():
            continue
        seen_ids.add(node_id)
        valid_nodes.append(node)
        if len(valid_nodes) >= MAX_NODES:
            break

    if not valid_nodes:
        return Flowchart()

    seen_edges: set[tuple[str, str]] = set()
    valid_edges: list[FlowchartEdge] = []

    for edge in flowchart.edges:
        source = (edge.source or "").strip()
        target = (edge.target or "").strip()
        if source not in seen_ids or target not in seen_ids:
            continue
        if source == target:
            continue
        key = (source, target)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        valid_edges.append(edge)
        if len(valid_edges) >= MAX_EDGES:
            break

    return Flowchart(
        title=flowchart.title,
        nodes=valid_nodes,
        edges=valid_edges,
    )

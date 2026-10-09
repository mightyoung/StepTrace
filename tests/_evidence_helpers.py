from display_project.evidence import ProcessEvidenceGraph
from display_project.sop.visual_evidence import VisualEvidenceResult
from display_project.sop.state_machine import Evidence, issue_model_geometry_evidence

def authorized(step):
    graph = ProcessEvidenceGraph().add_node({"id":"o", "kind":"object", "source":"model_geometry"}).add_node({"id":"r", "kind":"relation", "source":"model_geometry", "predicate":"confirmed_step", "value":step})
    visual = VisualEvidenceResult(step, step, 4, (), {})
    return Evidence(deterministic_step=issue_model_geometry_evidence(visual=visual, graph=graph, expected_step=step))

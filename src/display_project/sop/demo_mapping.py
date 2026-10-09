from __future__ import annotations


DEMO_STEPS = (
    "pick_components",
    "align_components",
    "insert_screw",
    "insert_bolt",
    "tighten_fastener",
)


def action_to_step(action_name: str | None, original_frame: int) -> str | None:
    if not action_name:
        return None
    action = action_name.casefold()
    if 150 <= original_frame < 195 and action.startswith("take_"):
        return "pick_components"
    if 195 <= original_frame < 227 and "align" in action:
        return "align_components"
    if 227 <= original_frame < 392 and any(token in action for token in ("screw", "plug_screw")):
        return "insert_screw"
    if 392 <= original_frame < 422 and any(token in action for token in ("bolt", "align")):
        return "insert_bolt"
    if original_frame >= 422 and any(token in action for token in ("tighten", "screw_screw")):
        return "tighten_fastener"
    return None

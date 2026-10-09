from dataclasses import dataclass


@dataclass(frozen=True)
class NegativeEvidence:
    reason: str
    source: str = "deterministic"

    def __post_init__(self):
        if not self.reason:
            raise ValueError("negative evidence requires reason")


def should_abstain(evidence):
    return any(
        isinstance(e, NegativeEvidence)
        or (isinstance(e, dict) and (e.get("negative") or e.get("abstain")))
        for e in evidence
    )

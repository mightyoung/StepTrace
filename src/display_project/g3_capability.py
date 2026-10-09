"""Stable public API for the non-authorizing G3 precheck."""

from display_project.g3_precheck import (
    DOWNSTREAM_STAGE_ALIASES,
    G3_CAPABILITY_SCHEMA,
    MECHANISM_RESULT_SCHEMA,
    METHOD_MANIFEST_SCHEMA,
    OPERATING_POINTS_SCHEMA,
    PAIRED_SUPPORT_SCHEMA,
    THRESHOLDS_SCHEMA,
    build_g3_capability_artifact,
    canonical_sha256,
    normalize_downstream_stage,
    require_g3_capability,
    validate_g3_capability,
    write_g3_capability,
)

__all__ = [
    "DOWNSTREAM_STAGE_ALIASES",
    "G3_CAPABILITY_SCHEMA",
    "MECHANISM_RESULT_SCHEMA",
    "METHOD_MANIFEST_SCHEMA",
    "OPERATING_POINTS_SCHEMA",
    "PAIRED_SUPPORT_SCHEMA",
    "THRESHOLDS_SCHEMA",
    "build_g3_capability_artifact",
    "canonical_sha256",
    "normalize_downstream_stage",
    "require_g3_capability",
    "validate_g3_capability",
    "write_g3_capability",
]

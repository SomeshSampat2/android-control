"""Configuration for the Jev (TypeSafe System One) decision layer."""

ENV_API_KEY = "TYPESAFE_API_KEY"
ENV_BASE_URL = "TYPESAFE_BASE_URL"
ENV_MODEL = "TYPESAFE_DEFAULT_MODEL"
ENV_MAX_ELEMENTS = "JEV_MAX_ELEMENTS"

DEFAULT_MODEL = "jev-latest"
DEFAULT_MAX_ELEMENTS = 40

# Noul probability at or above which a "does a match exist" answer counts as yes.
PRESENCE_THRESHOLD = 0.5

# Noul probability at or above which the goal is treated as already achieved.
GOAL_DONE_THRESHOLD = 0.7

# Verdict bands for judge(): noul >= VERDICT_YES -> "yes",
# noul <= VERDICT_NO -> "no", anything between -> "uncertain".
VERDICT_YES = 0.6
VERDICT_NO = 0.4

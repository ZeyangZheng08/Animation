"""
keys.py — where the OpenAI key comes from. One place, because the repo split left it behind.

`key.env` sits in the UNITY repository root, not here: it predates the 2026-08-05 split and stayed with
the tree it was written into. Rather than move a secret across a boundary, this looks in both places and
derives the Unity-side location from the configured KB path, which is already the one thing that knows
where that repository is.

The key is never logged, never put in an error message, and never written anywhere.
"""
import os

import paths

ENV_VAR = "OPENAI_API_KEY"
FILENAME = "key.env"


def candidate_paths():
    """Search order: this repo, then the Unity repository (KB_DIR is <unity>/agent/animation_knowledge_base)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    unity_root = os.path.dirname(os.path.dirname(paths.KB_DIR))
    return [os.path.join(here, FILENAME), os.path.join(unity_root, FILENAME)]


def load_openai_key():
    key = os.environ.get(ENV_VAR)
    if key and key.strip():
        return key.strip()

    for path in candidate_paths():
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(ENV_VAR + "="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value

    raise SystemExit(
        "%s is not set and no %s was found.\nLooked in:\n  %s"
        % (ENV_VAR, FILENAME, "\n  ".join(candidate_paths())))

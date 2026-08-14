"""
tools — what the model is allowed to do, and the discipline for handing results back.

Two error severities, borrowed from Codex's `FunctionCallError`, because the distinction is what keeps a
ReAct turn alive:

    ToolFailure   the tool ran and could not do the thing. Becomes an ORDINARY tool result with
                  success=false, which the model reads and reacts to inside the same turn. Unknown
                  action_id, unresolvable scene object, a failed gate — all of these are conversation,
                  not crashes.
    ToolFatal     the tool cannot run at all: transport dead, protocol version mismatch. Ends the turn.

Getting this backwards is the classic ReAct failure: a tool raises on bad input, the turn dies, and the
model never gets the chance to correct a mistake it was perfectly capable of correcting.
"""
from .registry import ToolFailure, ToolFatal, ToolRegistry, ToolSpec

__all__ = ["ToolFailure", "ToolFatal", "ToolRegistry", "ToolSpec"]

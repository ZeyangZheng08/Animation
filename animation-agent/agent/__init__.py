"""
agent — the runtime service: a single ReAct agent that turns natural language into character motion.

Layout:
    protocol.py   the typed message contract with the engine executor (mirrored in Protocol.cs)
    engine.py     the runtime channel — a WebSocket server the engine connects into
"""

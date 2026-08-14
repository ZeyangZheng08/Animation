"""Protocol conformance. These are cheap and they are the ones that catch drift between the Python
contract and Protocol.cs, which otherwise surfaces much later as a wrong pose in the scene."""
import pytest

from agent import protocol as P


def test_request_response_event_are_distinguishable():
    req = P.request(P.T.SCENE_FIND, {"category": "medication"}, "r1")
    assert P.classify(req) == "request"
    assert P.classify(P.ok("r1", {"objects": []})) == "response"
    assert P.classify(P.err("r1", P.E.NOT_FOUND, "nope")) == "response"
    assert P.classify(P.event(P.T.MOTION_STATUS, {"phase": "started"})) == "event"


def test_validate_accepts_every_well_formed_shape():
    for msg in (P.request(P.T.SCENE_ANCHORS, {}, "r1"),
                P.ok("r1", {"anchors": []}),
                P.err("r1", P.E.NOT_READY, "not in play mode"),
                P.event(P.T.ENGINE_HELLO, {"scene": "EmergencyRoom"})):
        P.validate(msg)


def test_version_mismatch_is_fatal():
    """A demo that silently half-speaks an old protocol is worse than one that refuses to start."""
    msg = P.ok("r1", {})
    msg["v"] = PROTOCOL_VERSION_FROM_THE_FUTURE = P.PROTOCOL_VERSION + 1
    with pytest.raises(P.ProtocolError, match="out of step"):
        P.validate(msg)


@pytest.mark.parametrize("msg, match", [
    ({"v": P.PROTOCOL_VERSION, "ok": True, "data": {}}, "without an id"),
    ({"v": P.PROTOCOL_VERSION, "id": "r1", "ok": False, "err": {"code": "x"}}, "err.code and err.msg"),
    ({"v": P.PROTOCOL_VERSION, "type": "scene.teleport", "data": {}}, "unknown event type"),
    ({"v": P.PROTOCOL_VERSION, "id": "r1", "type": "scene.teleport", "params": {}}, "unknown request"),
    ({"v": P.PROTOCOL_VERSION, "id": "r1", "type": P.T.SCENE_FIND, "params": []}, "not an object"),
    ("just a string", "not an object"),
])
def test_validate_rejects_malformed(msg, match):
    with pytest.raises(P.ProtocolError, match=match):
        P.validate(msg)


def test_type_sets_do_not_overlap():
    """A type that is both a request and an event would make `classify` ambiguous for the engine."""
    assert not (P.REQUEST_TYPES & P.EVENT_TYPES)


def test_constructors_refuse_the_wrong_direction():
    with pytest.raises(P.ProtocolError):
        P.request(P.T.MOTION_STATUS, {}, "r1")     # an event type is not requestable
    with pytest.raises(P.ProtocolError):
        P.event(P.T.SCENE_FIND, {})                # a request type is not an event

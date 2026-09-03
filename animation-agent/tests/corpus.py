"""The corpus ids the tests stand on, and a small store built out of them.

WHY NAMED CONSTANTS RATHER THAN LITERALS. Every test in this suite used to name one of the eight
nursing actions, and each name carried its own meaning: `walking` WAS the walk cycle, `idle` WAS the
stance, and a reader could tell what a case was about from the id. The library is 2446 general Mixamo
clips now, and `mx_Picking_Up_An_Object_With_One_Hand` says what it animates without saying why a
test picked it. So the id is bound to the PROPERTY it is here for, once, and the tests read the
property.

Every one of these was checked against the knowledge base and the posture sidecar when it was chosen;
`test_corpus_fixtures.py` re-checks them, so a corpus change that invalidates one fails as a small
explicit test rather than as a dozen puzzling assertions elsewhere.
"""
import glob
import os
import shutil

import paths

# ---- the runtime primitives, and the trap next to one of them -------------------------------
WALK = "mx_Walking_Forward"          # standing throughout, root + both legs dynamic, a real gait cycle
IDLE = "mx_Standing_Idle"            # standing, root static, only the arms and hands stirring
POSE = "mx_Walking"                  # a TWO-FRAME pose asset. Named `mx_Walking`, animates nothing:
                                     # the trap the primitive check exists to catch.

# ---- one clip per coarse posture ------------------------------------------------------------
SEATED = "mx_Aim_Pistol_While_Sitting"                    # seated start to finish
FLOOR = "mx_Crawling_Forward_On_Hands_And_Knees"          # floor start to finish
OTHER = "mx_Kneeling_Idle"                                # kneeling: the conservative fallback state

# ---- clips that CROSS a posture, which is what a seam has to reason about --------------------
SIT_DOWN = "mx_Standing_To_Sitting_Transition"            # standing -> seated, one boundary
STAND_UP = "mx_Sitting_To_Standing_2"                     # seated -> standing, one boundary
FALL = "mx_Death_Falling_Forwards"                        # standing -> floor

# ---- clips picked for what they do to a channel partition -----------------------------------
GRAB = "mx_Picking_Up_An_Object_With_One_Hand"            # a one-handed reach, root dynamic
CHEST = "mx_Administering_Cpr_To_A_Victim_On_The_Ground"  # the top hit for "chest compressions"
CYCLIC = "mx_Run_While_Reloading_Rifle"                   # legs and root carry a measured period
GIVING = "mx_Reaching_Out_Gesture"                        # a two-armed reach over its own legs
WORK = "mx_Taking_An_Item_And_Examining_It"               # standing, both hands busy: a base that
                                                          # DOES something rather than travelling
JUMP = "mx_Cross_Jumps"                                   # leaves the ground and lands

# The store a small-library fixture copies. Twelve records: a walk, a stance, one clip for each of the
# four coarse postures, both directions of a posture change, a fall, a one-handed reach, a repeating
# clip and a pose asset. Small enough that a search over it ranks predictably and large enough that
# every filter has something to keep and something to drop.
SMALL_STORE = (WALK, IDLE, POSE, SEATED, FLOOR, OTHER, SIT_DOWN, STAND_UP, FALL, GRAB, CHEST, CYCLIC)

# Every standing clip named here, which is what a test combining two actions has to draw from: an
# overlay and its base must share a posture, so a substitution that reached for a seated or a floor
# clip would be testing the posture gate rather than whatever it meant to test.
STANDING = (WALK, IDLE, POSE, GRAB, GIVING, WORK, CYCLIC, JUMP)

# What must never appear in anything the agent can see. The eight nursing records left the knowledge
# base for `agent/nursing_assets/`; these are their action_ids, their clip names and the words their
# descriptions were full of.
NURSING_TOKENS = ("nurse", "bvm", "check_pulse", "giving_pills", "grab_bottle", "walk_n",
                  "nurse_cpr_30", "nurse_give_meds", "nurse_grab_aspirin", "aspirin")


def copy_store(tmp_path, action_ids=SMALL_STORE):
    """Copy those records into a temporary `actions/` directory and return its path.

    ONLY THE RECORDS ARE COPIED, and that is deliberate. The ids are REAL, so the raw dumps, the
    segment table and the posture sidecar in the knowledge base already answer about them — a fixture
    that copied those too would be maintaining a second corpus in order to test the first. What the
    temporary store gives is a small INDEX: a search over twelve documents ranks predictably, where
    the same query over 2446 depends on what else happens to be in the library.

    `KBIndex.load(actions_dir=...)` reads a directory rather than the manifest, so no manifest is
    written here.
    """
    store = os.path.join(str(tmp_path), "actions")
    os.makedirs(store, exist_ok=True)
    for action_id in action_ids:
        source = os.path.join(paths.ACTIONS_DIR, action_id + ".json")
        if not os.path.isfile(source):
            raise AssertionError(
                "tests/corpus.py names %s, which is not in the knowledge base at %s"
                % (action_id, paths.rel(paths.ACTIONS_DIR)))
        shutil.copyfile(source, os.path.join(store, action_id + ".json"))
    return store


def has_nursing_content(text):
    """Any nursing token in a blob of text, case-insensitively.

    `nurse_avatar.fbx` IS EXPECTED and is not one. It is the calibration rig all 2446 clips were
    sampled on, so it appears in every record's provenance block; what it names is a skeleton, not a
    motion. Callers that search a whole record strip it first — see the isolation tests.
    """
    folded = text.lower()
    return [token for token in NURSING_TOKENS if token in folded]


def raw_dump_names():
    """Clip names with a `raw` dump in the knowledge base. Used to skip a test rather than fail it
    when the corpus's dumps are not on this machine: they are untracked (ADR 0014), so a fresh clone
    has records and no dumps."""
    return {os.path.basename(p)[:-5]
            for p in glob.glob(os.path.join(paths.RAW_DIR, "*.json"))}

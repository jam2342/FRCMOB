# Quick tags a scout taps beside the notes ("Played defense", "Broke down"). The frontend owns
# the labels (ScoutingApp/src/features/scoutNotes/noteTags.ts); this is the allow-list, so a
# crafted payload can't put arbitrary strings in what every teammate sees.

from typing import Any

SCOUT_NOTE_TAGS = (
    "defense",
    "great_driver",
    "fast_cycles",
    "broke_down",
    "tipped",
    "lost_power",
    "penalties",
    "no_show",
)
SCOUT_NOTE_TAG_SET = frozenset(SCOUT_NOTE_TAGS)


def entry_note_tags(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("tags")
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for value in raw:
        tag = str(value or "").strip().lower()
        if tag in SCOUT_NOTE_TAG_SET and tag not in tags:
            tags.append(tag)
    return tags

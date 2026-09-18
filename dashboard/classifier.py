"""Turn unclassified amoCRM tags into dashboard dimensions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Set


UNKNOWN = "Не определено"
SPEED_DIAL_SOURCE = "Скорозвон"
SPEED_DIAL_MASSAGE_SOURCE = "Скорозвон Массаж"
SPEED_DIAL_LASER_SOURCE = "Скорозвон Лазер"
SPEED_DIAL_UNKNOWN_SOURCE = "Скорозвон — Не определено"
SPEED_DIAL_ANALYTICS_SOURCES = frozenset(
    {
        SPEED_DIAL_MASSAGE_SOURCE,
        SPEED_DIAL_LASER_SOURCE,
        SPEED_DIAL_UNKNOWN_SOURCE,
    }
)


def normalize(value: object) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    return " ".join(text.split())


def analytics_source_label(source: str, direction: str) -> str:
    """Split network-wide Скорозвон traffic by service direction."""
    if source != SPEED_DIAL_SOURCE:
        return source
    if direction == "Массаж":
        return SPEED_DIAL_MASSAGE_SOURCE
    if direction == "Лазер":
        return SPEED_DIAL_LASER_SOURCE
    return SPEED_DIAL_UNKNOWN_SOURCE


def is_speed_dial_source(source: object) -> bool:
    return str(source or "") in SPEED_DIAL_ANALYTICS_SOURCES


class TagClassifier:
    def __init__(self, mapping_path: Optional[Path] = None) -> None:
        path = mapping_path or Path(__file__).with_name("tag_mapping.json")
        self.mapping = json.loads(path.read_text(encoding="utf-8"))
        self._indexes: Dict[str, Dict[str, str]] = {}
        for category in ("branches", "directions", "sources", "offers"):
            index: Dict[str, str] = {}
            for label, aliases in self.mapping.get(category, {}).items():
                index[normalize(label)] = label
                for alias in aliases:
                    index[normalize(alias)] = label
            self._indexes[category] = index

    def _exact_match(
        self, category: str, tags: Sequence[str], used: Set[str]
    ) -> Optional[str]:
        index = self._indexes[category]
        for tag in tags:
            normalized = normalize(tag)
            if normalized in index:
                used.add(normalized)
                return index[normalized]
        return None

    def _branch_from_field(self, branch_name: Optional[str]) -> Optional[str]:
        normalized = normalize(branch_name)
        if not normalized:
            return None
        index = self._indexes["branches"]
        if normalized in index:
            return index[normalized]
        for alias, label in index.items():
            if len(alias) >= 5 and alias in normalized:
                return label
        return str(branch_name).strip()

    def classify(
        self,
        tags: Iterable[str],
        *,
        branch_name: Optional[str] = None,
    ) -> Mapping[str, str]:
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        used: Set[str] = set()

        branch = self._branch_from_field(branch_name)
        if not branch:
            branch = self._exact_match("branches", clean_tags, used)

        normalized_tags = {normalize(tag) for tag in clean_tags}
        direction = self._exact_match("directions", clean_tags, used)
        # Скорозвон is an explicit campaign marker and must win over any
        # accidental secondary source tag that may be added later.
        if normalize(SPEED_DIAL_SOURCE) in normalized_tags:
            source = SPEED_DIAL_SOURCE
            used.add(normalize(SPEED_DIAL_SOURCE))
            speed_dial_directions = {
                self._indexes["directions"][tag]
                for tag in normalized_tags
                if tag in self._indexes["directions"]
            }
            if len(speed_dial_directions) != 1:
                direction = None
        else:
            source = self._exact_match("sources", clean_tags, used)
        offer = self._exact_match("offers", clean_tags, used)

        return {
            "branch": branch or UNKNOWN,
            "direction": direction or UNKNOWN,
            "offer": offer or UNKNOWN,
            "source": source or UNKNOWN,
        }

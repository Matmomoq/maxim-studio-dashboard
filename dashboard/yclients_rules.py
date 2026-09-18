"""Shared business rules for Yclients records used as payment placeholders."""

from __future__ import annotations

from typing import Any, Iterable, Optional


ADMINISTRATOR_STAFF_NAME = "администратор"
ADMINISTRATOR_PREPAYMENT_SERVICES = {
    "предоплата (массаж)": "Массаж",
    "предоплата (лазер)": "Лазер",
}


def normalize_yclients_label(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("ё", "е").split())


def is_administrator_staff(value: Any) -> bool:
    """Match only the synthetic payment user, not 'Администратор XS'."""
    return normalize_yclients_label(value) == ADMINISTRATOR_STAFF_NAME


def administrator_prepayment_direction(values: Iterable[Any]) -> Optional[str]:
    """Return a direction only for the two explicitly approved service names."""
    for value in values:
        direction = ADMINISTRATOR_PREPAYMENT_SERVICES.get(
            normalize_yclients_label(value)
        )
        if direction:
            return direction
    return None

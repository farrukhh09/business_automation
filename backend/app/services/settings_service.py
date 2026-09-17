"""Typed business settings on top of ``app_settings`` (02-data-model.md, 04-api.md §12).

Storage: ``app_settings.key = "business"`` holds only the fields an admin has set (JSON). Reading
merges them over the ``BusinessSettings`` defaults, so defaults that come from the environment
(``daily_report_time``) keep following it until overridden. Unknown keys are ignored; a stored value
that no longer validates falls back to its default (logged as ``settings.invalid_stored_value``).
"""

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.core.logging import get_logger, log_event
from app.models.user import User
from app.repositories.app_settings import AppSettingRepository
from app.schemas.settings import BusinessSettings, BusinessSettingsUpdate, Warehouse

logger = get_logger(__name__)

BUSINESS_SETTINGS_KEY = "business"
WAREHOUSE_FIELD = "warehouse"
NULLABLE_WAREHOUSE_FIELDS = frozenset({"latitude", "longitude"})


def _error_fields(exc: PydanticValidationError) -> list[str]:
    return sorted({".".join(str(part) for part in error["loc"]) or "__root__" for error in exc.errors()})


class SettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = AppSettingRepository(db)

    # ------------------------------------------------------------------ read

    def get(self) -> BusinessSettings:
        settings, _ = self._effective(self._stored())
        return settings

    def _stored(self) -> dict[str, Any]:
        """Known keys of the stored JSON (warehouse filtered to known keys too)."""
        raw = self.repository.get_value(BUSINESS_SETTINGS_KEY, default={})
        if not isinstance(raw, Mapping):
            return {}
        stored = {key: value for key, value in raw.items() if key in BusinessSettings.model_fields}
        if WAREHOUSE_FIELD in stored:
            warehouse = stored[WAREHOUSE_FIELD]
            if isinstance(warehouse, Mapping):
                stored[WAREHOUSE_FIELD] = {
                    key: value for key, value in warehouse.items() if key in Warehouse.model_fields
                }
            else:
                stored.pop(WAREHOUSE_FIELD)
        return stored

    def _effective(self, stored: dict[str, Any]) -> tuple[BusinessSettings, dict[str, Any]]:
        """Validate stored values over defaults, dropping values that fail; returns (settings, usable stored)."""
        data: dict[str, Any] = {
            key: (dict(value) if isinstance(value, Mapping) else value) for key, value in stored.items()
        }
        dropped: list[str] = []
        for _ in range(len(BusinessSettings.model_fields) + len(Warehouse.model_fields) + 1):
            try:
                settings = BusinessSettings.model_validate(data)
            except PydanticValidationError as exc:
                removed = False
                for error in exc.errors():
                    loc = error["loc"]
                    if not loc:
                        continue
                    top = str(loc[0])
                    nested = data.get(WAREHOUSE_FIELD)
                    if top == WAREHOUSE_FIELD and len(loc) > 1 and isinstance(nested, dict) and loc[1] in nested:
                        nested.pop(str(loc[1]))
                        dropped.append(f"{top}.{loc[1]}")
                        removed = True
                    elif top in data:
                        data.pop(top)
                        dropped.append(top)
                        removed = True
                if not removed:
                    data = {}
                    dropped.append("__all__")
                continue
            if dropped:
                log_event(
                    logger,
                    "settings.invalid_stored_value",
                    level=logging.WARNING,
                    key=BUSINESS_SETTINGS_KEY,
                    fields=sorted(set(dropped)),
                )
            return settings, data
        return BusinessSettings(), {}  # pragma: no cover - the loop always converges

    # ------------------------------------------------------------------ write

    def update(self, patch: BusinessSettingsUpdate | Mapping[str, Any], user: User | None = None) -> BusinessSettings:
        """Apply a partial update, validate the result, store and commit (04 §12 ``PUT /settings``).

        Invalid values → ``ValidationError`` (422 ``validation_error``) with ``fields``.
        """
        if not isinstance(patch, BusinessSettingsUpdate):
            try:
                patch = BusinessSettingsUpdate.model_validate(dict(patch))
            except PydanticValidationError as exc:
                raise ValidationError(detail="Некорректные значения настроек", fields=_error_fields(exc)) from exc

        changes = patch.model_dump(exclude_unset=True)
        warehouse_changes: dict[str, Any] = {}
        if isinstance(changes.get(WAREHOUSE_FIELD), dict):
            warehouse_changes = {
                key: value
                for key, value in changes[WAREHOUSE_FIELD].items()
                if value is not None or key in NULLABLE_WAREHOUSE_FIELDS
            }
        changes = {key: value for key, value in changes.items() if key != WAREHOUSE_FIELD and value is not None}

        current, usable_stored = self._effective(self._stored())
        candidate = current.model_dump()
        candidate.update(changes)
        if warehouse_changes:
            candidate[WAREHOUSE_FIELD] = {**candidate[WAREHOUSE_FIELD], **warehouse_changes}
        try:
            new_settings = BusinessSettings.model_validate(candidate)
        except PydanticValidationError as exc:
            raise ValidationError(detail="Некорректные значения настроек", fields=_error_fields(exc)) from exc

        changed_fields = sorted(
            [key for key in changes if getattr(current, key) != getattr(new_settings, key)]
            + [
                f"{WAREHOUSE_FIELD}.{key}"
                for key in warehouse_changes
                if getattr(current.warehouse, key) != getattr(new_settings.warehouse, key)
            ]
        )

        serialized = new_settings.model_dump(mode="json")
        keys_to_store = set(usable_stored) | set(changes) | ({WAREHOUSE_FIELD} if warehouse_changes else set())
        to_store = {key: serialized[key] for key in sorted(keys_to_store)}
        self.repository.set_value(BUSINESS_SETTINGS_KEY, to_store, updated_by_user_id=user.id if user else None)
        self.db.commit()

        log_event(
            logger,
            "settings.updated",
            key=BUSINESS_SETTINGS_KEY,
            changed_fields=changed_fields,
            user_id=user.id if user else None,
        )
        return new_settings

"""Key-value settings (02-data-model.md: app_settings). Typed access: ``SettingsService``."""

import copy
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from app.models.settings import AppSetting
from app.repositories.base import BaseRepository


class AppSettingRepository(BaseRepository[AppSetting]):
    model = AppSetting
    not_found_detail = "Настройка не найдена"

    def get_value(self, key: str, default: Any = None) -> Any:
        """Stored JSON value (a deep copy, safe to mutate) or ``default``."""
        setting = self.get(key)
        if setting is None:
            return default
        return copy.deepcopy(setting.value)

    def set_value(self, key: str, value: Any, updated_by_user_id: int | None = None) -> AppSetting:
        """Create or replace the value (flush only)."""
        setting = self.get(key)
        if setting is None:
            setting = AppSetting(key=key, value=copy.deepcopy(value), updated_by_user_id=updated_by_user_id)
            self.db.add(setting)
        else:
            setting.value = copy.deepcopy(value)
            setting.updated_by_user_id = updated_by_user_id
            flag_modified(setting, "value")
        self.db.flush()
        return setting

"""验证迁移插件同步保护，不访问市场、安装插件或修改真实配置。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.plugin import PluginManager


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes"])
def test_sync_disabled_before_side_effects(monkeypatch, value: str) -> None:
    """停用时必须早于配置读取、候选查询和安装操作返回。"""
    monkeypatch.setenv("MOVIEPILOT_PLUGIN_SYNC_DISABLED", value)
    config = Mock(side_effect=AssertionError("不应读取配置"))
    market = Mock(side_effect=AssertionError("不应查询市场"))
    install = Mock(side_effect=AssertionError("不应安装插件"))
    monkeypatch.setattr("app.core.plugin.SystemConfigOper", config)
    monkeypatch.setattr("app.core.plugin.PluginHelper", install)
    manager = SimpleNamespace(get_online_plugins=market)

    assert PluginManager.sync(manager) == []
    config.assert_not_called()
    market.assert_not_called()
    install.assert_not_called()


@pytest.mark.parametrize("value", [None, "false", "0", "no", ""])
def test_default_sync_keeps_existing_behavior(monkeypatch, value) -> None:
    """未启用保护时继续原有候选查询，不改变默认同步行为。"""
    if value is None:
        monkeypatch.delenv("MOVIEPILOT_PLUGIN_SYNC_DISABLED", raising=False)
    else:
        monkeypatch.setenv("MOVIEPILOT_PLUGIN_SYNC_DISABLED", value)
    monkeypatch.setattr("app.core.plugin.SystemUtils.is_frozen", lambda: False)
    config = Mock()
    config.return_value.get.return_value = []
    monkeypatch.setattr("app.core.plugin.SystemConfigOper", config)
    market = Mock(return_value=[])
    local = Mock(return_value=[])
    manager = SimpleNamespace(get_online_plugins=market, get_local_repo_plugins=local)

    assert PluginManager.sync(manager) == []
    config.return_value.get.assert_called_once()
    market.assert_called_once()
    local.assert_called_once()

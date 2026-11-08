from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "local_setup.py"


def _load_local_setup_module():
    module_name = f"moviepilot_local_setup_frontend_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_repo_frontend_version_reads_version_file(tmp_path, monkeypatch):
    """从仓库版本文件读取前端版本。"""
    module = _load_local_setup_module()
    (tmp_path / "version.py").write_text(
        "APP_VERSION = 'v0.0.1'\nFRONTEND_VERSION = 'v9.9.9'\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module._repo_frontend_version() == "v9.9.9"


def test_resolve_frontend_release_uses_repo_frontend_version_by_default(monkeypatch):
    """未指定版本时使用仓库版本，发布地址由测试显式提供。"""
    module = _load_local_setup_module()
    release = {
        "tag_name": "v9.9.9",
        "assets": [{"name": "dist.zip", "browser_download_url": "https://example.com/dist.zip"}],
    }
    fetch = Mock(return_value=release)
    monkeypatch.setattr(module, "FRONTEND_LATEST_API", "https://example.test/latest")
    monkeypatch.setattr(module, "FRONTEND_TAG_API", "https://example.test/tags/{tag}")
    monkeypatch.setattr(module, "_repo_frontend_version", lambda: "v9.9.9")
    monkeypatch.setattr(module, "fetch_json", fetch)
    assert module._resolve_frontend_release(None) == ("v9.9.9", "https://example.com/dist.zip")
    fetch.assert_called_once_with("https://example.test/tags/v9.9.9")


def test_resolve_frontend_release_rejects_unconfigured_urls(monkeypatch):
    """未配置发布地址时不隐式访问外部资源。"""
    module = _load_local_setup_module()
    monkeypatch.setattr(module, "FRONTEND_LATEST_API", "")
    monkeypatch.setattr(module, "FRONTEND_TAG_API", "")
    with pytest.raises(RuntimeError, match="未配置前端版本发布地址"):
        module._resolve_frontend_release(None)


def test_parser_leaves_frontend_version_empty_until_runtime_resolution():
    """解析命令时不提前决定前端版本。"""
    module = _load_local_setup_module()
    parser = module.build_parser()
    assert parser.parse_args(["install-frontend"]).version is None
    assert parser.parse_args(["setup"]).frontend_version is None
    assert parser.parse_args(["update", "frontend"]).frontend_version is None

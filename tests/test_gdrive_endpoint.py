import inspect
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.endpoints import gdrive as gdrive_endpoint
from app.db.user_oper import (
    get_current_active_manage_user,
    get_current_active_superuser,
)
from app.schemas import GdriveTaskCleanupRequest, GdriveTaskCleanupResult
from app.schemas.response import Response


def _dependency_of(func, parameter_name: str):
    """读取 FastAPI 接口参数上的依赖函数。"""
    return inspect.signature(func).parameters[parameter_name].default.dependency


def test_gdrive_endpoint_permissions_are_split_by_risk():
    """任务读取和可重试操作允许管理用户，配置写入和清理要求超级管理员。"""
    manage_endpoints = [
        gdrive_endpoint.get_settings,
        gdrive_endpoint.preflight,
        gdrive_endpoint.list_tasks,
        gdrive_endpoint.list_task_history,
        gdrive_endpoint.create_task,
        gdrive_endpoint.retry_task,
        gdrive_endpoint.cancel_task,
    ]
    superuser_endpoints = [gdrive_endpoint.update_settings, gdrive_endpoint.cleanup_task]

    assert all(_dependency_of(endpoint, "_") is get_current_active_manage_user
               for endpoint in manage_endpoints)
    assert all(_dependency_of(endpoint, "_") is get_current_active_superuser
               for endpoint in superuser_endpoints)


def test_list_tasks_forwards_status_and_limit(monkeypatch):
    """任务列表接口应把状态和上限传递给数据访问层。"""
    calls = []
    task = SimpleNamespace(id=4)

    class FakeOper:
        def list(self, status=None, limit=100):
            calls.append((status, limit))
            return [task]

    monkeypatch.setattr(gdrive_endpoint, "GdriveTaskOper", FakeOper)
    monkeypatch.setattr(
        gdrive_endpoint,
        "_task_info",
        lambda value: {"id": value.id},
    )

    result = gdrive_endpoint.list_tasks(status="failed", limit=23, _=None)

    assert result == [{"id": 4}]
    assert calls == [("failed", 23)]


def test_history_forwards_task_id_and_limit(monkeypatch):
    """任务历史接口应使用路径任务 ID 和请求上限查询记录。"""
    calls = []
    record = SimpleNamespace(to_dict=lambda: {
        "id": 8,
        "task_id": 4,
        "to_status": "failed",
        "to_stage": "uploading",
        "event_type": "failed",
        "retry_count": 0,
        "created_at": "2026-01-01 00:00:00",
    })

    class FakeOper:
        def list_by_task(self, task_id, limit=200):
            calls.append((task_id, limit))
            return [record]

    monkeypatch.setattr(gdrive_endpoint, "GdriveTaskHistoryOper", FakeOper)

    result = gdrive_endpoint.list_task_history(4, limit=17, _=None)

    assert result[0].task_id == 4
    assert result[0].event_type == "failed"
    assert calls == [(4, 17)]


def test_retry_and_cancel_return_standard_responses(monkeypatch):
    """重试和取消接口应统一返回成功标志及领域错误。"""
    monkeypatch.setattr(
        gdrive_endpoint.GdriveChain,
        "retry_task",
        classmethod(lambda cls, task_id: (False, "任务仍在运行")),
    )
    retry_response = gdrive_endpoint.retry_task(4, _=None)
    assert isinstance(retry_response, Response)
    assert retry_response.success is False
    assert retry_response.message == "任务仍在运行"

    monkeypatch.setattr(
        gdrive_endpoint.GdriveChain,
        "cancel_task",
        classmethod(lambda cls, task_id: True),
    )
    cancel_response = gdrive_endpoint.cancel_task(4, _=None)
    assert cancel_response.success is True


def test_cleanup_defaults_to_dry_run_and_accepts_explicit_request(monkeypatch):
    """清理接口缺省为预览，显式请求才允许执行删除。"""
    calls = []

    def cleanup(task_id, dry_run=True):
        calls.append((task_id, dry_run))
        return GdriveTaskCleanupResult(
            task_id=task_id,
            dry_run=dry_run,
            can_cleanup=True,
            message="ok",
        )

    monkeypatch.setattr(gdrive_endpoint.GdriveChain, "cleanup_task", cleanup)

    preview = gdrive_endpoint.cleanup_task(4, value=None, _=None)
    actual = gdrive_endpoint.cleanup_task(
        4, value=GdriveTaskCleanupRequest(dry_run=False), _=None
    )

    assert preview.dry_run is True
    assert actual.dry_run is False
    assert calls == [(4, True), (4, False)]


@pytest.mark.anyio
async def test_task_list_rejects_out_of_range_limit_at_http_layer():
    """任务列表接口应在进入业务逻辑前拒绝超出范围的分页参数。"""
    app = FastAPI()
    app.include_router(gdrive_endpoint.router, prefix="/gdrive")
    app.dependency_overrides[get_current_active_manage_user] = lambda: SimpleNamespace()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        lower_response = await client.get("/gdrive/tasks?limit=0")
        upper_response = await client.get("/gdrive/tasks?limit=501")

    assert lower_response.status_code == 422
    assert upper_response.status_code == 422


@pytest.mark.anyio
async def test_history_rejects_non_integer_task_id_at_http_layer():
    """任务历史接口应由 FastAPI 拒绝非整数任务 ID。"""
    app = FastAPI()
    app.include_router(gdrive_endpoint.router, prefix="/gdrive")
    app.dependency_overrides[get_current_active_manage_user] = lambda: SimpleNamespace()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/gdrive/tasks/not-an-id/history")

    assert response.status_code == 422

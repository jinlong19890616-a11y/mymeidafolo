"""Google Drive/FUSE 挂载点现场能力回归测试。

测试只使用 ``GDRIVE_TEST_MOUNT_PATH`` 指定的挂载点下的专属临时目录，
不会读取、覆盖或删除挂载点中的既有文件。未配置现场挂载点时，所有测试安全跳过。
"""

import hashlib
import errno
import multiprocessing
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest


_PAYLOADS = (b"mymediafolo-live-a\n", b"mymediafolo-live-b\n")


def _exclusive_create_worker(path: str, barrier, result_queue) -> None:
    """在子进程中竞争创建同一个文件。"""
    barrier.wait(timeout=30)
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(descriptor, b"exclusive-create\n")
        os.fsync(descriptor)
        result_queue.put("created")
    except FileExistsError:
        result_queue.put("exists")
    except OSError as error:
        result_queue.put(f"error:{error.errno}:{error}")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_reader_worker(path: str, ready, stop, result_queue) -> None:
    """持续读取原子替换目标并报告缺失或半写入内容。"""
    ready.set()
    allowed = set(_PAYLOADS)
    result = "ok"
    while not stop.is_set():
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            result = "missing"
            break
        except OSError as error:
            result = f"error:{error.errno}:{error}"
            break
        if data not in allowed:
            result = "partial-or-invalid"
            break
    result_queue.put(result)


def _recovery_worker(path: str, payload: bytes, barrier, result_queue) -> None:
    """竞争发布完整归档，失败方等待并验证胜者的完整结果。"""
    barrier.wait(timeout=30)
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        result_queue.put("publisher")
        return
    except FileExistsError:
        expected = hashlib.sha256(payload).hexdigest()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                candidate = Path(path).read_bytes()
            except FileNotFoundError:
                continue
            if hashlib.sha256(candidate).hexdigest() == expected:
                result_queue.put("recovered")
                return
            if len(candidate) > len(payload):
                result_queue.put("corrupt")
                return
    except OSError as error:
        result_queue.put(f"error:{error.errno}:{error}")
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)
    result_queue.put("timeout")


@pytest.fixture
def live_mount_root() -> Path:
    """创建并清理现场挂载点专属测试目录。"""
    configured = os.environ.get("GDRIVE_TEST_MOUNT_PATH")
    if not configured:
        pytest.skip("未设置 GDRIVE_TEST_MOUNT_PATH，跳过真实挂载测试")
    mount_path = Path(configured).expanduser().resolve()
    if not mount_path.is_dir():
        pytest.fail(f"现场挂载路径不存在或不是目录：{mount_path}")
    if not os.path.ismount(mount_path):
        pytest.fail(f"现场路径不是系统挂载点，拒绝在普通目录执行现场测试：{mount_path}")
    test_root = mount_path / f".mymediafolo-live-regression-{uuid.uuid4().hex}"
    try:
        test_root.mkdir()
    except OSError as error:
        pytest.fail(f"现场挂载点不可写：{mount_path} ({error})")
    try:
        yield test_root
    finally:
        shutil.rmtree(test_root, ignore_errors=True)


def test_live_mount_hard_link_or_exclusive_copy_fallback(live_mount_root: Path) -> None:
    """探测硬链接能力，并验证不支持时的排他复制回退。"""
    source = live_mount_root / "source.bin"
    archive = live_mount_root / "archive.bin"
    payload = b"hard-link-content\n"
    source.write_bytes(payload)

    try:
        os.link(source, archive)
    except OSError as error:
        assert error.errno in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP}
        with source.open("rb") as source_file, archive.open("xb") as archive_file:
            while block := source_file.read(1024 * 1024):
                archive_file.write(block)
            archive_file.flush()
            os.fsync(archive_file.fileno())
        assert archive.read_bytes() == payload
    else:
        assert os.path.samefile(source, archive)
        assert archive.read_bytes() == payload



def test_live_mount_supports_exclusive_create(live_mount_root: Path) -> None:
    """验证现场挂载的 O_EXCL 创建在多进程下只有一个胜者。"""
    target = live_mount_root / "exclusive.bin"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(target=_exclusive_create_worker, args=(str(target), barrier, result_queue))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        if worker.is_alive():
            worker.terminate()
            worker.join()

    results = [result_queue.get(timeout=5) for _ in workers]
    assert all(worker.exitcode == 0 for worker in workers)
    assert sorted(results) == ["created", "exists"]
    assert target.read_bytes() == b"exclusive-create\n"


def test_live_mount_supports_fsync_and_visible_writes(live_mount_root: Path) -> None:
    """验证写入后 fsync，关闭文件后其他打开句柄可见完整内容。"""
    target = live_mount_root / "fsync.bin"
    payload = b"fsync-visible-content\n"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        assert os.write(descriptor, payload) == len(payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

    assert target.read_bytes() == payload


def test_live_mount_supports_single_atomic_replace(live_mount_root: Path) -> None:
    """验证现场挂载至少支持一次完整的临时文件原子改名。"""
    target = live_mount_root / "atomic-single.bin"
    temporary = live_mount_root / ".atomic-single.tmp"
    target.write_bytes(_PAYLOADS[0])
    temporary.write_bytes(_PAYLOADS[1])
    with temporary.open("rb") as file_obj:
        os.fsync(file_obj.fileno())

    os.replace(temporary, target)

    assert not temporary.exists()
    assert target.read_bytes() == _PAYLOADS[1]


def test_live_mount_supports_atomic_replace_under_readers(live_mount_root: Path) -> None:
    """验证临时文件 fsync 后原子改名不会暴露缺失或半写入内容。"""
    target = live_mount_root / "atomic.bin"
    target.write_bytes(_PAYLOADS[0])
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    stop = context.Event()
    result_queue = context.Queue()
    reader = context.Process(
        target=_atomic_reader_worker,
        args=(str(target), ready, stop, result_queue),
    )
    reader.start()
    assert ready.wait(10)
    try:
        for index in range(100):
            temporary = live_mount_root / f".atomic-{index}.tmp"
            temporary.write_bytes(_PAYLOADS[index % len(_PAYLOADS)])
            with temporary.open("rb") as file_obj:
                os.fsync(file_obj.fileno())
            os.replace(temporary, target)
    finally:
        stop.set()
        reader.join(30)
        if reader.is_alive():
            reader.terminate()
            reader.join()

    assert reader.exitcode == 0
    result = result_queue.get(timeout=5)
    if result.startswith(f"error:{errno.EIO}:"):
        pytest.skip("现场挂载在并发读者下的连续原子改名返回 EIO")
    assert result == "ok"
    assert target.read_bytes() in _PAYLOADS


def test_live_mount_supports_concurrent_recovery(live_mount_root: Path) -> None:
    """验证并发恢复时只有一个进程发布，其他进程能看到完整文件。"""
    target = live_mount_root / "recovery.bin"
    payload = b"concurrent-recovery-content\n" * 1024
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_recovery_worker,
            args=(str(target), payload, barrier, result_queue),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        if worker.is_alive():
            worker.terminate()
            worker.join()

    results = [result_queue.get(timeout=5) for _ in workers]
    assert all(worker.exitcode == 0 for worker in workers)
    assert sorted(results) == ["publisher", "recovered"]
    assert target.read_bytes() == payload

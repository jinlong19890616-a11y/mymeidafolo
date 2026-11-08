import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from app.core.config import settings
from app.schemas.gdrive import GdriveUploadCredential, GdriveUploadTarget
from app.utils.http import RequestUtils


_CREDENTIAL_LOCK = threading.RLock()
_FOLDER_LOCK = threading.RLock()
_API_ROOT = "https://www.googleapis.com/drive/v3"
_UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3/files"
_FILE_FIELDS = "id,name,size,md5Checksum,parents,driveId,trashed,appProperties"


class UploadHelper:
    """封装共享盘可恢复上传协议和私有凭据文件，不依赖挂载目录"""

    def __init__(self, account_id: str, check: Optional[Callable] = None):
        self.account_id = account_id
        self.check = check or (lambda: None)

    @staticmethod
    def _private_root() -> Path:
        root = Path(settings.CONFIG_PATH) / "gdrive-upload"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("上传私有目录不能是符号链接")
        root.chmod(0o700)
        return root

    @classmethod
    def _credential_path(cls, account_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", account_id):
            raise ValueError("上传账号标识无效")
        return cls._private_root() / f"account-{account_id}.json"

    @staticmethod
    def _save_private(path: Path, value: dict) -> None:
        temporary = path.with_suffix(".tmp")
        if path.is_symlink() or temporary.is_symlink():
            raise ValueError("上传私有文件不能是符号链接")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)

    @classmethod
    def save_credential(cls, account_id: str, value: GdriveUploadCredential) -> None:
        """保存只写 token；更新时不清除已有 OAuth 客户端凭据"""
        try:
            token = json.loads(value.token.get_secret_value())
        except (TypeError, ValueError) as err:
            raise ValueError("Token 必须是包含 access_token 或 refresh_token 的 JSON") from err
        if not isinstance(token, dict) or not any(token.get(key) for key in ("access_token", "refresh_token")):
            raise ValueError("Token 缺少 access_token 或 refresh_token")
        if any(key in token and (not isinstance(token[key], str) or re.search(r"[\x00-\x1f\x7f]", token[key]))
               for key in ("access_token", "refresh_token")):
            raise ValueError("Token 字段必须是不含控制字符的字符串")
        with _CREDENTIAL_LOCK:
            path = cls._credential_path(account_id)
            previous = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else {}
            cls._save_private(path, {
                "token": {key: token[key] for key in ("access_token", "refresh_token", "expiry", "expires_in") if key in token},
                "client_id": value.client_id or previous.get("client_id", ""),
                "client_secret": value.client_secret.get_secret_value() or previous.get("client_secret", ""),
            })

    @classmethod
    def has_credential(cls, account_id: str) -> bool:
        """仅返回凭据是否存在，不向页面回显内容"""
        path = cls._credential_path(account_id)
        return path.is_file() and not path.is_symlink()

    def _access_token(self, refresh: bool = False) -> str:
        with _CREDENTIAL_LOCK:
            path = self._credential_path(self.account_id)
            if not path.is_file() or path.is_symlink():
                raise ValueError("上传账号尚未配置 Token")
            value = json.loads(path.read_text())
            token = value["token"]
            if not refresh and token.get("access_token"):
                return token["access_token"]
            if not all((token.get("refresh_token"), value.get("client_id"), value.get("client_secret"))):
                raise ValueError("账号 Token 已过期；自动续期需要 refresh_token、client_id 和 client_secret")
            self.check()
            response = RequestUtils(timeout=30, proxies=settings.PROXY).post_res(
                "https://oauth2.googleapis.com/token", data={
                    "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                    "client_id": value["client_id"], "client_secret": value["client_secret"],
                }, verify=True, allow_redirects=False,
            )
            if response is None or response.status_code != 200:
                if response is not None:
                    response.close()
                raise RuntimeError("Google OAuth 续期失败，请更新该账号凭据")
            try:
                updated = response.json()
            finally:
                response.close()
            if not isinstance(updated.get("access_token"), str) or not updated["access_token"]:
                raise RuntimeError("Google OAuth 未返回有效访问令牌")
            token.update({key: updated[key] for key in ("access_token", "refresh_token", "expires_in") if key in updated})
            self._save_private(path, value)
            return token["access_token"]

    def _request(self, method: str, url: str, **kwargs):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "www.googleapis.com" or not parsed.path.startswith(("/drive/v3/", "/upload/drive/v3/")):
            raise ValueError("Google 上传会话地址无效")
        refreshed = False
        request_headers = kwargs.pop("headers", {})
        if "json" in kwargs:
            request_headers.setdefault("Content-Type", "application/json; charset=UTF-8")
        for attempt in range(6):
            self.check()
            headers = {"Authorization": f"Bearer {self._access_token()}", **request_headers}
            response = RequestUtils(timeout=60, proxies=settings.PROXY).request(
                method, url, headers=headers, verify=True, allow_redirects=False, **kwargs,
            )
            # 分块请求返回 308 时不能跟随重定向，否则会丢失服务端确认的偏移。
            if response is not None and response.status_code == 401 and not refreshed:
                response.close()
                self._access_token(refresh=True)
                refreshed = True
                continue
            if response is not None and response.status_code not in {429, 500, 502, 503, 504}:
                return response
            if response is not None:
                response.close()
            if method.lower() in {"post", "put", "patch"}:
                raise RuntimeError("Google Drive 写入请求结果未确认，请重试任务以核对云端状态")
            if attempt < 5:
                for _ in range(2 ** attempt):
                    self.check()
                    time.sleep(1)
        raise RuntimeError("Google Drive 请求多次失败，可稍后重试任务")

    @staticmethod
    def _json(response, expected: tuple = (200, 201)) -> dict:
        if response is None:
            raise RuntimeError("Google Drive 请求未收到响应")
        try:
            if response.status_code not in expected:
                reason = ""
                try:
                    errors = response.json().get("error", {}).get("errors", [])
                    reason = next((item.get("reason", "") for item in errors), "")
                except (ValueError, AttributeError, TypeError):
                    pass
                safe_reason = reason if re.fullmatch(r"[A-Za-z]{1,80}", reason) else "unknown"
                raise RuntimeError(f"Google Drive 请求失败（HTTP {response.status_code}，{safe_reason}）")
            return response.json()
        finally:
            response.close()

    def check_target(self, target: GdriveUploadTarget) -> dict:
        """只读验证根目录属于所选共享盘且当前账号可添加文件"""
        folder = self._json(self._request("get", f"{_API_ROOT}/files/{target.folder_id}", params={
            "supportsAllDrives": "true", "fields": "id,driveId,mimeType,trashed,capabilities(canAddChildren)",
        }))
        if (folder.get("driveId") != target.drive_id or folder.get("trashed")
                or folder.get("mimeType") != "application/vnd.google-apps.folder"
                or not folder.get("capabilities", {}).get("canAddChildren")):
            raise ValueError("该账号无法写入所选共享盘目录，请检查共享盘 ID、目录 ID 和权限")
        return folder

    def _list(self, target: GdriveUploadTarget, query: str) -> list[dict]:
        result = self._json(self._request("get", f"{_API_ROOT}/files", params={
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "drive",
            "driveId": target.drive_id, "q": query, "fields": "files(id,name,mimeType),nextPageToken", "pageSize": 100,
        }))
        if result.get("nextPageToken"):
            raise RuntimeError("同名网盘项目过多，请整理目标目录后重试")
        return result.get("files", [])

    def _parent(self, target: GdriveUploadTarget, parts: tuple) -> str:
        parent = target.folder_id
        with _FOLDER_LOCK:
            for name in parts:
                escaped = name.replace("\\", "\\\\").replace("'", "\\'")
                matches = self._list(target, f"'{parent}' in parents and trashed=false and name='{escaped}'")
                if len(matches) > 1 or (matches and matches[0].get("mimeType") != "application/vnd.google-apps.folder"):
                    raise ValueError("网盘分类目录重名或被文件占用，请处理后重试")
                if matches:
                    parent = matches[0]["id"]
                else:
                    parent = self._json(self._request("post", f"{_API_ROOT}/files", params={
                        "supportsAllDrives": "true", "fields": "id",
                    }, json={"name": name, "parents": [parent], "mimeType": "application/vnd.google-apps.folder"}))["id"]
        return parent

    @classmethod
    def _state_path(cls, task_id: int) -> Path:
        return cls._private_root() / f"task-{int(task_id)}.json"

    def _metadata(self, file_id: str) -> Optional[dict]:
        response = self._request("get", f"{_API_ROOT}/files/{file_id}", params={
            "supportsAllDrives": "true", "fields": _FILE_FIELDS,
        })
        if response.status_code == 404:
            response.close()
            return None
        return self._json(response)

    def verify_uploaded(self, task_id: int, target: GdriveUploadTarget, file_id: str) -> dict:
        """从私有会话重新核验云端成品，支持源文件删除阶段的崩溃恢复"""
        state = json.loads(self._state_path(task_id).read_text())
        if (state.get("file_id") != file_id or GdriveUploadTarget(**state["identity"]["target"]).model_dump() != target.model_dump()
                or state["identity"]["account"] != self.account_id):
            raise ValueError("云端文件与上传会话身份不一致")
        metadata = self._metadata(file_id)
        archive = state.get("archive", {})
        at_archive = bool(archive and metadata and metadata.get("parents") == [archive["parent"]]
                          and metadata.get("name") == archive["name"])
        parent = archive["parent"] if at_archive else state["parent"]
        name = archive["name"] if at_archive else Path(target.path).name
        if (state.get("archived") and not at_archive):
            raise IOError("Google Drive 已归档文件位置发生变化，保留源文件")
        if (not metadata or metadata.get("id") != file_id or metadata.get("name") != name
                or str(metadata.get("size")) != str(state["identity"]["size"])
                or metadata.get("md5Checksum") != state["identity"]["md5"]
                or metadata.get("driveId") != target.drive_id or metadata.get("trashed")
                or metadata.get("parents") != [parent]
                or metadata.get("appProperties", {}).get("moviepilot_upload") != str(task_id)):
            raise IOError("Google Drive 云端文件大小、校验和或归属校验失败，保留源文件")
        return metadata

    def archive_uploaded(self, task_id: int, target: GdriveUploadTarget, file_id: str) -> dict:
        """核验上传成品后在同一共享盘内移动，持久保存目标以恢复响应丢失的归档"""
        metadata = self.verify_uploaded(task_id, target, file_id)
        if not target.archive_folder_id:
            return metadata
        state_path = self._state_path(task_id)
        state = json.loads(state_path.read_text())
        destination = target.model_copy(update={"folder_id": target.archive_folder_id,
                                                 "path": target.archive_path or target.path})
        self.check_target(destination)
        if not state.get("archive"):
            relative = Path(destination.path)
            parent = self._parent(destination, relative.parts[:-1])
            if parent == state["parent"]:
                raise ValueError("上传目录与云端归档目录必须分开")
            state["archive"] = {"parent": parent, "name": relative.name}
            self._save_private(state_path, state)
        archive = state["archive"]
        if metadata.get("parents") != [archive["parent"]] or metadata.get("name") != archive["name"]:
            escaped = archive["name"].replace("\\", "\\\\").replace("'", "\\'")
            matches = self._list(destination, f"'{archive['parent']}' in parents and trashed=false and name='{escaped}'")
            if any(item["id"] != file_id for item in matches):
                raise ValueError("云端归档目录已有同名文件，保留源文件及已上传文件")
            self._json(self._request("patch", f"{_API_ROOT}/files/{file_id}", params={
                "supportsAllDrives": "true", "addParents": archive["parent"],
                "removeParents": state["parent"], "fields": _FILE_FIELDS,
            }, json={"name": archive["name"]}))
        metadata = self.verify_archived(task_id, target, file_id)
        state["archived"] = True
        self._save_private(state_path, state)
        return metadata

    def verify_archived(self, task_id: int, target: GdriveUploadTarget, file_id: str) -> dict:
        """只读核验最终归档位置，未确认归档时禁止生成 STRM 或删除源文件"""
        metadata = self.verify_uploaded(task_id, target, file_id)
        if target.archive_folder_id:
            state = json.loads(self._state_path(task_id).read_text())
            archive = state.get("archive")
            if (not archive or metadata.get("parents") != [archive["parent"]]
                    or metadata.get("name") != archive["name"]):
                raise IOError("Google Drive 云端归档位置未确认，保留源文件")
        return metadata

    @staticmethod
    def write_strm(task_id: int, output: Path, target: str) -> Path:
        """发布普通 STRM 文本，使用持久标记恢复中断写入且不覆盖已有内容"""
        content = target + "\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        base = output
        version = 0
        while True:
            marker = output.with_name(f".{output.name}.{task_id}.gdrive-upload.claim")
            if output.is_symlink() or marker.is_symlink():
                raise ValueError("STRM 输出不能是符号链接")
            if output.exists():
                if marker.is_file() and marker.read_text() == content and output.read_text() == content:
                    return output
                version += 1
                output = base.with_name(f"{base.stem} (version {version}).strm")
                continue
            if marker.exists() and marker.read_text() != content:
                raise ValueError("STRM 任务标记不匹配")
            if not marker.exists():
                with marker.open("x", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            try:
                with output.open("x", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                return output
            except FileExistsError:
                continue

    def upload(self, task_id: int, source: Path, target: GdriveUploadTarget, chunk_size: int,
               progress: Callable[[int], None]) -> dict:
        """分块直接上传并查询服务端偏移，完成后校验网盘文件大小及 MD5"""
        self.check_target(target)
        relative = Path(target.path)
        size = source.stat().st_size
        digest = hashlib.md5()
        with source.open("rb") as stream:
            while block := stream.read(8 * 1024 * 1024):
                self.check()
                digest.update(block)
        expected_md5 = digest.hexdigest()
        state_path = self._state_path(task_id)
        if state_path.is_symlink():
            raise ValueError("上传会话文件不能是符号链接")
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        identity = {"source": str(source), "size": size, "md5": expected_md5,
                    "account": self.account_id, "target": target.model_dump()}
        if state and state.get("identity"):
            state["identity"]["target"] = GdriveUploadTarget(**state["identity"]["target"]).model_dump()
        if state and state.get("identity") != identity:
            raise ValueError("源文件、上传账号或目标已变化，拒绝续传旧会话")
        if not state:
            parent = self._parent(target, relative.parts[:-1])
            escaped = relative.name.replace("\\", "\\\\").replace("'", "\\'")
            if self._list(target, f"'{parent}' in parents and trashed=false and name='{escaped}'"):
                raise ValueError("目标目录已有同名文件，请更改网盘相对路径后上传")
            generated = self._json(self._request("get", f"{_API_ROOT}/files/generateIds", params={"count": 1, "space": "drive"}))
            state = {"identity": identity, "parent": parent, "file_id": generated["ids"][0]}
            self._save_private(state_path, state)
        metadata = self._metadata(state["file_id"])
        if metadata is None:
            if state.get("archive"):
                raise IOError("已进入归档阶段的云端文件不存在，保留源文件并停止重传")
            for _ in range(2):
                if not state.get("session"):
                    response = self._request("post", _UPLOAD_ROOT, params={
                        "uploadType": "resumable", "supportsAllDrives": "true", "fields": _FILE_FIELDS,
                    }, headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "application/octet-stream",
                                "Content-Type": "application/json; charset=UTF-8"}, json={
                        "id": state["file_id"], "name": relative.name, "parents": [state["parent"]],
                        "appProperties": {"moviepilot_upload": str(task_id)},
                    })
                    try:
                        if response.status_code not in {200, 201} or not response.headers.get("Location"):
                            raise RuntimeError(f"无法创建 Google 可恢复上传会话（HTTP {response.status_code}）")
                        state["session"] = response.headers["Location"]
                    finally:
                        response.close()
                    self._save_private(state_path, state)
                response = self._request("put", state["session"], headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"}, data=b"")
                if response.status_code in {404, 410}:
                    response.close()
                    state.pop("session", None)
                    self._save_private(state_path, state)
                    continue
                if response.status_code in {200, 201}:
                    metadata = self._json(response)
                    break
                if response.status_code != 308:
                    self._json(response)
                offset = self._offset(response, size)
                progress(offset)
                with source.open("rb") as stream:
                    while offset < size:
                        self.check()
                        stream.seek(offset)
                        block = stream.read(min(chunk_size, size - offset))
                        if not block:
                            raise ValueError("上传期间源文件大小发生变化")
                        response = self._request("put", state["session"], headers={
                            "Content-Type": "application/octet-stream", "Content-Length": str(len(block)),
                            "Content-Range": f"bytes {offset}-{offset + len(block) - 1}/{size}",
                        }, data=block)
                        if response.status_code in {200, 201}:
                            metadata = self._json(response)
                            progress(size)
                            break
                        if response.status_code != 308:
                            self._json(response)
                        confirmed = self._offset(response, size)
                        if confirmed <= offset or confirmed > offset + len(block):
                            raise RuntimeError("Google Drive 返回无效的续传偏移")
                        offset = confirmed
                        progress(offset)
                break
        # 最后一次响应丢失或进程重启时，通过预分配 ID 查询同一云端文件，避免重复上传。
        return self.verify_uploaded(task_id, target, state["file_id"])

    @staticmethod
    def _offset(response, size: int) -> int:
        try:
            value = response.headers.get("Range", "")
            if not value:
                return 0
            match = re.fullmatch(r"bytes=0-(\d+)", value)
            if not match or int(match[1]) >= size:
                raise RuntimeError("Google Drive 返回无效的已上传范围")
            return int(match[1]) + 1
        finally:
            response.close()

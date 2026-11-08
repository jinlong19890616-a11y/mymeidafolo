from pathlib import Path
from typing import Literal, Optional
import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class GdriveUploadAccount(BaseModel):
    """上传账号的公开配置，凭据仅保存在服务器私有文件中"""

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    concurrency: int = Field(default=1, ge=1, le=16)


class GdriveUploadCredential(BaseModel):
    """只写 OAuth 凭据，兼容 rclone token 的 JSON 内容"""

    token: SecretStr
    client_id: str = Field(default="", max_length=500)
    client_secret: SecretStr = SecretStr("")


class GdriveUploadSettings(BaseModel):
    """与挂载复制独立的 Google Drive API 上传配置"""

    enabled: bool = False
    concurrency: int = Field(default=4, ge=1, le=16)
    chunk_size_mb: int = Field(default=16, ge=1, le=64)
    notifications_enabled: bool = False
    daily_report_enabled: bool = False
    notification_source: str = Field(default="", max_length=100)
    accounts: list[GdriveUploadAccount] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_accounts(self) -> "GdriveUploadSettings":
        """账号标识不可重复，避免并发计数和凭据混用"""
        if len({account.id for account in self.accounts}) != len(self.accounts):
            raise ValueError("上传账号标识不能重复")
        return self


class GdriveUploadTarget(BaseModel):
    """每次上传冻结共享盘、根目录和可参与调度的账号"""

    account_ids: list[str] = Field(min_length=1, max_length=50)
    drive_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,200}$")
    folder_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,200}$")
    path: str = Field(default="", max_length=3500)
    archive_folder_id: str = Field(default="", pattern=r"^[a-zA-Z0-9_-]{0,200}$")
    archive_path: str = Field(default="", max_length=3500)
    strm_prefix: str = Field(default="", max_length=4096)
    metadata_enabled: bool = False
    refresh_server: str = Field(default="", max_length=100)
    refresh_library_id: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_-]*$")

    @model_validator(mode="after")
    def complete_refresh_target(self) -> "GdriveUploadTarget":
        """扫库必须同时选择服务器与媒体库，避免误刷整个媒体库"""
        if bool(self.refresh_server) != bool(self.refresh_library_id):
            raise ValueError("扫库需要同时填写媒体服务器名称和媒体库 ID")
        if self.archive_path and not self.archive_folder_id:
            raise ValueError("云端归档路径需要同时填写归档文件夹 ID")
        return self

    @field_validator("account_ids")
    @classmethod
    def safe_account_ids(cls, value: list[str]) -> list[str]:
        """只接受唯一且符合账号标识规范的选择"""
        if len(value) != len(set(value)) or any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", item) for item in value):
            raise ValueError("请选择有效且不重复的上传账号")
        return value

    @field_validator("path", "archive_path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        """网盘路径为相对于目标文件夹的安全路径"""
        if value.startswith("/") or any(part in {".", ".."} for part in value.split("/")) or re.search(r"[\x00-\x1f\\]", value):
            raise ValueError("网盘路径必须为相对路径，不能包含上级目录")
        return "/".join(part for part in value.split("/") if part)

    @field_validator("strm_prefix")
    @classmethod
    def single_line_prefix(cls, value: str) -> str:
        """播放前缀只能占一行，避免生成含额外播放地址的 STRM"""
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("STRM 播放前缀不能包含控制字符")
        return value.strip()


class GdriveUploadCreate(BaseModel):
    """单文件直接上传请求，可独立使用且默认保留源文件"""

    source_path: str = Field(min_length=1, max_length=4096)
    target: GdriveUploadTarget
    strm_root: str = ""
    remove_source: bool = False


class GdriveSettings(BaseModel):
    """Google Drive 挂载目录功能配置。"""

    enabled: bool = False
    mount_path: str = ""
    archive_path: str = ""
    strm_path_prefix: str = ""
    remove_source: bool = True
    version_suffix: str = Field(default="version", min_length=1, max_length=32)
    source_root: str = ""
    copy_chunk_size: int = Field(default=10 * 1024 * 1024, ge=1024 * 1024)


class GdriveSettingsUpdate(GdriveSettings):
    """Google Drive 挂载目录功能配置更新请求。"""


class GdriveTaskCreate(BaseModel):
    """手动创建 Google Drive 后处理任务请求。"""

    source_path: str = Field(..., min_length=1)
    archive_path: Optional[str] = None
    remove_source: Optional[bool] = None


class GdrivePreflightRequest(BaseModel):
    """Google Drive 后处理预检请求。"""

    source_path: str = Field(..., min_length=1)
    archive_path: Optional[str] = None


class GdrivePreflightCheck(BaseModel):
    """Google Drive 后处理预检项目。"""

    name: str
    passed: bool
    level: str
    message: str


class GdrivePreflightResult(BaseModel):
    """Google Drive 后处理预检结果。"""

    ready: bool
    source_path: str
    mount_path: str
    archive_path: Optional[str] = None
    strm_path: Optional[str] = None
    source_size: Optional[int] = None
    checks: list[GdrivePreflightCheck] = Field(default_factory=list)


class GdriveTaskCleanupRequest(BaseModel):
    """Google Drive 后处理任务清理请求。"""

    dry_run: bool = True


class GdriveTaskCleanupResult(BaseModel):
    """Google Drive 后处理任务清理结果。"""

    task_id: int
    status: Optional[str] = None
    dry_run: bool
    can_cleanup: bool
    message: str
    candidates: list[str] = Field(default_factory=list)
    removed_paths: list[str] = Field(default_factory=list)
    retained_paths: list[str] = Field(default_factory=list)


class GdriveTaskHistoryInfo(BaseModel):
    """Google Drive 后处理任务状态历史响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    from_status: Optional[str] = None
    to_status: str
    from_stage: Optional[str] = None
    to_stage: str
    event_type: str
    error_message: Optional[str] = None
    retry_count: int = 0
    detail: Optional[str] = None
    created_at: str


class GdriveTaskInfo(BaseModel):
    """Google Drive 后处理任务响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    backend: str = "mount"
    upload_account_id: Optional[str] = None
    remote_file_id: Optional[str] = None
    upload_target: Optional[dict] = None
    src_path: str
    archive_path: Optional[str] = None
    strm_path: Optional[str] = None
    source_size: int = 0
    uploaded_size: int = 0
    status: str
    stage: str
    progress: int = 0
    error_message: Optional[str] = None
    retry_count: int = 0
    remove_source: bool = True
    media_type: Optional[str] = None
    title: Optional[str] = None
    transfer_history_id: Optional[int] = None
    created_at: str
    updated_at: str


class GdriveRuleConfig(BaseModel):
    """可复用的电影、剧集命名与分类规则，仅使用本地解析信息"""

    name: str = Field(min_length=1, max_length=100)
    category_policy: Optional["GdriveCategoryPolicy"] = None
    media_type: Literal["auto", "movie", "tv"] = "auto"
    movie_directory: str = Field(default="{{ title }} ({{ year }})", min_length=1, max_length=500)
    movie_filename: str = Field(default="{{ title }} ({{ year }})", min_length=1, max_length=500)
    tv_directory: str = Field(default="{{ title }} ({{ year }})/Season {{ season }}", min_length=1, max_length=500)
    tv_filename: str = Field(default="{{ title }} - {{ season_episode }}", min_length=1, max_length=500)
    categories: list[Literal["media_type", "year", "resolution", "video_codec", "team"]] = Field(
        default_factory=lambda: ["media_type"], max_length=5,
    )

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        """拒绝空白规则名称"""
        if not value.strip():
            raise ValueError("请填写规则名称")
        return value.strip()

    @field_validator("movie_directory", "movie_filename", "tv_directory", "tv_filename")
    @classmethod
    def validate_template(cls, value: str) -> str:
        """只允许明确的媒体变量，避免模板执行代码或隐式访问属性"""
        allowed = {"title", "year", "tmdb_id", "season", "episode", "season_episode",
                   "resolution", "video_codec", "audio_codec", "team", "original_name", "media_type"}
        tokens = re.findall(r"\{\{\s*(\w+)\s*\}\}", value)
        remainder = re.sub(r"\{\{\s*\w+\s*\}\}", "", value)
        if (set(tokens) - allowed or any(char in remainder for char in "{}\0\\")
                or value.startswith("/") or any(part in {".", ".."} for part in value.split("/"))):
            raise ValueError("模板只能使用支持的 {{ 变量 }}，且不能包含绝对路径或上级目录")
        return value

    @model_validator(mode="after")
    def validate_filenames(self) -> "GdriveRuleConfig":
        """文件名模板不允许创建目录，分类层级不能重复"""
        if "/" in self.movie_filename or "/" in self.tv_filename:
            raise ValueError("文件名模板不能包含目录分隔符")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("分类层级不能重复")
        return self


class GdriveRuleInfo(GdriveRuleConfig):
    """规则及其持久化版本"""

    id: int
    revision: int
    updated_at: str


class GdriveRulePreview(BaseModel):
    """命名规则只读样例请求，不访问媒体或外部识别服务"""

    rule: GdriveRuleConfig
    filename: str = Field(default="Example.Movie.2024.2160p.H265.mkv", min_length=1, max_length=500)
    media: dict = Field(default_factory=dict)


class GdriveCategoryRule(BaseModel):
    """按顺序匹配的离线二级分类条件"""

    name: str = Field(min_length=1, max_length=500)
    conditions: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        """允许安全的多层相对目录，逐段拒绝穿越和非法名称"""
        value = value.strip()
        if any(not part or part in {".", ".."} or part != part.strip()
               or re.search(r'[\x00-\x1f\\:*?"<>|]', part) or part.endswith(".")
               or len(part.encode("utf-8")) > 255 for part in value.split("/")):
            raise ValueError("分类名称必须是安全的相对目录，可用 / 分隔多层目录")
        return value

    @field_validator("conditions")
    @classmethod
    def normalize_conditions(cls, value: dict[str, str]) -> dict[str, str]:
        """限制条件长度及年份范围，避免无界范围展开"""
        result = {}
        for key, condition in value.items():
            key = key.replace("？", "?", 1)
            if key.lstrip("?") not in {"original_language", "origin_country", "production_countries",
                                       "genre_ids", "release_year", "year", "keywords", "include_keywords",
                                       "series_keywords", "series_actors", "series_directors"} or key.startswith("??"):
                raise ValueError("分类条件字段名称无效")
            tokens = [token.strip() for token in condition.split(",") if token.strip()]
            if len(condition) > 10000 or not tokens or any(not token.lstrip("+!-") for token in tokens):
                raise ValueError("分类条件不能为空且不能超过 10000 个字符")
            if key.lstrip("?") in {"release_year", "year"} and any(
                not re.fullmatch(r"[+!\-]?\d{4}(?:-\d{4})?", token)
                or ("-" in token.lstrip("+!-") and int(token.lstrip("+!-")[:4]) > int(token[-4:])) for token in tokens
            ):
                raise ValueError("年份条件须为 YYYY 或 YYYY-YYYY，可用 - 或 ! 排除")
            if key in result:
                raise ValueError("分类条件字段不能重复")
            result[key] = ",".join(tokens)
        return result


class GdriveCategoryPolicy(BaseModel):
    """电影和剧集分别配置的有序分类策略"""

    movie: list[GdriveCategoryRule] = Field(default_factory=list, max_length=100)
    tv: list[GdriveCategoryRule] = Field(default_factory=list, max_length=100)
    unmatched: Literal["skip", "fallback", "keep"] = "skip"
    fallback: str = "未分类"
    path_mode: Literal["media_type", "relative"] = "media_type"

    @field_validator("fallback")
    @classmethod
    def safe_fallback(cls, value: str) -> str:
        """未匹配目录使用同一安全命名约束"""
        return GdriveCategoryRule.safe_name(value)

    @model_validator(mode="after")
    def unique_names(self) -> "GdriveCategoryPolicy":
        """同一媒体类型不能出现重复分类名称"""
        for rules in (self.movie, self.tv):
            if len({rule.name for rule in rules}) != len(rules):
                raise ValueError("同一媒体类型的分类名称不能重复")
        return self


GdriveRuleConfig.model_rebuild()
GdriveRuleInfo.model_rebuild()
GdriveRulePreview.model_rebuild()


class GdriveCategoryImport(BaseModel):
    """导入有序 YAML 分类，保留用户选择的路径和未匹配策略"""

    content: str = Field(min_length=1, max_length=262144)
    policy: GdriveCategoryPolicy = Field(default_factory=GdriveCategoryPolicy)


class GdriveDirectoryConfig(BaseModel):
    """可保存的目录归档任务及过滤和调度参数"""

    name: str = Field(min_length=1, max_length=100)
    source_path: str = Field(min_length=1, max_length=4096)
    archive_path: str = Field(min_length=1, max_length=4096)
    enabled: bool = False
    recursive: bool = True
    remove_source: bool = False
    extensions: list[str] = Field(default_factory=lambda: [".mkv", ".mp4", ".avi", ".mov", ".m2ts", ".ts"], min_length=1, max_length=50)
    ignore_keywords: list[str] = Field(default_factory=lambda: ["sample", "trailer", "bonus", ".tmp"], max_length=100)
    ignore_suffixes: list[str] = Field(default_factory=list, max_length=50)
    min_size_mb: int = Field(default=0, ge=0, le=1048576)
    settle_seconds: int = Field(default=300, ge=0, le=604800)
    schedule_enabled: bool = False
    interval_minutes: int = Field(default=30, ge=1, le=43200)
    rule_id: Optional[int] = Field(default=None, ge=1)
    classification_mode: Literal["inherit", "moviepilot", "custom"] = "inherit"
    category_policy: GdriveCategoryPolicy = Field(default_factory=GdriveCategoryPolicy)
    transfer_enabled: bool = True
    strm_root: str = Field(default="", max_length=4096)
    upload_target: Optional[GdriveUploadTarget] = None

    @field_validator("name", "source_path", "archive_path", "strm_root", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """清除字段两端的空白"""
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_path", "archive_path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        """目录参数必须是明确的绝对路径"""
        if not Path(value).is_absolute() or "\0" in value:
            raise ValueError("必须填写绝对路径")
        return value

    @field_validator("strm_root")
    @classmethod
    def absolute_strm_root(cls, value: str) -> str:
        """旧配置留空时继续在源文件旁输出 STRM"""
        if value and (not Path(value).is_absolute() or "\0" in value):
            raise ValueError("STRM 根目录必须是绝对路径")
        return value

    @field_validator("extensions", "ignore_suffixes", "ignore_keywords")
    @classmethod
    def normalize_filters(cls, values: list[str]) -> list[str]:
        """规范过滤项并限制单项长度"""
        result = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        if any(len(value) > 100 or "\0" in value for value in result):
            raise ValueError("过滤项长度不能超过 100 个字符")
        return result

    @model_validator(mode="after")
    def validate_extensions(self) -> "GdriveDirectoryConfig":
        """扩展名白名单不能包含 STRM 或临时文件"""
        if not self.extensions or any(
            not value.startswith(".") or value in {".strm", ".tmp", ".part"}
            or any(char in value for char in "/\\*? ") for value in self.extensions
        ):
            raise ValueError("请填写有效的媒体扩展名，不能使用 STRM 或临时文件")
        return self


class GdriveDirectoryInfo(GdriveDirectoryConfig):
    """目录任务及其最近运行时间"""

    id: int
    revision: int
    next_run_at: Optional[str] = None
    last_scan_at: Optional[str] = None
    created_at: str
    updated_at: str


class GdriveCategoryPreview(BaseModel):
    """使用用户提供的离线样例预览分类，不读取媒体或请求外部服务"""

    config: GdriveDirectoryConfig
    filename: str = Field(default="Example.Movie.2024.mkv", min_length=1, max_length=500)
    media: dict = Field(default_factory=dict)


class GdriveScanRequest(BaseModel):
    """扫描默认为只读预览，显式运行才会创建文件任务"""

    dry_run: bool = True


class GdriveScanInfo(BaseModel):
    """持久化扫描批次及文件执行汇总"""

    id: int
    directory_id: int
    directory_name: str
    trigger: str
    dry_run: bool
    status: str
    scanned: int = 0
    matched: int = 0
    skipped: int = 0
    queued: int = 0
    completed: int = 0
    failed: int = 0
    active: int = 0
    error_message: Optional[str] = None
    created_at: str
    finished_at: Optional[str] = None


class GdriveScanItemInfo(BaseModel):
    """扫描文件的过滤原因、目标路径及实际处理状态"""

    id: int
    source_path: str
    archive_path: Optional[str] = None
    size: int = 0
    reason: str
    task_id: Optional[int] = None
    status: Optional[str] = None
    stage: Optional[str] = None
    progress: int = 0
    error_message: Optional[str] = None


class GdriveScanDetail(BaseModel):
    """单次扫描详情，文件清单最多显示五百项"""

    scan: GdriveScanInfo
    items: list[GdriveScanItemInfo]
    total_items: int
    config: Optional[GdriveDirectoryConfig] = None
    rule: Optional[GdriveRuleInfo] = None

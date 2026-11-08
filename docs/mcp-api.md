# MoviePilot MCP (Model Context Protocol) API 文档

Google Drive 上传通知：`GET/PUT /api/v1/gdrive/upload/settings` 新增 `notifications_enabled=false`、`daily_report_enabled=false`、`notification_source=""`。超级管理员可启用 Telegram 阶段记录与每24小时汇总，指定通知中心已有渠道名称；留空选择首个匹配渠道，须允许“其它”场景。启用后每分钟检查历史，即使上传总开关关闭也继续通知。`upload_verified`、`cloud_archived`、`strm_generated` 按任务首次成功去重，失败按每次失败记录发送。只有实际云端核验或 STRM 写入后才产生成功记录，STRM 成功不代表扫库或播放验收成功。通知发送使用现有渠道的确认接口，确认后写通知中心历史、通知事件及任务回执；发送失败下次调度补发，不改变媒体任务。仅覆盖 API 上传链，旧挂载模式不计入。24小时窗口从首次启用开始，按应用本地时间展示左闭右开窗口，持久化窗口及报告分段进度，零上传也发送；总量按每个任务首次上传核验的 source_size 计量，不计重试流量。报告包含上传、归档、STRM、失败明细并分段，停机后逐窗口补发。Telegram 成功后、回执提交前若进程崩溃，可能补发同一条；Telegram 不提供事务性恰好一次送达，不能保证绝对不重复。

共用 Telegram Bot：通知渠道 config 可设置 `TELEGRAM_RECEIVE_ENABLED=false`，只发送、不启动 polling、不覆盖命令菜单；默认 true 保留旧行为。通知模板使用 HTML 并转义文件名及路径，失败通知不转发可能含凭据的原始异常。Symedia 默认模板若受保护且未配置自定义值，仅参考其归档通知风格，不承诺逐字复刻。

下载器离线整理：当 `TMDB_API_KEY` 显式为空、任务尚无媒体信息且源存储为本地时，整理链可读取已配置下载根内的同名 NFO／movie.nfo，或向上最多三层的 tvshow.nfo。只接受具有标题和数字年份的 movie／tvshow，拒绝符号链接、外部实体及超过 1 MiB 的 NFO；单集标题不会用作剧名。离线身份为 `local_nfo`，不补充 TMDB 信息。下载整理目录应关闭在线刮削及依赖 TMDB 的自动类别目录，由 GDrive 已有分类规则完成上传后的分类归档。若代码默认密钥非空，必须通过系统设置显式保存空值，重启后核验，不能仅省略配置项。

启用本地资料的 GDrive 上传目录收到整理完成事件后，会先把下载源的随片 NFO／图片准备到本地整理目录，再提交上传；此时不生成 STRM。缺资料或内容冲突时停止事件接管并保留源文件。最终 STRM 与旁边资料仍在云端归档核验后生成，随后请求指定媒体库扫描。正式部署需配置下载器下载根、本地整理根、共享盘 drmedia／media 两个不同根、对应 media 的只读播放入口，以及 Emby 可读的 STRM 根；上述目录和服务应分别核验持久化及权限。

Google Drive 上传目标可选 `archive_folder_id`、`archive_path`（默认空）。`folder_id/path` 指向上传暂存根（如 drmedia），`archive_folder_id/archive_path` 指向同一共享盘的最终归档根（如 media），`strm_prefix` 对应最终归档根的播放地址。目录任务保留源目录结构上传，再按批次冻结的离线分类结果移动云端文件；路径可先只读预览，实际归档移动必须在上传核验后。单文件任务的 archive_path 为含文件名的最终路径，留空沿用上传路径。归档移动保留原云端 ID，持久保存父目录和名称，重新查询并核验后才生成 STRM、复用本地资料、请求定向扫库。移动响应不确定时不自动重发，显式重试先查询同一 ID；重名或归属冲突保留暂存及本地源文件。已进入归档的文件缺失时停止重传。两个根目录不能相同；留空 archive_folder_id 兼容原直接上传。无需数据库迁移，目标字段随任务 JSON 冻结。

Emby 使用说明：服务器名称填写已启用的配置名，例如 `本机 Emby`；媒体库 ID 为该服务器的库编号。电视库的各扫描入口应直接包含各部剧目录；使用多层二次分类时，将最终分类目录分别加入同一个电视库，避免分类目录被识别成剧名。若要求只使用本地资料，需要在对应 Emby 库关闭在线元数据和图片提供方，并保持本地 NFO 读取启用；NFO 的 lockdata 本身不代表整个 Emby 服务不会联网。此功能不会自动修改现有媒体库提供方设置，也不对扫库提速作未测量的承诺。

Google Drive API 上传目标新增可选 `metadata_enabled`（默认 false）、`refresh_server` 与 `refresh_library_id`（默认空）。启用本地元数据时必须配置 `strm_root`，上传及云端校验、STRM 生成后，复用本地 NFO 与图片输出到 STRM 旁，不在线识别。电影优先同名 NFO 再 movie.nfo；剧集需要 tvshow.nfo，按 Season 目录输出剧和集 NFO，已有季集号可生成基础集信息。图片复用本地 poster/fanart/backdrop 等；移除 NFO 中远端图片引用并标记锁定。缺资料、非法 XML、符号链接或目标冲突导致任务失败并保留源文件，不覆盖已有元数据。设置刷新服务器名称与库 ID 后，元数据完成再请求指定 Emby 媒体库扫描；HTTP 200/202/204 只表示扫描请求接受，不等于条目入库或播放成功。两字段须同时填写，禁止隐式全库刷新。目标设置随任务 JSON 冻结，无数据库迁移；旧续传会话按新模型补默认值兼容。仅 API 直接上传流程提供此后处理；旧挂载复制暂不支持。

MoviePilot 实现了标准的 **Model Context Protocol (MCP)**，允许 AI 智能体（如 Claude, GPT 等）直接调用 MoviePilot 的功能进行媒体管理、搜索、订阅和下载。

## 1. 基础信息

*   **基础路径**: `/api/v1/mcp`
*   **协议版本**: `2025-11-25, 2025-06-18, 2024-11-05`
*   **传输协议**: HTTP (JSON-RPC 2.0)
*   **认证方式**: 
    *   Header: `X-API-KEY: <你的API_KEY>`
    *   Query: `?apikey=<你的API_KEY>`

### 安全提示

MCP 使用系统配置中的 `API_TOKEN` 作为认证密钥，文档中的 API KEY 是请求字段名。该密钥应按管理员级 secret 保管，持有者可作为受信第三方集成调用暴露的 MoviePilot 工具。

- 优先使用 `X-API-KEY` 请求头；查询参数更容易出现在代理、浏览器或客户端日志中。
- 不要在缺少 HTTPS、访问控制和网络隔离的情况下，将 MCP、OpenAI 或 Anthropic 兼容接口直接暴露到公网。
- MCP 隐藏工具列表只用于减少默认暴露面，不是 per-user 权限系统。

## 2. 标准 MCP 协议 (JSON-RPC 2.0)

### 端点
**POST** `/api/v1/mcp`

### 支持的方法
- `initialize`: 初始化会话，协商协议版本和能力。
- `notifications/initialized`: 客户端确认初始化完成。
- `tools/list`: 获取可用工具列表。
- `tools/call`: 调用特定工具。
- `ping`: 连接存活检测。

### 动态插件工具

`tools/list` 会同时返回 MoviePilot 内置工具和已启用插件通过 `get_agent_tools()` 声明的工具。插件启动、停止、重载或配置生效后，MCP 工具管理器会在下一次列出或调用工具时按注册表版本惰性刷新，避免继续暴露已移除的工具或遗漏新工具。

MCP 当前不会主动发送工具列表变更通知（`listChanged=false`）。如果客户端缓存了工具列表，插件状态变化后需要让客户端重新请求 `tools/list`；无法手动刷新的客户端应重新连接 MCP 服务或新建会话。

---

## 4. 客户端配置示例

### Claude Desktop (Anthropic)

在Claude Desktop的配置文件中添加MoviePilot的MCP服务器配置：

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

使用请求头方式：
```json
{
  "mcpServers": {
    "moviepilot": {
      "url": "http://localhost:3001/api/v1/mcp",
      "headers": {
        "X-API-KEY": "your_api_key_here"
      }
    }
  }
}
```

或使用查询参数方式：
```json
{
  "mcpServers": {
    "moviepilot": {
      "url": "http://localhost:3001/api/v1/mcp?apikey=your_api_key_here"
    }
  }
}
```

## 4.1 Agent 外部 MCP Client 配置

MoviePilot 的内置 Agent 也可以作为 MCP Client 连接外部 MCP 服务器，将外部工具注入到智能助手工具列表中。当前支持：

- `stdio`：按配置的命令和参数启动本地 MCP 进程，通过标准输入输出交换 JSON-RPC 消息。
- `sse`：连接旧版 HTTP+SSE MCP 服务，先读取 `endpoint` 事件，再向返回的 endpoint POST JSON-RPC 消息。
- `http` / `streamable_http`：连接 Streamable HTTP MCP 服务，直接向配置 URL POST JSON-RPC 消息。

这些配置是管理员级 Agent 运行时配置，保存在 `SystemConfigKey.AIAgentMcpServers` 中。外部 MCP 工具默认要求管理员上下文调用，避免普通用户触发高权限外部工具。

### Agent MCP 配置接口

这些接口使用登录态鉴权，并要求当前用户为超级管理员。

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/message/agent/mcp/servers` | 查询已配置的外部 MCP 服务器列表 |
| POST | `/api/v1/message/agent/mcp/servers` | 保存外部 MCP 服务器列表 |
| POST | `/api/v1/message/agent/mcp/servers/test` | 测试单个外部 MCP 服务器，返回发现的工具列表 |

## 5. 错误码说明

| 错误码 | 消息 | 说明 |
| :--- | :--- | :--- |
| -32700 | Parse error | JSON 格式错误 |
| -32600 | Invalid Request | 无效的 JSON-RPC 请求 |
| -32601 | Method not found | 方法不存在 |
| -32602 | Invalid params | 参数验证失败 |
| -32002 | Session not found | 会话不存在或已过期 |
| -32003 | Not initialized | 会话未完成初始化流程 |
| -32603 | Internal error | 服务器内部错误 |

## 6. RESTful API
所有工具相关的API端点都在 `/api/v1/mcp` 路径下（保持向后兼容）。

### 相关 REST 端点

MoviePilot 也提供普通 REST API 给前端和自动化客户端使用。所有接口同样需要 API KEY 认证，在请求头中添加 `X-API-KEY: <api_key>` 或在查询参数中添加 `apikey=<api_key>`。

#### REST API 版本

- `/api/v1` 默认保持原有响应结构，已有客户端无需迁移；登录壁纸接口的 URL 已统一放入 `data`。
- `/api/v2` 复用 `/api/v1` 的同一套路由、请求参数、鉴权依赖和业务实现，只统一普通 JSON 响应结构。
- v1 中已经使用通用 `Response` 的接口在 v2 中保持原样；其他成功 JSON 响应转换为 `{"success": true, "message": "", "data": <原响应>}`。
- HTTP 错误保留原状态码，并统一返回 `{"success": false, "message": <错误详情>, "data": {}}`；非业务异常不做多语言翻译。
- SSE、文件、图片、空响应，以及 OpenAI、Anthropic、MCP 等标准协议接口保持原始响应格式，不进行通用封装。

因此，普通 REST 接口可将文档中的 `/api/v1/...` 路径直接替换为 `/api/v2/...`。例如 `/api/v1/download/` 对应 `/api/v2/download/`。

#### Google Drive 上传与挂载目录后处理

直接上传通过 Google Drive API 将本地媒体上传到团队共享盘，使用独立开关，不依赖挂载目录。支持 OAuth 用户 Token JSON（含 rclone 格式）；自动续期需要 refresh_token、client_id 和 client_secret，暂不支持服务账号 JSON 或浏览器授权。公开账号及并发设置由 `SystemConfigKey.GdriveUploadSettings` 保存，凭据与续传会话仅保存在服务器配置目录的 `gdrive-upload` 下，目录权限 0700、文件权限 0600，接口不回显 Token。

上传目标 `target` 包含 account_ids、drive_id、folder_id、path 和 strm_prefix。任务冻结目标及可选账号，按账号最小负载分配，总并发与每账号并发共同限制，每个文件固定一个账号续传。上传前预分配文件 ID，重试先查询同一 ID 和服务端偏移；会话过期后沿用该 ID 重建。网络或 5xx 等写入结果不确定时任务失败，须显式重试核对云端，不自动重发写请求。上传后再次查询并核验文件 ID、名称、大小、MD5、共享盘、父目录和任务归属，通过后才生成普通 STRM 并执行源文件策略，默认保源。

API 上传仅支持单进程调度；总并发、账号并发和相同 drive_id/folder_id/path 的目标串行限制在当前进程内生效。数据库租约防止同一任务重复领取，但不提供跨进程的账号配额或目标锁。不同根目录 ID 与相对路径可能指向同一云端位置，应统一目标根目录，并避免其他程序同时写入相同目标；云端同名检查不能代替分布式排他锁。取消保留续传会话与已写入的云端内容，旧挂载清理接口拒绝清理 API 上传会话。自动测试全程模拟 Google API，真实上传验收须指定共享盘及专属测试目录。

挂载复制接口用于把整理完成的本地媒体文件复制到已有的 Google Drive 本地挂载目录，需要管理员登录态，归档路径必须位于挂载目录内。任务使用 SHA-256 校验复制内容，重复归档会保留独立版本（如 `name (version 2).ext`），STRM 同名时也会以版本名发布而不会覆盖既有文件。数据库租约防止不同进程同时执行同一任务；长时间操作会续约，失去租约的 worker 必须停止。

挂载文件系统应支持排他创建（`O_EXCL` 语义）和文件 SHA-256 读写；硬链接可用时优先用硬链接归档，不支持时回退到任务专属标记加排他复制。只有 `EPERM`、`ENOTSUP`、`EOPNOTSUPP` 或跨文件系统的 `EXDEV` 才会触发硬链接回退，`EIO` 等 I/O 错误会直接让任务失败，避免把挂载故障误判为能力限制。FUSE/云盘对排他创建、写入可见性、`fsync` 和原子改名的实现各不相同，必须在实际挂载验证，普通本地文件系统测试不能替代。中断复制仅在任务标记存在且归档确认为暂存内容前缀时续写；无凭据或内容不匹配时保留现有归档并让任务失败。

删除源文件前会先在源目录隔离，再比对任务创建时记录的设备号、inode 和源文件 SHA-256；身份无法验证、旧任务缺少任一身份字段或复核失败时保留文件。隔离依赖源文件系统支持同目录原子改名。该方式缩小外部并发窗口，但不构成对所有网络/FUSE 文件系统的通用保证。未在实际挂载完成验证前，建议关闭源文件自动删除；真实挂载验证至少覆盖排他创建、复制/续写、文件可见性、`fsync` 和同目录改名。

现场回归测试位于 `tests/test_gdrive_mount.py`。测试只接受显式的系统挂载点，不会把普通目录当作 Google Drive/FUSE 挂载：

```bash
GDRIVE_TEST_MOUNT_PATH=/绝对路径/GoogleDrive挂载点 pytest tests/test_gdrive_mount.py
```

测试会在挂载点下创建唯一的 `.mymediafolo-live-regression-*` 目录，并在每个测试结束后清理。覆盖硬链接能力探测及排他复制回退、`O_EXCL` 多进程竞争、写入后 `fsync`、单次原子改名、并发读取下的原子改名，以及并发归档恢复。硬链接不支持时必须能完成排他复制回退；并发读取下的原子改名若出现非 `EIO` 错误会严格失败，已知挂载在该压力场景返回 `EIO` 时标记为跳过，并记录为不满足安全原子发布假设的能力限制。该限制不改变业务代码对归档硬链接或其他真实 I/O 错误的失败处理。未设置 `GDRIVE_TEST_MOUNT_PATH` 时测试会跳过。

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/gdrive/upload/settings` | 管理用户读取独立 API 上传开关、总并发、分块大小和公开账号列表及凭据是否存在；不返回 Token |
| PUT | `/api/v1/gdrive/upload/settings` | 超级管理员设置 enabled、concurrency（1～16）、chunk_size_mb（1～64）、accounts（id/name/enabled/concurrency）；每账号并发 1～16 |
| PUT | `/api/v1/gdrive/upload/accounts/{id}/credential` | 超级管理员只写 token（JSON 字符串）、client_id、client_secret；服务器私有文件保存，禁止记录和回显 |
| POST | `/api/v1/gdrive/upload/check` | 超级管理员显式检查所选账号对共享盘文件夹的权限；只读网盘，不创建文件 |
| POST | `/api/v1/gdrive/upload/tasks` | 管理用户创建直接上传任务；source_path、target、strm_root（可选）、remove_source（默认 false） |
| GET | `/api/v1/gdrive/settings` | 查询挂载目录、归档目录、STRM 路径映射和源文件策略 |
| PUT | `/api/v1/gdrive/settings` | 超级管理员更新挂载目录后处理配置 |
| POST | `/api/v1/gdrive/preflight` | 只读检查源文件、挂载目录、真实挂载点、写权限和归档路径；不会创建目录或写入测试文件 |
| GET | `/api/v1/gdrive/tasks` | 按状态查询任务及上传进度、阶段和错误信息 |
| GET | `/api/v1/gdrive/tasks/{task_id}` | 按编号读取文件任务，供历史批次查看完整执行流程 |
| GET | `/api/v1/gdrive/tasks/{task_id}/history` | 查询任务状态、阶段、失败和重试历史 |
| POST | `/api/v1/gdrive/tasks` | 创建旧挂载复制任务；直接上传应使用 `/upload/tasks` |
| POST | `/api/v1/gdrive/tasks/{task_id}/retry` | 重试失败或取消的任务 |
| POST | `/api/v1/gdrive/tasks/{task_id}/cancel` | 取消等待中或执行中的任务；临时文件会保留用于断点恢复 |
| POST | `/api/v1/gdrive/tasks/{task_id}/cleanup` | 超级管理员预览或清理已取消/已完成任务的任务专属临时文件和归属标记；默认 `dry_run=true`，失败任务始终保留临时文件 |
| GET | `/api/v1/gdrive/directories` | 管理用户读取已保存的目录任务、过滤条件和下次扫描 UTC 时间 |
| POST | `/api/v1/gdrive/directories` | 超级管理员保存目录任务；默认停用、保留源文件，不立即扫描 |
| PUT | `/api/v1/gdrive/directories/{directory_id}` | 超级管理员修改目录任务，存在活动批次时拒绝修改 |
| DELETE | `/api/v1/gdrive/directories/{directory_id}` | 超级管理员删除停用且无活动批次的配置，保留历史和文件任务 |
| POST | `/api/v1/gdrive/directories/{directory_id}/scan` | 超级管理员触发扫描；请求体默认 `{"dry_run":true}`，显式 false 才启动实际归档 |
| GET | `/api/v1/gdrive/scans` | 管理用户查询扫描和执行历史；可按 directory_id 筛选，limit 默认 100、上限 500 |
| GET | `/api/v1/gdrive/scans/{scan_id}` | 管理用户查看批次汇总、过滤原因和关联文件状态；明细最多前 500 项，total_items 为实际总数 |
| GET | `/api/v1/gdrive/rules` | 管理用户读取可复用命名规则与版本 |
| POST | `/api/v1/gdrive/rules/preview` | 管理用户提交 rule 和 filename，仅解析样例文件名并返回相对目标路径，不访问外部识别或写文件 |
| POST | `/api/v1/gdrive/categories/preview` | 管理用户提交 config、filename 和离线 media 样例，返回相对归档路径 path 与当前冻结策略 policy，不写媒体文件 |
| POST | `/api/v1/gdrive/categories/import` | 管理用户提交 content（YAML，最多 256 KiB）及 policy（路径／未匹配选项），返回有序草稿 policy 与 warnings，不保存或扫描 |
| POST | `/api/v1/gdrive/rules` | 超级管理员新增归档规则 |
| PUT | `/api/v1/gdrive/rules/{rule_id}` | 超级管理员修改规则；已有扫描使用旧规则快照 |
| DELETE | `/api/v1/gdrive/rules/{rule_id}` | 超级管理员删除未被目录任务引用的规则 |

目录任务支持 `name`、绝对路径 `source_path` / `archive_path`、`enabled`、`recursive`、`remove_source`、`extensions`、`ignore_keywords`、`ignore_suffixes`、`min_size_mb`、`settle_seconds`、`schedule_enabled` 和 `interval_minutes`。大小单位为 MiB；过滤不区分大小写，扩展名必须带点，拒绝 STRM 和临时文件后缀。源目录与目标目录不能嵌套或包含符号链接，归档目标必须位于配置挂载点内；预览也要求有效的系统挂载点。扫描跳过符号链接、子挂载点和命中忽略关键词的子目录；每次最多遍历 10000 个目录项，超过上限则不提交本批次文件。

目录任务可通过可空的 `rule_id` 引用命名规则；未引用时保留相对目录结构。规则支持 `name`、`media_type`（auto/movie/tv）、`movie_directory`、`movie_filename`、`tv_directory`、`tv_filename` 和有序 `categories`（media_type/year/resolution/video_codec/team，最多五层且不重复）。目录模板允许多层相对目录，文件模板不含扩展名及目录分隔符。变量格式为 `{{ title }}`，支持 title/year/tmdb_id/season/episode/season_episode/resolution/video_codec/audio_codec/team/original_name/media_type；不支持代码、条件表达式、属性访问或其他 Jinja2 语法。扩展名自动保留。规则通过 `SystemConfigKey.GdriveRules` / `SystemConfigOper` 持久化，不新增数据库表。

媒体信息使用本地 `MetaInfoPath` 解析，不请求 TMDB 或其他识别服务。TMDB ID 仅来自文件名已有标记；缺失模板字段、跨季范围、非法或过长路径会记录 `error:` 原因并保留源文件；缺失分类信息归为“未分类”。规则预览只解析单个样例文件名，真实扫描可合并父目录信息。已有目标沿用原归档链的版本文件策略，不覆盖已有媒体。

保存配置、只读预览和实际运行是独立操作，实际运行重新检查源文件身份和目标路径。文件任务沿用现有归档和 STRM 流程。扫描记录目录配置及规则版本快照，详情返回 `config`、`rule`，文件项返回实际 `stage` 与 `progress`。数据库唯一活动键防止同一目录任务重入；文件指纹包含源路径、目标路径、设备、inode、大小和修改时间，同一版本的失败或取消任务也不会因再次扫描自动重建。需要重试时使用文件任务重试接口。

现有调度器每 30 秒检查到期目录，任务与总功能均启用时才执行；间隔为 1 至 43200 分钟，时间统一为 UTC。服务重启会把未结束的扫描标为 interrupted，保留已提交文件的关联并通过原文件状态机恢复；已入队文件结束前继续阻止同一目录任务重入。扫描失败项计入 failed，过滤项计入 skipped。复制完成不等于 Google Drive 云端同步完成。

目录任务另支持 `classification_mode`（默认 `inherit` 沿用整理目录及所选规则，`moviepilot` 只读复用当前 category.yaml，`custom` 独立分类）、`category_policy`、`transfer_enabled`（默认 true）和 `strm_root`（默认空）。`inherit` 在规则未配置二次分类时保持既有行为；启用二次分类时按 path_mode、分类目录与命名模板生成路径，忽略旧 categories 层级以避免重复。未选择命名规则时使用默认标题、年份和季集模板。

`category_policy.movie` 与 `.tv` 是有序 `{name, conditions}` 数组；同一类型名称不能重复，名称允许安全多层相对路径。`path_mode=media_type`（旧策略默认）自动添加电影／电视剧，`relative` 直接相对归档根目录。支持 original_language、origin_country、production_countries、genre_ids、release_year / year、keywords（标题子串）、include_keywords（已有标签子串）及 series_keywords / series_actors / series_directors。普通条件全部满足，带 `?` / `？` 的条件组至少一个满足；逗号普通值至少一个，`+` 值全部必需，`-` / `!` 排除，年份范围包含边界，空条件兜底。系列字段只生成首个匹配词的“关键词系列”子目录，仅系列规则须命中系列；组合规则始终要求可选组命中，不沿用 Symedia 文档中与总则不一致的系列示例。缺失字段不满足排除条件。unmatched 支持 skip / fallback / keep。YAML 拒绝重复键、别名、未知字段和非法路径；导入返回草稿和诊断，不保存。MoviePilot 模式只读冻结 category.yaml。

可复用归档规则可选 `category_policy`（默认 null），继续保存在 `SystemConfigKey.GdriveRules`，无数据库结构变化。目录 inherit 模式且所选规则有分类时使用该分类，custom / moviepilot 优先覆盖；已有批次通过 rule_snapshot 冻结。`POST /rules/preview` 另接收离线 media 对象，与实际目录共用分类和命名计算。NFO 可复用 tag、actor/name、director，事件快照保留已有标签与演职员，不新增网络识别。

分类和命名只复用整理事件、本地整理历史、源目录内的 NFO 与文件名；不新增在线识别，不需要额外密钥。事件保存媒体信息及文件版本签名供补扫复用，签名变化后不会使用旧事件信息。NFO 最大 1 MiB，拒绝实体声明和符号链接，只读取同名 NFO、movie.nfo 和源目录边界内最多三级 tvshow.nfo；国家字段复用明确的两位代码，类型字段复用数字编号，无法从本地资料得到的条件视为缺失，不从名称猜测编号。

启用任务且 `transfer_enabled=true` 时，整理完成事件只接管其源目录内的本地文件，事件无需等待静置时间，其余过滤、目录隔离、分类、命名和任务指纹与扫描共用。没有匹配任务时不自动上传；源目录重叠的任务不能同时启用。事件先持久化 waiting 批次及媒体、策略快照，再提交后台；可以在普通扫描或上传期间接收新文件。事件与扫描的同一源版本及目标指纹由数据库唯一约束去重。事件批次重启后安全重放，现有文件任务不会重新创建；每 30 秒轮询可恢复未提交的 waiting 事件。活动或等待批次存在时不能修改、删除目录配置。

`strm_root` 指定本地媒体库根目录，必须与源目录和整个挂载目录隔离且路径不含符号链接；按本批次 archive_path 的最终相对结构生成 STRM，包括归档冲突后的版本文件名。输出根目录及归档根目录随文件任务持久化，生成、重启恢复、清理使用同一映射校验。留空兼容已有任务，在源文件旁生成 STRM；输出仍是文本文件。STRM 成功后才按任务策略处理源文件。

服务重启时会恢复 `pending`、`uploading`、`archiving` 和 `generating_strm` 状态的任务；`failed` 和 `cancelled` 状态必须由管理员显式重试，避免启动时重复触发可能已被人工确认的失败操作。执行期间如果 worker 丢失租约，会停止后续文件操作、保留未完成阶段，并在租约释放后自动重新领取；取消任务不会删除源文件。

通用 REST 响应包含 `success`、`message`、`message_i18n`、`data` 字段。为兼容 App 和第三方客户端，`message` 继续保留原中文或原始后端文本；新版前端可发送 `X-MoviePilot-Locale: zh-CN|zh-TW|en-US` 或 `Accept-Language`，并优先展示 `message_i18n`。未提供语言头或翻译缺失时，`message_i18n` 会回退为原文本。

`GET /api/v1/login/wallpaper` 及对应的 v2 路径会将壁纸 URL 放在 `data` 字段中。`POST /api/v1/user/avatar/{user_id}` 及对应的 v2 路径会以 `data.filename` 返回原始文件名。上述接口的 `message` 均不再承载业务数据。

FastAPI 的 HTTP 异常在 v1、v2 均统一使用 `message`，不再返回顶层 `detail` / `detail_i18n`。

交互式接口文档 `/docs` 默认读取 `/api/v2/openapi.json`，页面版本号直接使用 `version.py` 中的后端 `APP_VERSION`。旧地址 `/api/v1/openapi.json` 继续保留并返回同一份完整接口文档。

#### 媒体识别 / 整理

媒体识别、搜索和手动整理内置支持 `themoviedb`、`douban`、`bangumi`、`anilist` 四种数据源，也允许插件处理自定义来源。自动识别仍使用系统默认来源；手动操作可通过请求级 `source` 或 `media_source` + `media_id` 临时指定来源，不修改系统默认值。

涉及媒体身份的请求统一以 `media_source` + `media_id` 表示本次选定的主身份，同时保留 `tmdbid`、`doubanid`、`bangumiid`、`anilistid` 作为跨数据源映射和旧客户端兼容字段。两者并非两套独立数据流：显式通用主身份优先，专用 ID 用于补全映射和兼容回退。

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/media/search` | 按标题搜索媒体、合集或人物，参数：`title`、`type`、`page`、`count`，可选 `source`；`media` 支持 `themoviedb`、`douban`、`bangumi`、`anilist`，`collection` 支持 `themoviedb`，`person` 支持 `themoviedb`、`douban` |
| GET | `/api/v1/media/recognize` | 识别标题，参数：`title`、`subtitle`、`custom_words`，可选 `source`；当 `title` 为含目录的媒体文件路径时，会合并父目录中的名称、年份等信息 |
| GET | `/api/v1/media/recognize_file` | 识别文件路径，参数：`path`，可选 `source` |
| GET | `/api/v1/media/{mediaid}` | 查询媒体详情，`mediaid` 支持 `tmdb:`、`douban:`、`bangumi:`、`anilist:` 及插件自定义来源前缀 |
| POST | `/api/v1/media/scrape/{storage}` | 刮削媒体元数据；请求体为 `FileItem`，可选查询参数 `media_source`、`media_id`、`type_name`（电影/电视剧）可指定本次刮削媒体 |
| POST | `/api/v1/transfer/manual/target-path` | 匹配手动整理目标路径；请求体可用 `media_source` + `media_id` 指定数据源原生ID |
| POST | `/api/v1/transfer/manual/history` | 查询文件、批量文件或目录命中的成功整理历史摘要，用于进入手动整理界面时显示重新整理状态 |
| POST | `/api/v1/transfer/manual` | 手动整理；请求体可用 `media_source` + `media_id` 指定本次识别与刮削数据源，同时兼容 `tmdbid`、`doubanid`、`bangumiid`、`anilistid`；命中失败历史时自动清理旧目标和记录后重试，`reorganize=true` 时清理命中的成功历史和非移动模式旧目标后重新整理 |

#### 搜索 / 种子 / 字幕

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/search/media/{mediaid}` | 按媒体 ID 搜索站点种子资源，`mediaid` 支持 `tmdb:123`、`douban:123`、`bangumi:123`、`anilist:123` 及插件来源前缀，参数：`mtype`、`area`、`title`、`year`、`season`、`sites` |
| GET | `/api/v1/search/media/{mediaid}/stream` | 按媒体 ID 渐进式搜索站点种子资源，返回 SSE，参数同上 |
| GET | `/api/v1/search/title` | 按关键字模糊搜索站点种子资源，参数：`keyword`、`page`、`sites` |
| GET | `/api/v1/search/title/stream` | 按关键字渐进式搜索站点种子资源，返回 SSE，参数：`keyword`、`page`、`sites` |
| GET | `/api/v1/search/subtitle/title` | 按关键字搜索站点字幕资源，参数：`keyword`、`page`、`sites` |
| GET | `/api/v1/search/subtitle/title/stream` | 按关键字渐进式搜索站点字幕资源，返回 SSE，参数：`keyword`、`page`、`sites` |
| GET | `/api/v1/search/subtitle/media/{mediaid}` | 按媒体 ID 精确搜索站点字幕资源，`mediaid` 支持四种内置来源及插件来源前缀，参数：`mtype`、`title`、`year`、`season`、`episode`、`sites` |
| GET | `/api/v1/search/subtitle/media/{mediaid}/stream` | 按媒体 ID 渐进式精确搜索站点字幕资源，返回 SSE，参数同上 |
| GET | `/api/v1/search/last` | 获取上一次种子搜索结果 |
| GET | `/api/v1/search/last/context` | 获取上一次搜索结果及可复用搜索参数，`params.result_type` 为 `torrent` 或 `subtitle` |
| POST | `/api/v1/search/recommend` | 获取 AI 推荐资源，请求体：`filtered_indices`、`check_only`、`force` |

渐进式搜索在无业务事件时每 15 秒发送 `{"type":"heartbeat"}`，客户端应将其仅用于连接保活。超过 48 条的最终 `replace` 会分批发送：首批 `type=replace`，后续批次 `type=append`，所有批次均带 `replace_batch=true`、从 0 开始的 `batch_index`、`batch_count` 和最终 `total_items`；客户端必须按顺序收齐后再原子替换结果。最终 `done` 在已发送 `replace` 后不重复携带 `items`。

#### AniList 榜单 / 探索

AniList 榜单、探索、详情、人物和推荐接口优先通过 `anilist-chinese` 代理查询。代理不可用时自动回退 AniList 官方 GraphQL，并合并 `anilist-chinese` 每日数据集；媒体标题优先使用项目提供的中文标题，未提供中文标题时回退 AniList 原语言标题。

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/anilist/trending` | 查询 TRENDING NOW 榜单，参数：`page`、`count` |
| GET | `/api/v1/anilist/popular-this-season` | 查询 POPULAR THIS SEASON 榜单，参数：`page`、`count` |
| GET | `/api/v1/anilist/discover` | 组合探索动画，参数：`search`、`genre`、`format`、`season`、`season_year`、`status`、`country`、`sort`、`page`、`count` |
| GET | `/api/v1/anilist/{anilist_id}` | 查询动画详情 |
| GET | `/api/v1/anilist/credits/{anilist_id}` | 查询日语配音演员，参数：`page`、`count` |
| GET | `/api/v1/anilist/recommend/{anilist_id}` | 查询相关推荐，参数：`page`、`count` |
| GET | `/api/v1/anilist/person/{person_id}` | 查询人物详情 |
| GET | `/api/v1/anilist/person/credits/{person_id}` | 查询人物参与的动画作品，参数：`page`、`count` |

#### 下载

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/download/` | 查询正在下载的任务，参数：`name`；关联下载历史时返回媒体类型、来源站点 `site_name`，以及 `media.poster` 海报和 `media.backdrop` 背景图；兼容字段 `media.image` 与 `media.poster` 相同 |
| POST | `/api/v1/download/` | 添加含媒体信息的下载任务，请求体包含媒体信息和种子信息 |
| POST | `/api/v1/download/add` | 添加不含媒体信息的下载任务，请求体包含 `torrent_in`，可选 `media_source` + `media_id`；继续兼容四种专用 ID，并支持 `downloader`、`save_path` |
| POST | `/api/v1/download/subtitle` | 下载字幕到识别出的媒体下载目录，请求体包含 `subtitle_in`，可选 `media_source` + `media_id`；继续兼容四种专用 ID，并支持 `save_path` |
| GET | `/api/v1/download/start/{hashString}` | 恢复下载任务，参数：`name` |
| GET | `/api/v1/download/stop/{hashString}` | 暂停下载任务，参数：`name` |
| GET | `/api/v1/download/clients` | 查询可用下载器 |
| GET | `/api/v1/download/paths` | 查询可用于下载接口 `save_path` 参数的下载路径 |
| DELETE | `/api/v1/download/{hashString}` | 删除下载任务，参数：`name` |

#### 历史

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/history/download` | 按下载时间倒序查询下载历史，参数：`page`、`count`；`poster` 为海报，兼容字段 `image` 为背景图 |
| DELETE | `/api/v1/history/download` | 删除下载历史，请求体为下载历史记录 |

#### 系统

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/system/ping` | 登录用户服务存活检测，用于前端重启后轮询恢复状态 |
| GET | `/api/v1/dashboard/system` | 查询仪表板系统摘要，包括主机名称、操作系统、MoviePilot 运行时间和后端版本 |
| GET | `/api/v1/dashboard/schedule` | 查询所有后台定时服务，包含当前完成百分比、进度文本和执行状态 |
| GET | `/api/v1/dashboard/schedule/{job_id}/progress` | 查询指定后台定时服务的实时进度详情 |
| GET | `/api/v1/dashboard/schedule2/{job_id}/progress` | 使用 API_TOKEN 查询指定后台定时服务的实时进度详情 |
| GET | `/api/v1/system/setting/public/{key}` | 登录用户读取白名单内非敏感系统设置，仅支持目录、存储、站点范围、默认订阅规则、Follow 订阅者和插件市场地址等前端必需配置 |
| POST | `/api/v1/system/setting/PLUGIN_MARKET/sync-wiki` | 管理员从配置注入的插件市场 Wiki 文档同步公开插件仓库清单，和本地 `PLUGIN_MARKET` 合并去重后写入配置；未配置 Wiki 地址时跳过 |
| GET | `/api/v1/system/modulelist` | 查询已加载模块，保留 `name` 原始中文字段，并提供 `name_i18n` 和 `name_key` 给多语言前端展示 |
| GET | `/api/v1/system/moduletest/{moduleid}` | 测试指定模块可用性，保留原 `message`，并在标准响应顶层返回 `message_i18n` |
| GET | `/api/v1/message/agent/mcp/servers` | 管理员查询 Agent 外部 MCP 服务器配置 |
| POST | `/api/v1/message/agent/mcp/servers` | 管理员保存 Agent 外部 MCP 服务器配置 |
| POST | `/api/v1/message/agent/mcp/servers/test` | 管理员测试单个 Agent 外部 MCP 服务器并读取工具列表 |

#### 缓存管理

以下接口使用登录态鉴权，并要求当前用户为超级管理员。

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/v1/tmdb/cache` | 查询 TheMovieDb 识别缓存统计、共享识别累计成功命中次数及开关状态 |
| DELETE | `/api/v1/tmdb/cache/{cache_key}` | 按缓存键删除单条 TheMovieDb 识别缓存，缓存键需要进行 URL 编码 |
| DELETE | `/api/v1/tmdb/cache` | 清空全部 TheMovieDb 识别缓存 |

TMDB 缓存查询响应的 `data` 包含 `count`、`recognized`、`unrecognized`、`data`，以及共享识别统计字段
`shared_recognized` 和开关字段 `shared_recognize_enabled`。共享命中次数仅在共享结果驱动的二次媒体识别成功后累计。

### 插件补充接口

**GET** `/api/v1/plugin/history/{plugin_id}`

按需读取指定已安装插件的最新远端更新说明。该接口用于前端在用户点击“查看更新说明”时再实时访问插件仓库，避免加载已安装插件列表时批量请求网络。

**GET** `/api/v1/plugin/rating?plugin_ids={plugin_id,...}`

批量查询插件平均分、评分人数和当前安装实例评分。`plugin_ids` 省略时查询中心端已有的全部插件评分。

**GET** `/api/v1/plugin/rating/{plugin_id}`

查询单个插件平均分、评分人数和当前安装实例评分。中心端暂不可用时返回该插件的零评分结果。

**POST** `/api/v1/plugin/rating/{plugin_id}`

为已安装插件提交当前安装实例评分，请求体为 `{"rating": 4.5}`。评分范围为 `0.1` 至 `5.0`，精确到 `0.1`；同一安装实例再次提交会更新原评分。

### 1. 列出所有工具

**GET** `/api/v1/mcp/tools`

获取所有可用的MCP工具列表。

内置工具的 `inputSchema` 只包含实际执行业务所需的参数，不包含用于解释调用原因的通用 `explanation` 参数，以减少 Agent 上下文消耗。插件工具的参数结构由插件自身声明。

内置 Agent 的本地文件与命令工具 `read_file`、`write_file`、`edit_file`、
`execute_command` 不通过 MCP 暴露。这些工具在 Agent 运行时执行独立的
用户权限与路径边界检查；MCP 隐藏列表只负责收敛接口暴露面，不替代权限控制。
其中 `read_file` 单次最多返回 50KB 文件内容；超出时会截断并提示 Agent 使用
`start_line`、`end_line` 指定更小的行号范围继续读取。

媒体相关 MCP 工具（如 `query_media_detail`、`search_torrents`、`query_library_exists`、`add_subscribe`、`transfer_file`）接受 `tmdb_id`/`tmdbid`、`douban_id`/`doubanid`、`bangumi_id`/`bangumiid`、`anilist_id`/`anilistid`，也接受 `media_source` + `media_id`。工具返回的媒体、订阅、下载和整理记录会同步带回可用的四种专用 ID 及通用主身份。

`get_search_results` 可使用 `title_pattern` 对种子标题执行正则筛选，也可使用 `content_pattern` 联合匹配种子标题、简介和标签。`title_pattern` 保持仅匹配标题的兼容语义；需要在结果中查看种子简介时，传入 `include_description=true`。两种正则参数与站点、分辨率等结构化筛选条件同时传入时按 AND 关系组合。

#### Agent 自主定时任务工具

以下工具用于管理会在指定时间重新唤醒 Agent 的持久化任务，均为管理员级工具：

| 工具 | 说明 |
| :--- | :--- |
| `create_agent_task` | 创建单次或周期任务，并保存任务内容及当前用户、会话上下文 |
| `query_agent_tasks` | 查询任务配置、启用状态、下次执行时间及最近执行结果 |
| `update_agent_task` | 修改任务内容或触发器，也可通过 `enabled` 暂停、恢复任务 |
| `run_agent_task` | 使用整数 `task_id` 将当前用户已启用的任务提交为立即执行 |
| `delete_agent_task` | 永久删除任务并立即移除运行时调度 |

`trigger_type=date` 表示单次执行：“30 分钟后检查”这类相对时间传 `delay_minutes=30`，由后端计算精确时间；固定时间则传 ISO 8601 `trigger`，支持精确到秒。`trigger_type=cron` 使用标准五段 cron（分、时、日、月、周），适合周期检查。未显式携带时区的时间按 MoviePilot 的 `TZ` 配置解释。任务由内存调度器精确触发，配置持久化到数据库，服务重启后会自动恢复；触发后 Agent 在原会话中执行 `content`，执行过程及最终结果均不绑定创建任务时的消息渠道，而是通过 MoviePilot 已配置的通知渠道广播。如果 Agent 在执行过程中已通过消息工具发送完整结果，任务结束时不会再次发送相同的最终回复。

Agent 自主任务工具使用数据库中的整数 `task_id`。`query_schedulers` 与 `run_scheduler` 仅面向系统、插件和工作流注册的运行时定时服务，使用字符串 `job_id`，不会返回或执行 `agent-task-*`。两类 ID 不可混用；需要立即执行自主任务时，应先通过 `query_agent_tasks` 确认归属和状态，再调用 `run_agent_task`。立即执行只提交任务，不在当前工具调用内等待结果，从而避免同一 Agent 会话互相等待；执行结果仍按上述通知规则广播。

上述过滤只约束 Agent 工具，避免模型混用两类任务。前端系统设置和仪表盘使用的 `/api/v1/dashboard/schedule` 仍返回完整运行时列表，其中包含 `provider=[Agent]` 的自主任务；前端通过 `/api/v1/system/runscheduler` 立即执行这类列表项的行为也保持不变。

创建单次任务的参数示例：

```json
{
  "tool_name": "create_agent_task",
  "arguments": {
    "name": "检查电影资源",
    "content": "搜索电影《示例电影》是否已有可下载资源，并报告站点、版本和大小；不要自动下载。",
    "trigger_type": "date",
    "delay_minutes": 30
  }
}
```

创建每天 20:30 执行的周期任务时，使用 `trigger_type=cron` 和 `trigger="30 20 * * *"`。

**认证**: 需要API KEY，在请求头中添加 `X-API-KEY: <api_key>` 或在查询参数中添加 `apikey=<api_key>`

**响应示例**:
```json
[
  {
    "name": "add_subscribe",
    "description": "Add media subscription to create automated download rules...",
    "inputSchema": {
      "type": "object",
      "properties": {
        "title": {
          "type": "string",
          "description": "The title of the media to subscribe to"
        },
        "year": {
          "type": "string",
          "description": "Release year of the media"
        },
        ...
      },
      "required": ["title", "year", "media_type"]
    }
  },
  ...
]
```

#### 系统诊断工具

`query_doctor_report` 以只读方式返回 MoviePilot Doctor 诊断报告，可通过 `deep` 启用深度检查，并通过 `include_details` 控制是否返回完整详情。每条诊断项的 `affects_report_status` 表示其是否参与整体状态聚合；插件日志异常会保留为 `warn/degraded` 线索，但该字段为 `false`，不会单独把系统整体状态降为 `degraded`。

### 2. 调用工具

**POST** `/api/v1/mcp/tools/call`

调用指定的MCP工具。

**认证**: 需要API KEY，在请求头中添加 `X-API-KEY: <api_key>` 或在查询参数中添加 `apikey=<api_key>`

**请求体**:
```json
{
  "tool_name": "add_subscribe",
  "arguments": {
    "title": "流浪地球",
    "year": "2019",
    "media_type": "movie"
  }
}
```

**响应示例**:
```json
{
  "success": true,
  "result": "成功添加订阅：流浪地球 (2019)",
  "error": null
}
```

**错误响应示例**:
```json
{
  "success": false,
  "result": null,
  "error": "调用工具失败: 参数验证失败"
}
```

### 3. 获取工具详情

**GET** `/api/v1/mcp/tools/{tool_name}`

获取指定工具的详细信息。

**认证**: 需要API KEY，在请求头中添加 `X-API-KEY: <api_key>` 或在查询参数中添加 `apikey=<api_key>`

**路径参数**:
- `tool_name`: 工具名称

**响应示例**:
```json
{
  "name": "add_subscribe",
  "description": "Add media subscription to create automated download rules...",
  "inputSchema": {
    "type": "object",
    "properties": {
      "title": {
        "type": "string",
        "description": "The title of the media to subscribe to"
      },
      ...
    },
    "required": ["title", "year", "media_type"]
  }
}
```

### 4. 获取工具参数Schema

**GET** `/api/v1/mcp/tools/{tool_name}/schema`

获取指定工具的参数Schema（JSON Schema格式）。

**认证**: 需要API KEY，在请求头中添加 `X-API-KEY: <api_key>` 或在查询参数中添加 `apikey=<api_key>`

**路径参数**:
- `tool_name`: 工具名称

**响应示例**:
```json
{
  "type": "object",
  "properties": {
    "title": {
      "type": "string",
      "description": "The title of the media to subscribe to"
    },
    "year": {
      "type": "string",
      "description": "Release year of the media"
    },
    ...
  },
  "required": ["title", "year", "media_type"]
}
```

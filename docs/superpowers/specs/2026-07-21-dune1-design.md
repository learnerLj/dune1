# dune1 最小 DuneSQL 客户端设计

## 1. 背景与目标

`dune1` 是一个项目本地、无遥测的 Python 命令行工具，仅用于通过 Dune REST API 执行原始 DuneSQL。它保留官方 Dune CLI `query run-sql` 的主要交互方式，但不复用官方 CLI 或第三方 Python SDK，确保每一次网络请求和本地写入都可以直接审计。

项目位置、Python 包名和命令名统一为 `dune1`：

```text
dune1
```

V1 的成功标准：

- 通过项目本地配置读取一组 API key 和 HTTP proxy，用户无需每次输入。
- 缺少代理、代理配置无效或代理连接失败时，必须在发送 Dune 请求前或失败处停止，绝不回退直连。
- 支持内联 SQL 和 SQL 文件，默认等待执行完成，也支持只提交不等待。
- 支持参数、性能等级、超时、结果条数限制以及 text/json 输出。
- 不发送遥测，不查询账户身份，不保存查询历史、结果、日志或身份缓存。
- 明确区分疑似账户暂停、普通认证失败、额度不足、查询失败和网络故障。

## 2. 范围

### 2.1 V1 包含

唯一业务命令：

```bash
uv run dune1 query --sql "SELECT 1"
uv run dune1 query --file query.sql
```

支持的选项：

| 选项 | 约束与行为 |
| --- | --- |
| `--sql TEXT` | 内联 DuneSQL；与 `--file` 二选一 |
| `--file PATH` | 读取 UTF-8 SQL 文件；与 `--sql` 二选一 |
| `--param KEY=VALUE` | 可重复；值按字符串发送；空 key、缺少 `=` 或重复 key 均拒绝 |
| `--performance TIER` | 只接受 `small`、`medium`、`large`；默认不传，由 Dune 自动选择 |
| `--limit INTEGER` | 非负整数；`0` 表示获取所有可用结果；正数限制最终请求和输出的行数 |
| `--no-wait` | 提交后立即输出 `execution_id` 和初始状态 |
| `--timeout SECONDS` | 等待完成的总时限，默认 300 秒，必须大于 0 |
| `--output [text\|json]` / `-o` | 默认 `text`；JSON 输出保留 Dune 响应结构 |

默认轮询间隔固定为 2 秒，不增加用户可配置项。

### 2.2 V1 不包含

- 保存、读取、更新或归档 Dune Query。
- 运行已有 `query_id`。
- `whoami`、usage、dataset search、upload、dashboard、visualization 或 Sim API。
- 多账户、自动轮换、额度耗尽后 fallback 或封号后的换号重试。
- 取消 execution、恢复历史 execution 或单独查询 execution 结果的命令。
- CSV 输出、并发查询、异步事件循环、数据库、缓存和自动结果文件。
- 代理池、系统代理发现、代理健康检查或无代理降级。
- 规避 Dune 的账户限制、风控或服务条款执行。

## 3. 技术选型

- Python 3.12。
- `uv` 管理虚拟环境、依赖、锁文件和命令执行。
- `Click 8.x` 提供命令行解析、互斥参数校验和稳定退出行为。
- `httpx 0.28.x` 提供同步 HTTP 客户端、显式代理和连接复用。
- 标准库 `tomllib` 读取配置，避免额外配置依赖。
- `pytest`、Ruff 和 mypy 用于开发期验证。

V1 使用同步 `httpx.Client`。一次命令只处理一个串行 execution，异步不会缩短 Dune 服务端执行时间，也不会带来可见吞吐收益。

## 4. 项目结构

```text
dune1/
├── config.local.toml          # 本地密钥与代理，不进入 Git
├── config.example.toml        # 仅包含字段示例，不含真实凭据
├── pyproject.toml
├── uv.lock
├── src/dune1/
│   ├── __init__.py
│   ├── cli.py                 # Click 命令与输入校验
│   ├── config.py              # 项目本地配置读取与权限校验
│   ├── api.py                 # 唯一 HTTP 出口与响应解析
│   ├── query.py               # 提交、轮询和获取结果
│   ├── output.py              # text/json 输出
│   └── errors.py              # 领域错误和退出码
└── tests/
    ├── test_cli.py
    ├── test_config.py
    ├── test_api.py
    ├── test_query.py
    └── test_output.py
```

模块边界：

- `cli.py` 不构造 HTTP 请求，只把已校验输入交给查询服务。
- `config.py` 不读取环境变量、Keychain 或用户主目录配置。
- `api.py` 不轮询、不休眠、不格式化终端输出。
- `query.py` 只编排状态机，时间和 sleep 可注入以便测试。
- `output.py` 不读取配置，不访问网络。

## 5. 本地配置与凭据边界

配置固定放在项目根目录：

```toml
[dune]
api_key = "replace-me"
proxy_url = "http://username:password@host:port"
```

加载规则：

1. 从 `dune1` 源码所在项目根目录定位 `config.local.toml`，不受当前 shell 工作目录影响。
2. 文件不存在、无法读取、TOML 无效、字段为空时，在建立 HTTP client 前失败。
3. 在 POSIX 系统上，配置文件存在 group/other 权限时拒绝读取，并提示执行 `chmod 600 config.local.toml`。
4. `proxy_url` 必须使用 `http://`，并包含 hostname、port、username 和 password。
5. API key 与代理从同一个 `[dune]` 配置块原子加载；CLI 不提供覆盖其中任意一项的 flag 或环境变量。
6. `config.local.toml` 必须进入 `.gitignore`；`config.example.toml` 不得出现真实 key、代理地址或凭据哈希。

V1 只有一组配置，不实现 profile。项目无法证明某个外部 key 在 Dune 侧归属于某个代理，因此它通过“单一不可拆分配置块 + 无单项覆盖入口”防止程序内串用，但不声称能够阻止用户手工修改文件。

## 6. 网络与隐私边界

API base URL 固定为：

```text
https://api.dune.com
```

不能通过配置或命令行改写 base URL，避免 API key 被发送到其他主机。`api.py` 创建客户端时必须满足：

- 显式传入 `proxy=proxy_url`。
- 设置 `trust_env=False`，忽略 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`。
- 保持 TLS 证书校验开启。
- 不允许自动跟随跨主机重定向。
- 所有 Dune 请求只向固定 base URL 发送 `X-DUNE-API-KEY`。
- 不配置直连 transport，也不在代理异常后重建无代理 client。
- 不记录请求头、API key、完整代理 URL、SQL 或响应正文。

HTTP transport 错误、代理认证错误、TLS 错误和连接超时立即终止本次命令。POST 提交不自动重试，避免不确定是否已经创建 execution。轮询只针对成功返回的 pending/executing 状态；轮询请求自身失败也停止，不做直连或账户切换。

## 7. API 与数据流

V1 仅调用以下官方端点：

| 阶段 | 方法与端点 | 用途 |
| --- | --- | --- |
| 提交 | `POST /api/v1/sql/execute` | 提交原始 SQL、参数和可选 performance |
| 轮询 | `GET /api/v1/execution/{execution_id}/status` | 获取轻量 execution 状态 |
| 结果 | `GET /api/v1/execution/{execution_id}/results` | execution 完成后获取结果，可带 limit/offset |

请求流程：

1. Click 校验参数；读取内联 SQL 或 UTF-8 文件，拒绝空 SQL。
2. 加载并校验项目本地配置。
3. 建立唯一的、显式代理绑定的 `httpx.Client`。
4. 提交 `POST /api/v1/sql/execute`，验证响应包含合法的 `execution_id` 和 `QUERY_STATE_*`。
5. 若指定 `--no-wait`，立即按选定格式输出提交响应并退出。
6. 否则每 2 秒调用 status 端点，直到 completed、failed、cancelled 或达到总超时。
7. completed 后只调用一次 results 端点；`--limit > 0` 时传给服务端，`0` 时按 Dune 的 `next_offset` 分页获取全部结果。
8. text 模式按 Dune 返回的 `column_names` 顺序输出表格和行数；json 模式输出完整、可机器读取的结果对象。

与官方 CLI 的外部行为保持一致，但不复制其完成后可能重复获取结果的内部流程。轮询 status、完成后获取一次结果可以减少不必要的数据传输。

## 8. 状态、错误与退出码

已知 execution 状态：

- `QUERY_STATE_PENDING`、`QUERY_STATE_EXECUTING`：继续轮询。
- `QUERY_STATE_COMPLETED`：获取并输出结果。
- `QUERY_STATE_FAILED`：输出 Dune 返回的查询错误，禁止再次提交。
- `QUERY_STATE_CANCELLED`：报告已取消。
- 任何未知状态：作为 API 协议错误停止。

账户暂停只在 Dune 响应正文明确、大小写不敏感地包含 `account has been suspended` 或 `violating our Terms of Service` 等确定性表述时识别。提示用户停止调用并联系 Dune 支持。普通 401/403 只报告认证或权限失败，不推断封号。

错误正文优先解析 JSON 的 `error` 或 `message` 字段，并限制读取和展示长度。所有输出都要清除 API key 和代理凭据；异常对象不得包含完整代理 URL。

稳定退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | Click 参数或本地配置错误 |
| `3` | 代理、DNS、连接、TLS 或 transport 错误 |
| `4` | 认证、权限或明确的账户暂停 |
| `5` | 额度不足或 rate limit；不自动 fallback |
| `6` | 查询失败或 execution 取消 |
| `7` | 等待 execution 超时 |
| `8` | Dune API 响应结构或未知状态错误 |

## 9. 输出契约

默认 text 输出面向人类：

```text
column_a  column_b
-------   --------
value_a   value_b

1 rows
```

`--no-wait` 的 text 输出：

```text
Execution ID: 01...
State:        QUERY_STATE_PENDING
```

JSON 始终只写到 stdout，诊断信息只写到 stderr，保证以下命令生成合法 JSON：

```bash
uv run dune1 query --file query.sql --output json > result.json
```

工具本身不创建结果文件、日志文件或 execution 历史。

## 10. 测试设计

默认测试全部离线，不接触真实 Dune 账户或代理：

- Click 测试：SQL/file 互斥、缺失输入、参数解析、重复 key、performance、limit、timeout 和输出模式。
- 配置测试：缺失文件、无效 TOML、字段缺失、权限过宽、代理 scheme/认证/端口校验，以及从非项目工作目录调用。
- 网络工厂测试：断言显式 proxy、`trust_env=False`、TLS 校验开启、固定 base URL 和禁止重定向。
- API 测试：使用 `httpx.MockTransport` 覆盖三个端点、请求头、请求体、分页、非 JSON 响应和畸形响应。
- 状态机测试：pending 到 completed、failed、cancelled、未知状态和可控时钟下的总超时。
- 安全回归测试：配置或代理无效时零 HTTP 请求；代理异常后不会创建第二个无代理 client；错误输出不含 key 和代理密码。
- 错误分类测试：明确 suspension 文案、普通 401/403、402/额度耗尽、429、查询语法错误和 transport 错误。
- 输出测试：text 列顺序、空结果、JSON 可解析、stdout/stderr 分离和 `--limit` 一致性。

不把真实 API 调用纳入默认测试。只有在用户提供一个未暂停且获授权的账户并明确要求 live test 时，才单独执行最小 `SELECT 1` 验证；live test 仍必须通过配置中的代理。

完成实现前必须通过：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

## 11. 验收边界

实现可以被判定为完成，必须同时满足：

1. 所有离线测试、Ruff 和 mypy 通过。
2. 搜索代码和依赖后不存在 Amplitude、Sentry、OpenTelemetry 或其他遥测客户端。
3. 除用户主动创建的 `config.local.toml` 外，执行命令不会写入项目目录、用户主目录或系统凭据存储。
4. 没有代理配置时，在任何 Dune 网络请求前失败。
5. 代理连接失败时，测试能够证明没有直连重试。
6. suspension、普通认证失败、额度不足和查询失败会得到不同且准确的错误提示。
7. README 明确说明 Dune 服务端仍能看到 API key、代理出口 IP、SQL、参数、调用频率和时间；本工具只能移除官方 CLI 额外的遥测与本地身份缓存。


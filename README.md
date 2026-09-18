# dune1

`dune1` 直接或通过项目配置的 HTTP proxy 执行原始 DuneSQL。它不包含官方 CLI 的遥测、`whoami`、身份缓存和其他 Dune 产品功能。

## 1. 安装

项目固定使用 Python 3.12 和 `uv`。以 editable tool 安装后，源码修改立即反映到 `dune1` 命令：

```bash
uv tool install --editable .
```

`pyproject.toml` 的依赖或命令入口发生变化时，运行 `uv tool install --editable . --force` 更新工具环境。

## 2. 配置

复制示例文件，只配置一次：

```bash
cp config.example.toml config.local.toml
chmod 600 config.local.toml
```

配置多个命名账户，并用 `active` 指定当前唯一使用的账户：

```toml
[dune]
active = "main"

[dune.accounts.main]
api_key = "replace-with-an-active-authorized-key"

[dune.accounts.backup]
api_key = "replace-with-another-active-authorized-key"
proxy_url = "http://host:port"
```

每个账户独立保存自己的 key 和可选 proxy。省略 `proxy_url` 时该账户直连；无认证代理使用 `http://host:port`；认证代理使用 `http://username:password@host:port`。

切换账户只编辑 `[dune]` 下的 `active`。active 必须匹配一个账户名。认证失败、账户暂停、额度不足、网络错误或查询失败时，程序立即停止，不会尝试其他账户。

`config.local.toml` 以明文保存凭据，已被 Git 忽略。程序要求该文件权限为 `0600`。已经暂停的账户不得写入配置或继续调用。

程序不读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`、Keychain 或用户主目录配置。只有 `config.local.toml` 明确包含 `proxy_url` 时才使用代理。

## 3. 查询

执行内联 SQL：

```bash
dune1 query --sql "SELECT 1"
```

执行 UTF-8 SQL 文件：

```bash
dune1 query --file query.sql
```

传入参数和性能等级：

```bash
dune1 query \
  --file query.sql \
  --param days=30 \
  --param wallet=0x0000000000000000000000000000000000000000 \
  --performance small
```

输出 JSON 并由 shell 保存：

```bash
dune1 query --file query.sql --output json > result.json
```

只提交 execution，不等待结果：

```bash
dune1 query --sql "SELECT 1" --no-wait
```

完整选项：

```bash
dune1 query --help
```

默认每 2 秒查询一次状态，总等待时间为 300 秒。`--limit 0` 获取所有可用结果；正数限制服务端返回和最终输出的行数。

## 4. 错误边界

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 命令参数或本地配置错误 |
| `3` | 代理、DNS、连接、TLS 或 transport 错误 |
| `4` | 认证、权限或明确的账户暂停 |
| `5` | 额度不足或 rate limit |
| `6` | 查询失败或 execution 取消 |
| `7` | 等待 execution 超时 |
| `8` | Dune API 响应结构或未知状态错误 |

只有 Dune 响应明确包含账户暂停或违反服务条款的文案时，程序才提示账户可能已暂停。普通 `401` 或 `403` 只报告认证或权限失败。所有错误都会停止当前调用，不自动重试提交、不切换账户。

## 5. 隐私边界

`dune1` 只调用以下三个端点：

```text
POST /api/v1/sql/execute
GET  /api/v1/execution/{execution_id}/status
GET  /api/v1/execution/{execution_id}/results
```

程序不会发送额外遥测，也不会保存 SQL、execution ID、结果、日志或账户身份。Dune 服务端仍然必然看到 API key、代理出口 IP、SQL、参数、请求时间和调用频率；自建客户端无法隐藏服务端处理请求所必需的数据。

## 6. 开发验证

默认测试不联网，不读取真实配置，也不访问 Dune：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

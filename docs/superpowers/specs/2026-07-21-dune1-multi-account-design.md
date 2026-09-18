# dune1 多账户配置设计

## 1. 目标

`config.local.toml` 可以保存多个命名账户，但每次查询只使用唯一的 active 账户。账户选择发生在 `dune1` 配置层；CLI 和 skill 继续直接调用 `dune1 query ...`，不接收账户参数，也不自动切换账户。

## 2. 配置结构

```toml
[dune]
active = "main"

[dune.accounts.main]
api_key = "replace-with-an-active-authorized-key"

[dune.accounts.backup]
api_key = "replace-with-another-active-authorized-key"
proxy_url = "http://host:port"
```

规则：

- `[dune]` 只允许 `active` 和 `accounts`。
- `active` 必须是非空字符串，并精确匹配 `[dune.accounts]` 下的一个账户名。
- `accounts` 至少包含一个账户；账户名不得为空。
- 每个账户只允许 `api_key` 和可选的 `proxy_url`。
- `api_key` 必须是非空字符串。
- 省略 `proxy_url` 时直连；配置时继续沿用现有 HTTP proxy 校验。
- key 与 proxy 位于同一账户块中，不存在跨账户组合逻辑。
- 当前单账户格式不保留兼容层；本地配置一次性迁移到新结构。

## 3. 选择与执行

`load_config()` 解析完整文档并只返回 active 账户对应的 `DuneConfig(api_key, proxy_url)`。API client、查询状态机、CLI 参数和输出格式均不感知其他账户。

一次命令的流程：

1. 读取并校验完整配置。
2. 按 `active` 精确选择一个账户。
3. 使用该账户的 key 和可选 proxy 创建唯一的 `httpx.Client`。
4. 提交、轮询和获取结果全程使用该客户端。

认证失败、账户暂停、额度不足、rate limit、网络错误、代理错误、TLS 错误、查询失败或超时都会立即结束当前命令。程序不得读取或尝试其他账户。

切换账户只通过人工编辑 `config.local.toml` 的 `active` 字段完成。V1 不新增 `--account`、`account use`、顺序轮换、随机选择或 fallback。

## 4. 模块边界

- `config.py`：验证多账户文档并解析 active 账户。
- `api.py`：继续只接收一个已经解析好的 `DuneConfig`。
- `cli.py`：保持现有命令契约，不增加账户相关选项。
- `dune-cli/SKILL.md`：保持纯调用职责，不读取、修改或解释账户配置。

## 5. 错误处理

以下情况在任何网络请求前返回配置错误，退出码为 `2`：

- 缺少 `active` 或 `accounts`。
- `active` 不是字符串、为空或不存在。
- `accounts` 为空或不是 TOML table。
- 账户不是 TOML table。
- 账户缺少有效 `api_key`。
- 出现未知字段。
- proxy 格式无效。

错误信息可以包含无敏感性的账户名，但不得包含 API key、proxy URL 或代理凭据。

## 6. 测试与迁移

离线测试覆盖：

- 多账户配置只返回 active 账户。
- active 账户可直连或使用自己的 proxy。
- 修改 active 后选择结果随之变化。
- active 不存在、账户为空、未知字段和无效账户结构全部拒绝。
- 旧单账户结构明确拒绝，防止静默解释错误。
- 失败账户不会触发其他账户读取或网络请求。

迁移当前本地配置时，将现有 key 放入 `[dune.accounts.main]`，并设置 `active = "main"`。`config.local.toml` 继续保持 `0600` 且不进入 Git。

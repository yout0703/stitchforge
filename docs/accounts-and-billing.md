# 账号、次数与自助网站

2026-09-12：产品从单人本地工作台扩展为账号制网站。当前已实现邮箱／密码注册登录、账号内容隔离、成功生成扣次数和移动端查看。微信支付、支付宝与 Stripe 的适配器已实现；尚无商户配置与真实联调，默认显示“在线支付尚未开放”。

## 使用流程

1. 注册或登录。新账号默认 0 次，不会自动获得旧本地图纸。
2. 免费导入全针 OXS / XSD，或使用已有次数导入 JPG／PNG。
3. 图片生成成功并保存版本后扣 1 次；解析失败、余额不足、版本冲突都不扣次数。免费编辑、查看和重新打开已有图纸。
4. 在图纸库打开作品，默认进入查看模式：点选针格查线色、同色高亮、网格与符号显示、拖动平移、双指缩放。手机下方保留可滚动色表。
5. “次数 / 账号”显示余额、最近 50 条充值订单和次数记录，可退出登录。

浏览器按用户 ID 保存“上次打开的图纸”，不在 localStorage 保存登录凭证。响应丢失时保留可能已生成的图纸，用户可以从图纸库找回；只有服务端明确拒绝的图片请求才清理新建空图纸。

## 运行与配置

继续使用 `uv run stitchforge-api` 和 `pnpm --dir apps/web dev`。首次启动自动增加账号、会话、素材授权、订单及账本表，保留原项目与版本。

`stitchforge-api` 启动时读取当前目录的 `.env`，已有进程环境变量优先；从项目根目录运行即可。管理员 CLI 仍从进程环境读取配置。DeepSeek 配置见[AI 图片分析](ai-analysis.md)。

| 配置 | 用途 |
|---|---|
| `STITCHFORGE_DATA_DIR` | 数据目录，默认 `.stitchforge-data` |
| `STITCHFORGE_PUBLIC_ORIGIN` | 正式站点完整来源，如 `https://stitch.example.com`。必须 HTTPS；启用 Secure Cookie，并仅允许这个 Origin 的写请求 |
| `STITCHFORGE_CREDIT_PACKS` | 服务端套餐 JSON，默认 `[]`。元素字段为 `id`、`credits`、`amount_minor`、`currency`，金额使用最小货币单位；支持的两位小数币种见支付专项说明 |
| `STITCHFORGE_CODEX_ENABLED` | `0` 禁用外部 Codex 图片分析。公开服务默认禁用；本地未配置时保留原有行为，可显式配置 `1` 启用 |

正式部署需要在同一个 HTTPS 来源下提供前端构建目录 `apps/web/dist` 和 `/api` 反向代理。API 继续监听回环地址；静态服务器只提供前端构建目录，不提供数据目录。反向代理需限制上传大小并设置适合图片计算时长的超时。

## 图纸下载

打开图纸后点击「下载图纸」。下载免费，不扣生成次数，图片生成与 OXS / XSD 导入的图纸均支持。

- **完整 PNG**：整张图纸的彩色针格、与网页一致的字母／数字符号、每 10 格加粗的网格与坐标刻度；下方附符号、品牌色号、股数和针数对照表。空格留白。导出不受网页缩放、选区、符号开关或同色高亮影响。
- **OXS**：保留全针位置、材料色号、显示色、股数和绣布 CT。沿用 `oxs-v1-zero-based` 格式；不保留历史、区域锁定、完整来源信息或顶层针方向，其他软件可能重分配显示符号。
- **XSD**：Pattern Maker v4 二进制全针图纸。保留全针位置、材料线色、股数和 CT；不保留历史、区域锁定、来源、边距和针方向。文字长度及外部软件验证边界见 [XSD 格式说明](xsd-format.md)。

存在未保存修改时先提交新版本，保存失败则停止下载并显示错误，重试不会退回旧版本。文件准备完成后自动触发下载，同时保留可点击的保存链接；手机可使用保存到文件或 PNG 预览功能。

接口为 `GET /api/v1/projects/{project_id}/revisions/{revision_id}/download/{format}`，`format` 支持 `oxs`、`xsd`、`png`。要求有效登录、项目归属及版本归属；回收站项目需先恢复。响应使用附件文件名和 `Cache-Control: no-store`，临时文件响应前清理，不写入公开素材目录。

PNG 每格 12–28 像素，随网格大小调整；支持当前上限 600×600 格，单图最多 6400 万像素。每个 API 进程同时只允许一个导出任务；繁忙时提示稍后重试。每账号每 15 分钟最多 60 次下载请求。

中文标题和色号需要服务器有中文字库。自动查找 Linux 的 Noto Sans CJK、文泉驿正黑及 macOS 的 Arial Unicode；可通过 `STITCHFORGE_CHART_FONT` 指定支持中文的 `.ttf` / `.ttc` 文件。没有这些字体时英文图纸可使用内置字体，含中文的图纸会明确报错，不会生成乱码文件。Linux 正式部署应安装 Noto Sans CJK 并配置字体路径，OXS 下载不受字体影响。

验证：`uv run pytest tests/test_downloads.py` 覆盖 PNG 针格／符号、长色号换行、最大网格、OXS 往返、下载权限及失败恢复；`uv run --with playwright python tools/check_web_accounts.py` 使用临时 API 和前端构建验证生成后下载、移动端链接以及未保存修改／保存失败重试。

## 旧图纸迁移

旧数据仍在 `local` 工作区，不由首个注册用户自动认领。先注册目标账号，再由拥有服务器权限的操作人执行：

```bash
uv run stitchforge-admin migrate-local --email owner@example.com
```

需要自定义目录时，把全局参数放在子命令前：

```bash
uv run stitchforge-admin --data-dir /path/to/data migrate-local --email owner@example.com
```

此命令在一个事务内迁移旧工作区的全部项目（含回收站）、任务及版本引用的素材授权。保留项目 ID 和版本内容；再次执行迁移数为 0。迁移前应备份整个数据目录。没有执行过针对现有用户数据的自动迁移。

## 充值接入边界

当前不提供跳过平台验证的支付成功按钮，也没有面向用户的“加次数”接口。套餐价格没有产品默认值，需要经营者配置。仅配置套餐不会启用线上支付。

已有能力：

- 服务端按套餐确定价格与次数，拒绝客户端自报金额或次数。
- `POST /api/v1/billing/orders` 使用 `pack_id` 和 `request_key` 创建待付款订单；同一账号重试相同请求不重复建单。
- `AccountStore.settle_order` 检查订单、实付金额、币种和唯一交易号，在同一事务内更新订单、余额与账本；并发或重复通知不会重复入账。
- 图片生成的版本、素材授权、余额扣减和消耗账本在同一事务提交。并发请求最多消耗现有余额；同一基础版本重试会返回版本冲突，不再次收费。

微信支付、支付宝和 Stripe 适配器、前端支付入口、签名回调及主动查单已实现；真实收款需等待商户开通、配置与联调。各渠道的配置、手机支持范围和接口说明见[多渠道支付说明](payment-providers.md)。未配置的渠道不会出现在可用支付方式列表中。退款／撤销、拒付和后台定期对账仍待实现。

在商户接入前，如有经过核实的线下付款，可由服务器操作人按已配置套餐建单并核账：

```bash
uv run stitchforge-admin create-order --email owner@example.com \
  --pack-id YOUR_CONFIGURED_PACK --request-key UNIQUE_REQUEST_KEY
uv run stitchforge-admin settle-order --order-id ORDER_ID \
  --payment-reference VERIFIED_TRANSACTION_ID --amount-minor VERIFIED_AMOUNT_IN_CENTS --currency CNY
```

这里的参数是占位符，需要替换为实际配置和已核实的交易信息。本工具不会发起真实付款，也不会自行确认收款。

## 账号安全与恢复

密码使用带随机盐的 scrypt 保存；会话为 256 位随机 token，数据库仅存 token 哈希，30 天后过期；退出登录撤销当前会话。Cookie 使用 HttpOnly、SameSite=Strict，正式来源配置后还使用 Secure。

写请求必须携带 `X-StitchForge-Request: 1`，且浏览器 Origin 要符合配置；不启用跨来源 CORS；三个指定的支付通知路径使用渠道签名验证，豁免浏览器会话／CSRF 检查。API 和素材响应使用 `Cache-Control: no-store`，防止共享浏览器在换账号后继续命中私有素材缓存。图纸、缩略图、版本、任务、命令与素材都在服务端校验归属。素材权限来自实际上传和受信迁移，伪造图纸中的素材 URL 不会取得权限。

注册／登录按 ASGI 请求中的客户端 IP 和规范化邮箱做持久化窗口限流；业务代码不直接读取任意转发头。正式部署需在服务器层正确配置可信代理来源与边缘限流；若没有还原真实客户端 IP，代理后的所有用户会共用代理 IP 限额。

尚未接邮件验证与自助找回密码。服务器操作人核实账号身份后可执行以下命令，交互输入新密码并撤销该账号全部旧会话（密码不会进入命令行参数）：

```bash
uv run stitchforge-admin reset-password --email owner@example.com
```

## 验证与剩余部署工作

- `uv run pytest`：账号隔离、撤销与过期会话、CSRF、限流、旧数据迁移、支付入账幂等、余额并发、失败不扣次及原引擎测试。
- `pnpm --dir apps/web test`、`pnpm --dir apps/web lint`、`pnpm --dir apps/web build`。
- `uv run --with playwright python tools/check_web_accounts.py`：构建后启动独立临时 API／数据目录，真实注册、换账号、免费 OXS、图片扣次、手机双指缩放；截图保存到 `output/account-review/`。不修改用户现有数据，也不调用真实支付或 Codex。

图片生成已接入 SQLite 持久队列和独立计算进程，支持请求幂等、刷新查询、取消和租约恢复；每用户最多 1 个在途图片任务，全局最多 32 个在途任务、同时计算 1 个。生成版本、扣次账本和任务成功状态在同一事务提交。上传前检查余额与配额，正式入队和提交时再次检查。运行方式与 TDD 验证见[架构优化说明](architecture-optimization-2026-09-13.md)。可信代理限流、邮件服务、商户联调与备份恢复演练仍需部署时完成；该版本尚不是已上线的完整收款服务。

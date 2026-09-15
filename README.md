# StitchForge

AI 辅助十字绣打版工作台：把图片转换为可编辑的针脚设计，并从同一份针脚数据生成图纸、预览和材料表。

当前已从自用工作台扩展为账号制网站：用户注册登录后管理自己的图纸，免费导入 OXS；图片生成成功并保存后扣 1 次，失败不扣次数。支持手机查看、同色高亮、双指缩放与拖动。充值订单、余额与账本已实现，支持微信支付、支付宝和 Stripe 的独立适配器；尚无商户配置与真实联调，支付默认未开放。

多渠道支付配置见[支付说明](docs/payment-providers.md)；账号配置、旧图纸迁移、充值接入边界和回归方法见 [账号与计次说明](docs/accounts-and-billing.md)。已有本地图纸保留在旧工作区，需要指定目标账号迁移；不会自动归给首个注册用户。

打开图纸后可通过「下载图纸」免费保存 OXS 或完整 PNG 图纸。PNG 包含彩色网格、字母符号、坐标刻度和色号对照表；下载前会保存当前修改。中文字体部署要求见[图纸下载说明](docs/accounts-and-billing.md#图纸下载)。

图片分析可接入 DeepSeek-V4.1-Flash：在项目根目录 `.env` 填写 `DEEPSEEK_API_KEY`，启动时自动读取。详见 [AI 图片分析配置](docs/ai-analysis.md)。

## 本地运行

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
uv run stitchforge validate examples/minimal-pattern.json
uv run stitchforge stats examples/minimal-pattern.json
uv run stitchforge export examples/minimal-pattern.json \
  --output-dir output/example \
  --allow-test-fixture
uv run stitchforge import-oxs examples/minimal-pattern.oxs \
  --output output/imported-pattern.json \
  --report output/import-oxs-report.json \
  --coordinate-profile oxs-v1-zero-based
uv run stitchforge export-oxs examples/minimal-pattern.json \
  --output output/example/pattern.oxs \
  --report output/example/export-oxs-report.json \
  --coordinate-profile oxs-v1-zero-based \
  --confirm-loss
uv run stitchforge normalize-image input.png \
  --output output/image/normalized.png \
  --report output/image/normalization-report.json
uv run stitchforge crop-image output/image/normalized.png \
  --box 0,0,1200,900 \
  --output output/image/cropped.png \
  --report output/image/crop-report.json
uv run stitchforge sample-image output/image/cropped.png \
  --grid 160x120 \
  --coverage-threshold 0.5 \
  --remove-border-background \
  --source-profile rendered-stitch \
  --recovery-report output/image/recovery-report.json \
  --background-report output/image/background-report.json \
  --output output/image/grid-sample.json
uv run stitchforge build-pattern output/image/grid-sample.json \
  --catalog examples/synthetic-material-catalog.json \
  --color-budget 6 \
  --palette-strategy clustered \
  --pattern-id demo_pattern \
  --title "图片打版测试" \
  --purpose test_fixture \
  --output output/image/pattern.json \
  --report output/image/palette-report.json
uv run stitchforge optimize-pattern output/image/pattern.json \
  --sample output/image/grid-sample.json \
  --output output/image/pattern-optimized.json \
  --report output/image/optimization-report.json
uv run pytest --cov=stitchforge --cov-report=term-missing --cov-branch
```

示例只使用人为定义的 TEST 黑白材料。`--allow-test-fixture` 只允许生成带测试标识的开发产物，不能将它当成真实绣线图纸。

OXS 目前只接受 `oxs-v1-zero-based` 全针坐标配置。半针、回针、珠绣、混色、未知扩展、重复坐标和不明确尺寸都会产生定位报告并阻止导入。导出要求 `--confirm-loss`，因为 OXS 1.0 无法完整保存顶层针方向、来源哈希和 StitchForge 区域策略。该 profile 已通过项目内非对称样例往返测试；2026-09-12 的 120×180、23 色兔子样例已由用户截图确认能导入 Cross Stitch Saga。具体 Saga 版本尚未记录，第二版配色仍待同一软件复核。

图片入口只接受实际格式为 JPG 或 PNG 的单帧文件，限制为 20 MiB、解码后 40 MP。规范化会应用 EXIF 方向，把可用 ICC 转到 sRGB，保留 alpha；无 ICC 时在报告中记录 sRGB 假设。裁切采用半开像素坐标，也可以通过 `mask-image` 乘入单帧 PNG 蒙版。网格采样按线性 RGB 和预乘 alpha 计算，透明像素的隐藏 RGB 不参与平均，覆盖率低于阈值的格子输出为 `null`。对带纯色底布的 RGB 效果图，可显式开启 `--remove-border-background`：它以边缘中位色估计底布，仅移除与画布边缘连通且色差在阈值内的格子，并把判断依据写入报告。默认 ΔE00 阈值为保守的 2；浅色主体仍需查看预览或使用人工蒙版。已有刺绣效果图可以使用 `--source-profile rendered-stitch`，对亮、低饱和线体做有限的明度恢复，避免把模拟线缝阴影误当成较深 DMC 色；该模式会单独生成恢复报告，普通图片不自动套用。

`build-pattern` 支持两种确定性配色：`greedy` 直接降低全局 CIEDE2000，`clustered` 先在 Lab 中保留主要色组与小面积差异色，再映射到真实材料，适合颜色预算有限且关键小色容易丢失的图片。两种策略都支持 1–64 色预算、重复运行稳定和 `--required-material` 必选材料，以及逐设计指定 `--fabric-count` 和 `--strands`。工作台图片转换默认使用 `clustered`。`catalogs/` 中提供 DMC 2021 的 489 个纯色身份，其中 454 色带近似屏幕 sRGB，01–35 明确标为 unknown；它不是实体绣样测量。`examples/` 中的 synthetic 目录仍只用于 `--purpose test_fixture`。`optimize-pattern` 最多进行五轮小连通域搜索，锁定区域不会改变，preserve 区域受局部色差上限保护。

按成品尺寸比较不同 CT 对应的针格：

```bash
uv run stitchforge plan-grid \
  --image-width 4000 --image-height 3000 \
  --max-width-mm 300 --max-height-mm 300 \
  --fabric-counts 14,16,18,20
```

启动本机 API（默认只监听 `127.0.0.1:8765`，数据写入 `.stitchforge-data/`）：

```bash
uv run stitchforge-api
```

该命令默认同时启动后台图片 worker。若需要单独管理计算进程，在 `.env` 设置 `STITCHFORGE_WORKER_ENABLED=0`，再另开终端运行 `uv run stitchforge-worker`；两个进程必须使用同一数据目录和色库配置。

工作台右上角的“导入图片”只需选择 JPG／PNG，由配置的 AI 适配器建议网格、颜色数、CT、股数和输入类型；不可用时使用本地方案，随后
由后台 worker 完成规范化、可选底布移除、可选效果图浅色恢复、DMC 配色、小色块优化和版本保存。网页显示阶段进度，支持取消及刷新后查询结果；相同请求重试不会重复扣次。“原图对照”
会显示本次打版素材；原图和规范化图片按 SHA-256 存入本机内容寻址目录。

基础接口位于 `/api/v1`，当前包括项目创建／删除、版本读取／提交、图片转换、素材读取、针格命令、撤销／重做、任务创建／查询／取消。图片转换要求当前工作头版本，冲突时不会覆盖较新的编辑；整份版本保存和针格命令都会校验 locked 区域。针格命令带基础版本和命令 ID，能阻止过期覆盖及重复提交。每次请求返回 `X-Request-ID`，请求需要登录会话；写请求还需 `X-StitchForge-Request: 1` 和允许的 `Origin`。正式部署配置 `STITCHFORGE_PUBLIC_ORIGIN`。网页使用 `POST /projects/{id}/image-jobs` 入队；旧 `/convert-image` 保留兼容，与 worker 共享单机计算并发限制。worker 目前只执行 `image_conversion`，其他通用任务类型仍为接口骨架。

另开一个终端启动浏览器工作台：

```bash
cd apps/web
pnpm install
pnpm dev
```

访问 `http://127.0.0.1:5173` 并注册登录。Canvas 首次打开图纸时会自动缩放到完整视图，也可随时点击“适应窗口”；支持高 DPI、滚轮缩放、按住空格临时拖动、中键或移动工具平移、画笔、取色、留空和矩形选区。原图按可用宽度显示，超出高度时可纵向滚动。前端测试覆盖边界坐标、完整视图缩放、锚点缩放、300×300 可见区裁剪、临时平移判定、快速拖线和反向选区。

工作台默认使用查看模式：点击针格和右侧色表可双向定位并高亮同色针格，显示每色针数，支持符号和网格开关。“原图对照”会左右并排显示原图与图纸。切换编辑模式后启用画笔、橡皮擦、连通填色、选区填充/清空以及撤销/重做。连续拖动按一笔记录，会话内保留最近 100 步。未保存草稿与有容量上限的撤销／重做记录按账号、项目存入 IndexedDB，刷新后可恢复；服务器版本冲突时保留草稿，并提供另存或加载服务器版本。已保存且无修改时清理本地草稿，服务端保存图纸版本。详细快捷键与浏览器回归入口见 [Web 说明](apps/web/README.md)。

固定针格时，CT 只改变成品尺寸；固定成品尺寸时，更高 CT 才能提供更多针格。股数目前作为明确的制作参数保存，待实体绣样校准后再参与颜色预测。

## 文档入口

- [少量线色合并](docs/rare-color-optimization-2026-09-14.md)：普通图片生成时的保守减色、轮廓保护与 TDD 验证。
- [架构优化与 TDD 验证](docs/architecture-optimization-2026-09-13.md)：当前流程图、后台运行方式、已修复问题和验证结果。
- [系统设计](docs/design.md)：产品范围、技术架构、算法、编辑与任务接口、交付流程及设计取舍。
- [数据契约](docs/data-contract.md)：针脚、材料、配方、坐标、锁定、版本与工程文件规范。
- [研发与验收计划](docs/implementation-plan.md)：实施顺序、测试样例、性能目标和外部验证事项。
- [原始参考文档](docs/references/ai_cross_stitch_engine_research_and_design.md)：从 Downloads 原样复制，保留调研背景和原始来源。
- [PatternDocument Schema](schemas/pattern-document.v0.1.schema.json)、[材料目录 Schema](schemas/material-catalog.v0.1.schema.json)与[最小示例](examples/minimal-pattern.json)：供实现时使用的数据结构基线；跨字段语义约束见数据契约。

设计以 `docs/design.md` 和 `docs/data-contract.md` 为准；原始参考中的未决建议已在正式设计中收敛或列入验证清单。文档中的性能阈值是验收目标，兼容性、实体色卡和试绣结果尚待实际验证。

Python 依赖使用 **uv**，前端依赖使用 **pnpm**；实际依赖版本在搭建代码骨架时锁定。

## 图纸管理

网页顶部「图纸库」统一管理新建、图片生成及 OXS 导入的作品，提供针格缩略图、名称搜索、时间／名称排序和分页。导入图片默认新建独立作品；重命名同步图纸标题并保存新版本。移入回收站的作品可恢复，原始素材与历史版本继续保留。

账号体系启用后，旧 `local` 工作区图纸需要按[迁移说明](docs/accounts-and-billing.md#旧图纸迁移)分配给指定账号；所有原始数据仍保留。默认数据库：`.stitchforge-data/metadata.sqlite3`，素材目录：`.stitchforge-data/artifacts/`；`STITCHFORGE_DATA_DIR` 可调整保存位置。备份时停止后端并复制整个数据目录（含可能存在的 WAL 文件）。

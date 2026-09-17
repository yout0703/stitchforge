# AI 图片分析

已接入 DeepSeek 官方 Chat Completions API，负责看图建议针格大小、颜色预算、CT、股数和背景处理；实际取色、针格生成及 OXS/PNG 导出仍由项目引擎完成。

2026-09-13 核对官方当前文档：DeepSeek-V4.1-Flash 的模型 ID 为 **`deepseek-flash`**，支持图片输入。请求地址为 `https://api.deepseek.com/chat/completions`，使用 Bearer API Key、base64 PNG 图片、JSON Output，关闭思考模式以控制参数分析的延迟与输出量。

- [官方首次调用说明](https://api-docs.deepseek.com/zh-cn/)
- [官方模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- [官方图像理解接口](https://api-docs.deepseek.com/zh-cn/guides/vision/)
- [官方 JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode)

## 填写 Key

在 [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) 创建 API Key，填入项目根目录 `.env`：

```dotenv
STITCHFORGE_AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=在这里填写你自己的Key
STITCHFORGE_DEEPSEEK_MODEL=deepseek-flash
STITCHFORGE_DEEPSEEK_TIMEOUT=60
STITCHFORGE_CODEX_ENABLED=0
```

`.env` 已被 Git 忽略。Key 仅由后端读取，不放入 `apps/web/.env` 或 `VITE_*` 变量。`stitchforge-api` 从启动目录读取 `.env`，已有进程环境变量优先。修改后在项目根目录重启后端：

```bash
uv run stitchforge-api
```

前端仍使用 `pnpm --dir apps/web dev`。生成图纸后右侧「自动方案」会显示「DeepSeek 分析」或「本地分析」及回退原因。填写 Key 前无法进行真实服务联调；本地自动方案仍可使用。

## 行为与限制

- 自动导入先定位主体，分别检测横纵针距，保留可靠与部分可靠的证据。程序生成最多 3 个候选，按原图轮廓检查覆盖率和误填面积；候选针格由图像证据计算，AI 不能直接重写这些针格。
- 配置 DeepSeek 时，会把原图主体与候选的实际 DMC 图纸一起发送复核，检查尖端、眼睛、细茎、叶片、空隙和独立装饰针。只接受已有候选 ID 的结构化结果；若多个候选针脚位置完全相同且全部通过复核，优先选择线色较少的方案。网页展示复核状态及说明，报告随图纸保存。
- 复核拒绝或返回无效数据时停止生成，不保存新版本、不扣次数。只有部分针距可靠的候选必须通过 AI 复核；服务不可用时提示稍后重试。未开启 DeepSeek或临时无法连接时，双轴针距可靠且本地结构检查通过的方案仍可生成，并明确显示仅通过本地检查，不冒充 AI 复核通过。每次转换最多一次结构复核请求，不自动重试外部收费请求；通过后成功保存仍扣 1 次网站生成次数。
- 普通图片继续使用原有转换流程。成品绣片若横纵针距一致（底布网格或绣线周期），按针距生成小图，不再交给 AI 放大成 480–600 格；若一针一格长边不足 160，再加密一倍以免窗格、细茎被平均掉。AI 整图尺寸建议仍须与检测针距吻合，防止把主体针数误当成整张照片的针格数。白色装饰、透视变形、倾斜或证据不足的照片仍可能无法可靠还原。手动指定尺寸保持不变。
- 独立图案默认将外围底布留为空针，画布底色默认为白色。刺绣照片先估计布纹的颜色变化并遮去底布，再缩小到针格，减少白点及底布混入轮廓；主体内封闭的浅色装饰仍保留。复杂风景、满幅画作可由分析器选择保留背景。
- 简单爱心、小花等优先建议整图长边 24–64 格、2–8 色；复杂构图仍支持 600 格长边、64 色。建议尺寸对应整张原图，自动转换后只裁去空针边距，四周留 2 格，最终网格以图纸为准。原始文件仍保留，查看模式使用对齐后的裁切图，裁切记录保存在图纸扩展字段中。手动指定针格时保持请求的画布大小。
- 这些建议和颜色分割不能保证精确复原照片中的原生针格；与布料十分接近且连通的浅色主体仍可能难以区分，生成后应检查轮廓和装饰。
- 参数分析只发送缩小至最长 1536 像素的图片和提示；结构复核发送最多 3 张 1000×680 的主体／候选对照图，不发送账号、文件名、本地路径、EXIF 或账单。启用后，图片内容会传到 DeepSeek 进行分析。
- 校验 JSON 字段、类型、针格尺寸、长宽比及颜色数量，禁止超过原图分辨率和 600×600 格上限。
- 缺少 Key、认证失败、余额不足、限流、超时或无效建议时自动回退本地方案，并显示原因；不自动重试收费请求，也不再尝试另一外部供应商。
- 网站只在图纸成功保存后扣 1 次，回退后成功生成也扣 1 次。DeepSeek 平台 API 费用独立计算；失败／超时是否产生平台费用以平台账单为准。
- 分析报告记录供应商、成功调用的模型名和已验证参数，不保存 Key、原始服务错误或推理文本。
- `STITCHFORGE_AI_PROVIDER=local` 完全使用本地方案。`codex` 保留原有 CLI 适配；未设置供应商时兼容旧版 Codex 开关及公开站点默认关闭行为。

针距识别同时检查颜色覆盖与针线明暗的周期，支持黄色等连成大片的线材。小面积的黑／深棕眼睛会作为独立针脚保留；合并线色时同时检查颜色比例和亮度差，避免把深棕眼睛并入橙色。接近底布颜色的白色装饰仍可能无法可靠区分，识别器会保守退出。

测试：`uv run pytest tests/test_structure_review.py tests/test_counted_stitches.py tests/test_deepseek_planning.py tests/test_import_planning.py`。测试使用模拟 HTTP 服务，不发送真实图片到 DeepSeek，也不消耗真实 API 额度。

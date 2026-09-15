# StitchForge Web

React 19 + TypeScript + Vite 的账号制打版界面。开发服务器把 `/api` 代理到 `127.0.0.1:8765`。

```bash
pnpm install
pnpm dev
pnpm lint
pnpm test
pnpm build
```

首次访问进入图纸库；有上次打开记录时恢复图纸，刷新图纸库则停留在图纸库。打开图纸默认进入查看模式：点击针格定位线色，点击色表高亮全图同色针格，显示每色针数；支持符号、网格开关、自由拖动和滚轮缩放。查看模式不响应修改图纸的快捷键。

“原图对照”为左侧原图、右侧图纸，两种模式都可使用。原图默认完整显示，点击“放大查看”后可滚动；图纸可单独缩放、平移，点击“适应窗口”恢复全图。

编辑模式支持画笔 B、取色 I、选区 S、移动 H、橡皮擦 E 和连通填色 G，选区可填色、清空或取消。一次连续画笔/橡皮拖动算一步，最近 100 步可撤销/重做。未保存草稿连同基础版本和撤销／重做记录存入 IndexedDB，刷新后恢复；每个方向最多保留 100 步、累计 100 万个针格变更，过大的旧记录会被裁掉，草稿图纸仍完整保留。保存后没有修改时删除本地草稿，刷新后的已保存图纸不恢复会话撤销栈。切换查看/编辑模式不会清空操作历史。

底部状态栏显示草稿保存状态。服务器版本变化时保留本地修改，可选择“另存为新图纸”或“载入服务器版本”；加载失败不会删除本地草稿。浏览器存储不可用时显示错误，不把未落盘内容标成已保存。

快捷键：⌘/Ctrl+Z 撤销，⌘/Ctrl+Shift+Z 或 Ctrl+Y 重做，⌘/Ctrl+S 保存，⌘/Ctrl+A 全选，Delete/Backspace 清空选区，Esc 取消选区。输入框内保留原生快捷键，关闭含未保存修改的页面会提示。

浏览器交互回归（需要先启动前端及本机安装 Chrome）：

```bash
cd ../..
uv run --with playwright python tools/check_web_viewport.py
uv run --with playwright python tools/check_web_editing.py
uv run --with playwright python tools/check_web_recovery.py
uv run --with playwright python tools/check_web_generation.py
```

使用隔离浏览器和模拟接口，验证页面高度、空格连续拖动与跨边界拖动、滚轮缩放及原图滚动到底部，不修改本机项目。

图片导入无需填写参数。通过项目根目录 `.env` 中的 `STITCHFORGE_AI_PROVIDER=deepseek` 和 `DEEPSEEK_API_KEY` 接入 DeepSeek-V4.1-Flash（API 模型名 `deepseek-flash`），验证结构化建议后生成图纸；不可用、超时或建议无效时自动使用本地方案。配置与切换说明见 [AI 图片分析](../../docs/ai-analysis.md)。CT/股数为制作建议，既有针格的精确恢复仍需专门的网格检测。分析依据随版本保存，右侧可查看。仅在使用 `STITCHFORGE_AI_PROVIDER=codex` 时调用本机 Codex CLI；设置 `STITCHFORGE_CODEX_ENABLED=0` 可关闭 CLI，`STITCHFORGE_CODEX_TIMEOUT` 控制超时秒数（默认 60），`STITCHFORGE_CODEX_MODEL` 可指定模型。

“导入 OXS”读取全针图纸的针格、线色、CT 和股数，新建独立项目并进入查看模式；不支持的针法会给出报告并阻止导入。实时坐标和针格线色固定在右侧顶部，滚动色表不会将其卷走。

额外导入回归（临时启动 API，使用临时数据库，不修改当前项目）：

```bash
uv run --with playwright python tools/check_web_imports.py
```

Codex 调用按[官方非交互执行文档](https://developers.openai.com/codex/noninteractive/)使用图片附件和 JSON Schema 输出，本机已经登录的 CLI 可直接复用。

图纸库入口在工作台顶部，可查看已有作品的针格缩略图、尺寸、CT、色数、来源和更新时间，按名称搜索，按更新／创建时间或名称排序，每页 24 份。新建、图片生成及 OXS 导入都会保存为独立项目，导入新图片不会覆盖正在查看的作品。切回图纸库前会提醒处理未保存修改。

重命名同时更新列表和图纸标题，创建新版本并检查当前版本，避免覆盖另一页面的修改。移入回收站后可恢复，图纸、历史版本和原始素材不永久删除。现有项目会自动出现在图纸库，无需重新导入。

默认持久化位置是项目根目录下 `.stitchforge-data/metadata.sqlite3`，原图等素材存放在 `.stitchforge-data/artifacts/`，可通过 `STITCHFORGE_DATA_DIR` 调整。备份时停止后端并复制整个数据目录，包括可能存在的 SQLite WAL 文件，不要只复制单个数据库文件。已保存作品由后端持久保存；浏览器按账号隔离保存最近项目、待查询生成请求和 IndexedDB 编辑草稿。本地草稿依赖当前浏览器存储，不跨设备同步。

图片提交后进入持久队列，显示阶段状态并可取消。刷新会继续查询已提交任务；完成后可打开结果，不覆盖期间的新编辑。上传响应丢失时重新选择同一文件可复用请求标识。`uv run stitchforge-api` 默认启动 worker；运行参数与恢复边界见[架构优化说明](../../docs/architecture-optimization-2026-09-13.md)。

`tools/check_web_imports.py` 同时覆盖图纸库搜索、排序、分页、重命名和刷新恢复、回收站、独立图片导入、失败保护、未保存修改提示及窄屏固定顶栏。

图片生成与 OXS 导入、查看及版本保存统一支持单边最多 600 格，总计最多 360,000 格。自动分析会为多人、文字和建筑等复杂大图优先选择 480–600 格长边及较多线色；保留图片比例，不放大超过原图像素。配色聚类分块计算以限制临时内存。

普通图片生成会保守合并少量近似线色，右侧“线色精简”显示生成时减少的颜色与改动针数，刷新后仍可查看。细线、透明边界和受保护区域优先保留；精确数针还原不执行此减色步骤。现有图纸不会自动改写，详细规则见[少量线色合并说明](../../docs/rare-color-optimization-2026-09-14.md)。

大图纸实文件回归可运行 `uv run --with playwright python tools/check_web_large_oxs.py /path/to/chart.oxs`，在临时 SQLite 中验证全部针格映射、取色、保存及图纸库恢复，不改动现有项目。

## 账号与移动端

首次打开需要注册或登录；OXS 免费，图片生成成功扣 1 次。顶部账号入口查看余额与账本。手机支持双指缩放与拖动。支付适配器已实现，未配置商户时不开放收款，详见[账号与计次说明](../../docs/accounts-and-billing.md)。

新增完整浏览器回归使用独立临时数据、真实账号与前端构建产物，无需预先启动开发服务：

```bash
pnpm build
cd ../..
uv run --with playwright python tools/check_web_accounts.py
```

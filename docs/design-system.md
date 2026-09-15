# StitchForge Design System (针织工坊设计系统)

版本：v1.0.0 (生产级标准)  
定位：AI 辅助十字绣打版工作台 · 数字工匠设计系统（Artisan Modernist Studio）

---

## 1. 设计哲学与核心理念

StitchForge 结合了 **数字生产力工作台的精密秩序** 与 **传统刺绣手作的温润匠心**。设计系统遵循三大核心支柱：

1. **精密克制 (Precision & Restraint)**
   - 界面作为创作的依托画布，自身不喧宾夺主。
   - 网格、刻度、色号与坐标采用等宽/表格式数字 (`tabular-nums`)，毫厘毕现。
   - 边框精细（0.5px ~ 1px），色彩传达明确语义。

2. **质感与景深 (Tactile Depth & Materiality)**
   - 借鉴布料编织经纬、丝线光泽与专业工艺台的物理质感。
   - 使用多层自然弥散阴影（Ambient + Direct Shadow）替代生硬的单一黑影。
   - 玻璃拟态与毛玻璃层级（Backdrop blur）赋予浮层通透的悬浮秩序。

3. **直觉流转 (Intuitive Ergonomics)**
   - 快捷键与视觉操作同等优先，每个工具提供键盘指引。
   - 模式状态（查看/编辑、画笔/取色/选区/移动/橡皮/填色）具备极高的视觉辨识度与即时触控反馈。
   - 支持移动端双指手势、平板触控与桌面高精度鼠标多端无缝体验。

---

## 2. Design Tokens (设计令牌体系)

所有基础样式均通过 CSS 变量统一注入 `:root`，支持全局维护与未来暗色模式扩展。

### 2.1 色彩系统 (Color Palette)

#### 品牌主色 (Brand & Accent - 织锦靛蓝)
- `--sf-accent-50`: `#eff5ff` (超轻底色、高亮背景)
- `--sf-accent-100`: `#dbe7fe` (边框、选中次级高亮)
- `--sf-accent-200`: `#bfd5fe` (聚焦指示)
- `--sf-accent-500`: `#2c62f6` (品牌主色、主交互按钮)
- `--sf-accent-600`: `#1c4fd8` (悬停加深)
- `--sf-accent-700`: `#143cb8` (点击激活)
- `--sf-accent-glow`: `rgba(44, 98, 246, 0.22)` (呼吸光晕)

#### 中性色与排版墨色 (Neutrals & Inks)
- `--sf-ink-900`: `#0f172a` (主标题、关键数字、高对比度文字)
- `--sf-ink-800`: `#1e293b` (常规正文、强调文本)
- `--sf-ink-700`: `#334155` (二级正文、工具标签)
- `--sf-ink-500`: `#64748b` (次级文本、辅助说明、状态标签)
- `--sf-ink-400`: `#94a3b8` (弱化提示、快捷键说明)
- `--sf-ink-300`: `#cbd5e1` (占位符、微弱边框)

#### 表面与层级背景 (Surfaces & Backgrounds)
- `--sf-bg-canvas`: `#e6e9ee` (工作台底色、具有经纬微质感)
- `--sf-bg-panel`: `#ffffff` (核心卡片、弹窗与面板底色)
- `--sf-bg-panel-subtle`: `#f8fafc` (侧边栏与工具栏底色)
- `--sf-bg-muted`: `#f1f5f9` (输入框内底、凹槽轨道底色)
- `--sf-bg-glass`: `rgba(255, 255, 255, 0.88)` (毛玻璃磨砂浮层)

#### 语义反馈色 (Feedback)
- 成功/已同步：`--sf-success`: `#10b981`, `--sf-success-bg`: `#ecfdf5`, `--sf-success-border`: `#a7f3d0`
- 告警/草稿未保存：`--sf-warning`: `#f59e0b`, `--sf-warning-bg`: `#fffbeb`, `--sf-warning-border`: `#fde68a`
- 错误/冲突：`--sf-danger`: `#ef4444`, `--sf-danger-bg`: `#fef2f2`, `--sf-danger-border`: `#fecaca`
- 信息提示：`--sf-info`: `#0284c7`, `--sf-info-bg`: `#f0f9ff`, `--sf-info-border`: `#bae6fd`

---

### 2.2 空间与度量 (Spacing & Sizing)
采用 4px 栅格基底系统：
- `space-1`: `4px`
- `space-2`: `8px`
- `space-3`: `12px`
- `space-4`: `16px`
- `space-5`: `20px`
- `space-6`: `24px`
- `space-8`: `32px`
- `space-12`: `48px`

### 2.3 圆角系统 (Radius)
- `--sf-radius-xs`: `4px` (标签、徽章、微小指示器)
- `--sf-radius-sm`: `6px` (次级按钮、快捷键键帽、输入项)
- `--sf-radius-md`: `9px` (标准按钮、控制条胶囊、分段项)
- `--sf-radius-lg`: `14px` (卡片、侧边面板、图纸卡片)
- `--sf-radius-xl`: `20px` (弹窗 Dialog、主浮层)
- `--sf-radius-full`: `9999px` (圆形线样、药丸胶囊)

### 2.4 景深与阴影系统 (Elevation & Shadows)
- `--sf-shadow-xs`: `0 1px 2px rgba(15, 23, 42, 0.04)`
- `--sf-shadow-sm`: `0 1px 3px rgba(15, 23, 42, 0.08), 0 1px 2px rgba(15, 23, 42, 0.04)`
- `--sf-shadow-md`: `0 4px 12px -2px rgba(15, 23, 42, 0.08), 0 2px 6px -1px rgba(15, 23, 42, 0.04)`
- `--sf-shadow-lg`: `0 12px 28px -4px rgba(15, 23, 42, 0.12), 0 4px 10px -2px rgba(15, 23, 42, 0.04)`
- `--sf-shadow-xl`: `0 24px 48px -12px rgba(15, 23, 42, 0.22), 0 8px 16px -4px rgba(15, 23, 42, 0.08)`
- `--sf-shadow-glow`: `0 0 0 3px var(--sf-accent-glow)`

---

## 3. 核心组件交互规范 (Component Patterns)

### 3.1 导航栏与顶栏 (Global Header)
- 高度固定 64px（移动端自适应），采用磨砂毛玻璃浮空层级。
- 品牌标识：带有微型几何刺绣印记的 Logo Badge，清晰易读。
- 项目状态指示：
  - 绿点：已保存至云端 / 实时同步。
  - 琥珀色点：有未保存本地草稿，实时保护中。
- 操作权重清晰分层：
  - **Primary**：突出操作（如「保存版本」、「下载图纸」）。
  - **Quiet / Ghost**：常规入口（「图纸库」、「导入图片」）。
  - **Badge Button**：个人账号与额度胶囊（显示剩余次数与入口）。

### 3.2 绘图工具栏 (Tool Rail)
- 垂直悬浮工具岛设计，背景轻微通透。
- 工具项带有清晰中文字样与键盘快捷键（`<kbd>` 标签，如 `B`、`I`、`S`、`H`、`E`、`G`）。
- 选中态：微发光指示条与主色浅染背景，点击提供触觉缩放反馈 (`active: scale(0.97)`）。

### 3.3 模式与视图控制器 (Segmented Controls)
- 采用现代拟物/高阶扁平滑块轨道结构。
- 包含查看模式 vs 编辑模式、针格视图 vs 原图对照视图。
- 选中项具有背景白色卡片浮起与微阴影，切换流畅平滑。

### 3.4 刺绣色表 (Palette Inspector)
- 绣线样本（Thread Swatch）：拟物化圆环高光与真实 sRGB 线色呈现，带白色高光边圈。
- 符号指示（Recipe Symbol）：等宽字体、清晰边框，支持放大查看。
- 详细信息：品牌代号、颜色名称、当前针数分布、推荐股数一览无余。
- 选中与高亮：强对比蓝色边框与整行聚焦，右侧对应高亮针数指示。

### 3.5 图纸库画廊 (Project Gallery)
- 卡片网格：响应式自适应列宽（最小 280px）。
- 缩略图视窗：底布网格质感衬底，图像采用像素级无模糊渲染 (`image-rendering: pixelated`)，鼠标悬浮平滑微放大。
- 操作区：常用功能（打开、重命名、移入回收站）清晰易点，危险操作提供防误触防护。

### 3.6 对话框与弹窗 (Dialogs & Modals)
- 居中自适应，最大高度不超过视口 90vh，内部内容独立流畅滚动。
- 遮罩层带有景深模糊效果 (`backdrop-filter: blur(8px)`)。
- 明确的 ESC、遮罩点击或取消按钮支持。

---

## 4. 后续页面扩展规范指南

为保证团队或后续新增页面（如个人设置页、线色管理库、图纸社区、批处理工具）严格保持风格统一，请遵循以下开发约定：
1. **统一引用 Token**：任何新增组件或页面，背景色、文字颜色、边框、阴影一律使用 `--sf-*` 系列变量，禁止在局部随意 hardcode `#xxx` 纯色。
2. **遵守交互层级**：重要操作全局单一主按钮（Primary），次要操作均使用 Quiet/Ghost 样式。
3. **保持状态可感知**：加载态添加统一骨架屏或 Spinner，成功/失败提示必须具备无障碍 role 属性（`role="status"` 或 `role="alert"`）。
4. **数字严谨**：所有规格、坐标、统计数字必须应用 `font-variant-numeric: tabular-nums`。

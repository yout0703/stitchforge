# StitchForge 前端生产级重构与长期体验优化 TODO List

本文档跟踪 StitchForge 前端由自用工作台迈向商用上线级体验的重构规划与实施进度。

---

## 阶段一：核心设计系统与基础样式重构 (Phase 1: Foundation & Design System)
- [x] **建立全局 Design Tokens 规范**
  - [x] 注入核心色彩系统（织锦靛蓝、中性墨色、表面质感色、功能反馈色）
  - [x] 建立间距（Spacing Scale）、圆角（Radius Scale）和阴影系统（Elevation Shadows）
  - [x] 统一全局字体、排版与等宽数字系统（`tabular-nums`）
- [x] **工作台主壳框架 (Workbench Shell) 质感升级**
  - [x] 背景增加十字绣手作质感与经纬微肌理氛围
  - [x] 优化顶部 Header 磨砂玻璃层级、品牌 Badge、项目状态实时呼吸指示灯
  - [x] 头部操作区层次分明化：账号额度胶囊、常用入口与主操作分级

---

## 阶段二：工作台核心交互与画布体验优化 (Phase 2: Canvas & Workbench Ergonomics)
- [x] **工具栏（Tool Rail）浮岛化重塑**
  - [x] 升级为现代工匠工具箱设计，悬浮轻阴影与圆角
  - [x] 工具按钮（画笔、取色、选区、平移、橡皮擦、填色）激活态微发光指示
  - [x] 精致的键盘快捷键键帽（`<kbd>` 样式）
  - [x] 优化查看模式提示卡（手势说明、查线色指引）
- [x] **视口与辅助控制条 (Stage Toolbar)**
  - [x] 现代分段选择器（Segmented Control）：针格视图 vs 原图对照
  - [x] 模式切换器（查看模式 vs 编辑模式）滑块胶囊美化
  - [x] 视口元数据展示（规格尺寸、CT数、色数）与「适应窗口」按钮高品质化
  - [x] 操作按钮分组与分隔线强化（撤销/重做、选区操作、符号/网格切换）
- [x] **底部状态栏 (Status Bar)**
  - [x] 精细化仪表盘设计，实时坐标与线色即时追踪
  - [x] 本地草稿保护状态指示（保存状态、同步冲突直观提示）
- [x] **右侧检查器与色表 (Inspector & Palette)**
  - [x] 实时针格信息卡片化，增强当前色号高亮展现
  - [x] 绣线样本（Thread Swatch）拟物化光泽感、清晰符号与针数统计
  - [x] 操作记录时间线、导入说明与 AI 方案指标美化展示

---

## 阶段三：图纸库画廊与工作流优化 (Phase 3: Gallery & Workflow)
- [x] **图纸库画廊 (Project Library)**
  - [x] 现代化画廊卡片网格布局与悬浮微浮起手感
  - [x] 缩略图容器添加底布网格质感衬底与像素无损渲染
  - [x] 搜索栏、排序选择器与全部/回收站标签切换胶囊美化
  - [x] 优雅的空状态（Empty States）与错误反馈
- [x] **登录注册落地页 (Account Gate)**
  - [x] 打造高品位品牌展示区与手作针织视觉氛围
  - [x] 悬浮玻璃认证卡片、输入框微动效与清晰福利展示
- [x] **对话框与弹窗体验统一 (Modals & Dialogs)**
  - [x] 统一 ImportDialog、AccountPanel、DownloadPanel 的视觉规范
  - [x] 磨砂背景模糊（Backdrop Blur）、平滑入场微动画
  - [x] 额度余额卡片、支付方式切换、下载格式选项视觉升华

---

## 阶段四：持续迭代与未来路线图 (Phase 4: Future Roadmap)
- [ ] **深色模式 (Dark Theme)**：基于 CSS Tokens 轻松切换深色刺绣工作台。
- [ ] **高阶画布手势与平滑惯性平移 (Physics-based Pan & Pinch)**：触控板与触摸屏的顺滑惯性。
- [ ] **线色搜索与色系筛选器 (Palette Filtering)**：在检查器中按色系（红、蓝、绿、中性）或 DMC 色号快速筛选。
- [ ] **图纸库批量管理 (Batch Actions)**：支持多选导出、批量归档与标签分类。
- [ ] **打版前后对比滑块 (Split Curtain Compare)**：原图与针格图左右滑杆即时透视对比。
- [ ] **实物效果模拟器 (Fabric Mockup 3D)**：模拟棉布、亚麻布真实绣线立体反光渲染。

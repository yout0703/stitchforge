# StitchForge 数据契约 v0.1

本文件配套[系统设计](design.md)、[JSON Schema](../schemas/pattern-document.v0.1.schema.json)和[最小样例](../examples/minimal-pattern.json)。这是全针 MVP 的实现契约，不是 OXS 或其他软件的格式规范。

## 1. 契约边界

`PatternDocument` 只表示已明确针脚含义的设计，允许尚未发布、尚未试绣的草稿。它不含识别未知格、任务进度、数据库主键关系或可变的“当前版本”状态。

结构约束分三层：

| 层 | 负责内容 | 实现要求 |
|---|---|---|
| JSON Schema | 类型、必填、范围、枚举、禁止未知核心字段 | 使用 Draft 2020-12 校验并启用日期 format 检查 |
| 语义校验器 | 数组形状、引用、ID 唯一、区域边界、配方／符号对应 | 见第 8 节；Schema 通过不能替代它 |
| 操作／发布校验 | 基础版本、锁定是否被改变、字体与出口能力、发布一致性 | 需要旧版本或导出配置；不能只检查新文档 |

v0.1.0 仅允许 `full_cross`，高级针法字段不存在。未知必需功能或 schema 版本拒绝编辑，原文件保留。不要用 `extensions` 绕过能力门禁保存未实现的针脚。解析器另限制 JSON 嵌套深度为 64 层、总节点数为 500,000，避免小体积深层数据耗尽解析资源。

## 2. 顶层结构

| 字段 | 内容 | 关键约束 |
|---|---|---|
| `schema_version` | `0.1.0` | 精确匹配版本 |
| `required_features` | `["full_cross"]` | 本版仅这一能力 |
| `metadata` | pattern_id、title、author、created_at、purpose | `purpose=user_design/test_fixture`；测试数据不可正式对外交付 |
| `grid` | width、height、origin、subgrid_scale | 每边 1–600 格、总数不超过 90,000 格；左上原点；子网格比例 2 |
| `fabric` | type、count、count_unit、worked_over、底色、留边 | 本版仅方格 Aida，CT 为 6–40 的整数，worked_over=1；常用值由实际布料目录推荐 |
| `materials` | 本版本所需材料快照列表 | ID 与材料身份约束见下文；最多 4,096 项 |
| `recipes` | 配方 ID、正整数 index、组成 | 本版每配方恰好一种材料、1–6 股；最多 4,096 项 |
| `full_stitches` | 行优先数组、编码声明、上层针方向 | cells 数量恰好 width×height |
| `regions` | 网格区域及 editable／preserve／locked | 区域通过行区间表达；允许空列表 |
| `legend` | recipe_id 到 symbol_id | 使用配方完整映射，实际使用符号不重复 |
| `constraints` | 颜色预算与匹配策略版本 | 实际使用配方不超过预算；允许保留未用配方 |
| `provenance` | 来源摘要、引擎、色库、规则与参数引用 | 无来源时显式空数组；未知不编造 |
| `extensions` | 可选的命名空间注释对象 | 只能存不影响针脚语义的附加信息 |

PatternDocument 不存 `revision_id`、`parent_revision_id` 和 `document_hash`；这些由 Revision 外层绑定。相同设计内容可以在多个恢复版本中复用相同文档哈希。更名会产生新的文档内容，但不会改变针脚统计。

## 3. 坐标、尺寸与数组

```text
格坐标：0 ≤ col < width，0 ≤ row < height
数组偏移：offset = row × width + col
网格顶点范围：0 ≤ x2 ≤ 2×width，0 ≤ y2 ≤ 2×height
格子左上顶点：(2×col, 2×row)
格子中心：(2×col+1, 2×row+1)
格子右下顶点：(2×col+2, 2×row+2)
```

全针用格索引，不用中心点代替。`top_leg=slash` 表示后绣、位于上层的一笔从左下到右上；`backslash` 表示左上到右下。本版全图统一方向，不能逐格无字段地改变。后续支持每针覆盖方向时必须增加显式数据能力。

`full_stitches.encoding` 固定 `row-major-u16`，表示元素逻辑上为无符号 16 位索引。JSON 传输和 `pattern.json` 都是整数数组，不是 Base64 或二进制。内存可用 uint16 数组，持久化可压缩整个 JSON；未来若引入二进制块，必须新定义容器编码及字节序。

0 明确表示不绣，非 0 必须引用一个配方 index。白色线同样使用正索引。尺寸和占用边界都是派生数据，不在文档中保存第二套可冲突数值。

```text
effective_stitches_per_inch = fabric.count       # 本版 Aida over one
canvas_width_mm = grid.width / fabric.count × 25.4
cloth_width_mm = canvas_width_mm + left_margin_mm + right_margin_mm
```

宽高分别换算，不取整到毫米后再计算其他结果。占用边界采用半开矩形 `[left,top,right,bottom)`；全空时为 `null`。MVP 留边以整数毫米保存，范围每边 0–500 mm。

## 4. 材料、配方与颜色

### 4.1 材料身份

材料字段：`id`、`brand`、`series`、`code`、`name`、`color`。品牌／系列使用目录统一名称，`code` 必须保留为字符串，例如字母线号或带前导零线号不得转为整数。界面显示名不作为引用键。

`color` 包含 `display_srgb`（`#RRGGBB` 或 null）、`status`、`source_ref`。本版状态：

| 状态 | 含义 | 可用范围 |
|---|---|---|
| `approximate` | 有出处的屏幕近似值，包括用户手工输入 | 可参与近似匹配，报告明确标注 |
| `measured` | 有测量记录及转换出处 | 需额外验证测量记录后才可这样标记 |
| `synthetic` | 人工定义的测试色 | 仅测试工程与测试导出 |
| `unknown` | 身份已知，显示色未知 | 仅手动编辑与统计；不参与自动匹配 |

`source_ref` 指向 `provenance.sources` 内的来源 ID，不能填一个无法解析的“官方”字符串。source 记录定位信息、说明、权限状态；权限状态是记录，不是本系统作出的法律认证。测量 source 的说明及随附记录必须包含仪器／方式、照明、观察者、采集时间和转换路径，否则语义校验不能认定为 measured。

自动匹配只纳入有显示色和允许使用范围的材料；明示为 unknown 时色值必须是 null。合格的实体目录与 synthetic 开发夹具分开，生产色库不得自动从测试夹具拼接。

### 4.1 材料目录快照

自动配色输入使用独立的 `MaterialCatalog v0.1.0`，结构由 [material-catalog.v0.1.schema.json](../schemas/material-catalog.v0.1.schema.json) 固定。目录包含稳定 `snapshot_id`、一个颜色来源记录，以及材料的品牌、系列、线号、显示色、颜色状态、可用状态和回退股数。品牌／系列／线号组合与材料 ID 都必须唯一。`default_strands` 只服务未显式指定股数的旧调用方，不表示该股数适合所有 CT、底布和覆盖效果。

`available=false` 或 `color_status=unknown` 的材料可以保存在目录中供手工统计，但不进入自动色差匹配。`synthetic` 目录只能生成 `test_fixture`；要生成自用正式设计，需要提供带来源说明和使用权限状态的实体材料目录。目录快照 ID、图片哈希和配色配置哈希都写入 PatternDocument provenance。

### 4.2 配方身份不可原地修改

```json
{
  "id": "r_black_2",
  "index": 1,
  "components": [{"material_id": "m_black", "strands": 2}]
}
```

`id` 是稳定语义引用，index 是此文档中的紧凑映射。相同 ID 跨版本必须保持材料和股数语义不变；调整两股为一股时新建 ID，再按选区替换引用。不要修改 `r_black_2` 的内容，使锁定格在索引未变时变成一股。

本版 components 长度只能是 1。允许相同材料不同股数的不同配方；不允许两个 ID 重复表达完全相同组成，导入时合并为规范配方并记录映射。未来混线版本按材料 ID 排序并合并重复组成，股数求和后校验，不能使用平均 RGB 代替材料记录。

材料快照内容若变化也创建新 ID。纯显示色校准可以在新的、明确的目录迁移操作中更新来源版本；即便真实线号不变，也必须展示预览变化，旧发布继续使用原快照。锁定区域引用的材料默认不自动升级。

### 4.3 稳定图例

`legend` 是 `{recipe_id,symbol_id}` 列表，symbol_id 是项目符号注册表中的语义 ID，例如 `s001`。具体 Unicode 字符、字体文件哈希和字号存在 ExportProfile，而非材料对象。

一个实际使用配方必须恰有一个图例条目；不同实际使用配方不能共享符号。导出先检查 profile 对所有 symbol_id 都有映射，且字体具有对应字形。没有注册表、缺字体或字形重复时拒绝相应符号图导出。未用配方可保留既有符号以支持撤销，但材料表只列实际使用项。

## 5. 区域、掩膜与锁定

区域字段为 `id,name,policy,spans`。spans 采用半开行区间：`{row,start_col,end_col}`，满足 `start_col < end_col`，并且不越界。每个区域按 row／start_col 排序，相邻或交叠区间合并。不是把每个像素点保存成单独对象。

不同区域允许重叠，硬规则取最强约束：`locked > preserve > editable`。优化权重取覆盖区域中的最大值，不重复累加无限增强。降低一个区域的 policy 不会自动解除另一区域的锁定。

图像空间掩膜是 pipeline Artifact，不直接存入网格区域；必须关联已定向原图哈希、裁切／缩放变换、输出网格宽高及栅格化规则。新裁切后的网格区域要重新映射并预览，不能复用旧 offset。

锁定操作在 Revision 命令记录中保存基准版本及保护区域摘要。后续操作验证前后格子是否存在、配方材料身份、股数、上层方向，以及影响锁定区域的网格／绣布设置。用户解锁和后续修改是两次明确操作。AI 不能提交解锁命令。

## 6. ImportDraft 与不确定性

ImportDraft 是独立的识别草稿结构，v0.1 PatternDocument Schema 不接受以下对象：

```json
{
  "draft_id": "import_1",
  "source_asset_id": "asset_1",
  "width": 4,
  "height": 3,
  "cells": [
    {
      "col": 0,
      "row": 0,
      "resolution": "unresolved",
      "candidates": [{"recipe_id": "r_candidate", "score": 0.83}],
      "evidence": {"page": 1, "source_bbox": [10, 10, 20, 20]}
    }
  ],
  "issues": [{"code": "UNKNOWN_SYMBOL", "col": 0, "row": 0}]
}
```

这是片段，完整草稿必须覆盖整个网格；不得因缺少 cell 就推定留空。置信分数描述识别器输出，校准前不称正确概率。`accepted_empty` 必须无配方，`accepted_recipe` 必须有可解析配方和确认来源（显式结构数据或人工决定）。

无冲突且来源 profile 已验证的结构化全针数据可直接产生 accepted 状态；未知或互相矛盾的输入不得自动确认。所有格／图例／对象问题解决后，转换为 PatternDocument 并执行完整语义校验。

对不支持的非全针内容，可以保留原文件并中止，或用户确认丢弃后产生明确的派生全针设计。必须记录 discarded 对象和损失，不能称为无损恢复。没有针脚语义损失的导入仍需记录忽略的工程注释或完成进度等信息。

## 7. 工程容器、哈希与迁移

### 7.1 容器清单

```text
container_version: 1
document_path: pattern.json
document_schema_version: 0.1.0
files: [{path, sha256, byte_length, media_type, role}]
capabilities: [full_cross]
source_included: true / false
```

路径是相对包根的 POSIX 路径，不允许逃出包目录。manifest 不列自身哈希，列出包内所有其他文件，未登记额外文件拒绝导入。初始限额：压缩包 100 MiB、解压总量 500 MiB、文件 1,000 个、单个 pattern.json 32 MiB；超限先拒绝并说明，不能加载后才限制内存。

来源图像可选，针脚、配方和必要材料必备。引用到的来源说明随 provenance 保存；需要离线完整审计的测量报告也应包含在包中。包内预览只是派生产物，不能反向替代 pattern.json。

### 7.2 哈希口径

区分两种哈希：`artifact_sha256` 是实际文件字节的 SHA-256；`document_hash` 是规范序列化 PatternDocument 的 SHA-256。重新压缩会改变 ZIP 字节哈希，不改变文档哈希。

v0.1 的核心文档数据使用整数、布尔、null 与字符串，避免物理尺寸浮点进入主数据。规范化规则如下：

1. UTF-8，无 BOM，无末尾换行或无意义空格；对象键按 Unicode 码点排序。
2. 字符串使用 JSON 转义；非 ASCII 字符直接写 UTF-8，不转义 `/`；拒绝非法代理字符。
3. 整数用十进制最短表示；拒绝 NaN／Infinity；扩展注释也不得使用浮点，浮点测量数据放独立有哈希产物。
4. 对象键重复拒绝；字符串不自动做 NFC／大小写转换，以保留输入含义。
5. 数组先规范排序：materials 按 id；recipes 按 index；regions 按 id，spans 按 row／start_col；legend 按 recipe_id；sources 按 id；纯集合型来源哈希数组按字典序。针格数组顺序绝对不能排序。
6. 引用关系及同义配方去重先完成，再生成规范字节；引擎是权威规范化方，前端显示服务端哈希。

API 与数据 Schema 类型生成不允许产生另一套哈希实现而无跨语言黄金样例。计算参数如色差权重允许在独立 GenerationConfig 中使用数值，缓存规范化统一由 Python 执行，并记录序列化版本；不能直接把字符串拼接后哈希，必须用有字段的对象消除边界歧义。

### 7.3 Schema 迁移

读取顺序：容器安全检查→精确 schema 校验→跨字段语义校验→能力检查→可选迁移。迁移不得覆盖原文件，输出新文档和迁移报告，记录旧／新哈希、迁移器版本及字段变化。

缺字段没有可靠来源时不能补造股数、色库或实际验证状态。需要用户选择的迁移输出待确认草稿。未知核心版本只保留预览及原包，不提供保存回去的编辑操作。扩展命名空间只能表达非语义注释，忽略后不得改变针脚含义；资源限制仍然适用。

## 8. 必须实现的语义规则

| ID | 约束 | 草稿／发布行为 |
|---|---|---|
| `GRID_SHAPE` | cells 长度等于 width×height，总格数≤90,000 | 结构不成立，拒绝作为 PatternDocument 保存 |
| `UNIQUE_IDS` | 每类 ID、配方 index 均唯一，规范组成不重复 | 拒绝；导入可先显式规范化 |
| `RECIPE_EXISTS` | 每个非 0 索引都有配方，每个组成都有材料 | 拒绝 |
| `MATERIAL_SOURCE_EXISTS` | 每个非空 source_ref 在 sources 中存在 | 拒绝；未证明测量则不许 measured |
| `MATERIAL_COLOR_STATE` | unknown⇔色值为 null；其他状态有色值和来源 | 拒绝不一致 |
| `RECIPE_STRANDS` | MVP 一个组成，股数 1–6 | 拒绝；混线去 ImportDraft |
| `PALETTE_BUDGET` | 实际使用配方数≤constraints.color_budget≤4096 | 图片自动配色预算仍为 1–64；已有图纸导入与编辑保留完整线色 |
| `LEGEND_COMPLETE` | 使用配方恰好一个 symbol；所有条目引用存在 | 拒绝缺失／重复映射 |
| `REGION_BOUNDS` | 区间在对应网格内，排序且无重叠冗余 | 规范化后重检，越界拒绝 |
| `CAPABILITY_SUPPORTED` | 只含声明且已实现的 full_cross 数据 | 拒绝不支持的核心能力 |
| `NO_UNRESOLVED` | PatternDocument 无 ImportDraft 未知项 | 只能保存识别草稿 |
| `LOCKED_CONTENT_UNCHANGED` | 对比旧／新版本完整锁定语义 | 阻止命令提交 |
| `NONEMPTY_RELEASE` | 发布版本至少一针 | 空图可以保存，默认不能发布 |
| `PRODUCTION_MATERIALS` | 正式交付不含 test_fixture／synthetic；所需显示色可用 | 测试出口可带“测试样例”标记，正式出口阻止 |
| `SYMBOL_PROFILE_COMPLETE` | 所选符号有唯一字形映射和可用字体 | 阻止对应图纸出口 |
| `EXPORT_SEMANTICS` | 没有未确认或不可允许的损失 | 阻止对应出口，不影响草稿 |
| `RELEASE_CONSISTENCY` | 全部产物绑定同一文档哈希及配置 | 不可 READY |

报告字段：`rule_id,severity,scope,paths,object_ids,message,suggested_action,rule_version`，可定位问题格或 JSON 字段。硬规则不可被软评分抵消。

## 9. 最小可核对样例

[minimal-pattern.json](../examples/minimal-pattern.json) 为 4×3 非对称测试设计，黑色配方 1、白色配方 2，0 为留空：

```text
1 0 2 0
0 1 2 0
2 0 0 1
```

| 项目 | 手工期望 |
|---|---|
| 总格数／全针／留空 | 12／6／6 |
| 配方 1／配方 2 针数 | 3／3 |
| 使用配方／材料 | 2／2 |
| 占用边界 | [0,0,4,3) |
| 4 邻域孤立针 | 4；配方 1 的三针，加配方 2 位于 (0,2) 的一针 |
| ≤3 针小连通域占比 | 1.0，作为指标演示，不代表应全部删除 |
| 14 CT 画布尺寸 | 7.257142857…×5.442857142… mm |
| 每边留 50 mm 裁布 | 107.257142857…×105.442857142… mm |
| 中心（格边界坐标） | (2,1.5) |
| 锁定区域 | (0,0) 一格；改变其配方、股数或方向均应阻断 |

样例色值为人为设定的纯黑和纯白，品牌为 TEST，没有 DMC 线号或实体校准声明。它用于验证结构和语义，不作为真实材料库或试绣证据。

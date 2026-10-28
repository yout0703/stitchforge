# XSD 导入与导出

网页「导入 OXS / XSD」按文件后缀选择解析器，导入后新建独立图纸，进入查看模式。图纸库标记「XSD 导入」，右侧色库来源显示「XSD 内置线色」。导入、查看、编辑和下载均免费，账号没有生成次数也可使用。

「下载图纸 → 下载 XSD」先保存当前修改，再导出指定版本。保存冲突或失败时不下载旧版本。下载权限、回收站限制和限流与 OXS / PNG 一致。

## 当前兼容范围

- 格式是 **Pattern Maker v4 的二进制 XSD**，文件签名为小端 `0x0510`，与 XML Schema 无关。其他签名明确报错。
- 支持全针、留空、调色板 RGB、材料线号、全针股数、标题、作者、绣布底色与 CT。内部索引 0 表示留空；XSD 的 0 号线色仍为可绣颜色。
- 图纸每边 1–600 格；CT 为经纬相同的 6–40 整数；股数 1–6；上传最大 16 MiB。Pattern Maker 调色板最多 240 项，导入时保留全部线色，不受图片自动配色的 64 色预算限制。PatternDocument 最多允许 4096 个配方。
- 半针、小十字、四分之一针、回针、直线、结粒、珠绣、混线、特殊针法模型及未知针格类型阻止导入，返回定位报告，不会静默丢弃。
- 非 Aida 绣布或不一致的经纬 CT 阻止导入。原始文件按 SHA-256 保存为账号私有素材，失败的导入不创建图纸。
- 导入会将针方向设为 `slash`、裁布边距设为 50 mm，重新分配工作台符号；颜色标记为内嵌屏幕近似色。公司、版权、说明和绣布名称保存在 `stitchforge.xsd_import` 注释中。

## 文字与转换损失

UTF-8 自动识别。旧版 Windows 文件可在选择 XSD 后切换文字编码：俄文 `cp1251`（默认）、简体中文 `gb18030`、西欧文字 `cp1252`；编码只影响文字，不改变针格。乱码时切换后重新导入。

导出文字使用 UTF-8。原生字段的容量为：标题和作者各 40 字节，线号 10 字节，线色名称 40 字节；超过时按完整字符截短。完整品牌、线号、线色名称额外写入标准线色备注，StitchForge 再导入时恢复；其他软件未必读取这些备注。常见品牌写入 Pattern Maker 品牌标识；未知品牌写入 Custom Thread，不能冒充 DMC。未识别的原生品牌保留其数字 ID。

XSD 不能完整保存 StitchForge 的历史、来源、区域锁定、裁布边距和针方向；当前导出也不保留原文件的排版、打印配置、公司、版权和说明注释。网页下载处展示格式边界；CLI 默认只报告损失，传入 `--confirm-loss` 后写文件。阻止导出时不会覆盖目标文件。

前 64 个工作台符号保持原映射；第 65 色起使用唯一的两字符标记（AA、AB……），网页与 PNG / PDF 一致。PNG 自适应标记宽度，PDF 图例按页续排，避免多色图例越过页面。

## 命令行与 API

```bash
uv run stitchforge import-xsd chart.xsd \
  --output output/imported.json \
  --report output/xsd-import-report.json \
  --legacy-encoding gb18030

uv run stitchforge export-xsd output/imported.json \
  --output output/chart.xsd \
  --report output/xsd-export-report.json \
  --confirm-loss
```

- `POST /api/v1/projects/import-xsd?legacy_encoding=cp1251`：请求体为原始文件字节，返回项目、初始版本、导入报告和原始文件 SHA-256。要求登录和 `X-StitchForge-Request: 1`。
- `GET /api/v1/projects/{project_id}/revisions/{revision_id}/download/xsd`：返回 `application/octet-stream` 附件，使用 UTF-8 文件名和 `Cache-Control: no-store`。

## 验证与限制

2026-10-27 已通过独立构造的非零密钥二进制样例、非对称坐标与留空、不同股数、稀疏内部索引、中文与旧编码、600×600 压缩长段、空调色板、截断文件、超大文件、恶意压缩计数、未知针法／线色索引、账号隔离、免费导入和指定版本下载测试。

格式布局参照 [Embroiderly 的 Pattern Maker 解析器](https://github.com/embroidery-space/embroiderly/blob/12aff970bbd3744faceb68ee11d02252d51de808/crates/embroiderly-parsers/src/pmaker/xsd.rs)，项目运行时不依赖它。导出的 4×3 非对称样例已由其 v0.8.0 独立解析，核对尺寸、CT、底色、标题、线号以及全部 6 个全针坐标。第三方复杂图纸样例验证为明确阻止导入。**尚未在 Pattern Maker 或 Cross Stitch Saga 实机打开验证；不承诺所有 XSD 版本或目标软件都兼容。**

```bash
uv run pytest tests/test_xsd.py tests/test_downloads.py
# 前端 pnpm dev 启动后，隔离临时数据测试上传、编辑、自动保存、下载、图纸库和移动端：
uv run --with playwright python tools/check_web_xsd.py
# 也可传入自己的文件；在临时账号中验证，不修改已有图纸：
uv run --with playwright python tools/check_web_xsd.py /path/to/chart.xsd
```

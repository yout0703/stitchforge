# 材料目录

`dmc-117-2021-quiltysolid-2025-07.json` 是 StitchForge 的首个可运行 DMC 纯色目录：

- DMC 官方 2021 Article 117 Numerical Listing 提供 489 个线号身份和辅助名称；
- QuiltySolid 2025-07 MIT 数据提供其中 454 个近似屏幕 sRGB；
- 01–35 保存为 `unknown`，不会进入自动匹配；
- 目录中的 `default_strands=2` 只是旧调用方的回退值，不是 CT 对应的行业规则；
- 该目录不代表 DMC 官方数字色值或实体绣样测量结果。

重新生成：

```bash
uv run python tools/build_dmc_catalog.py \
  --official-pdf path/to/DMC_Embroidery_Floss_Numerical_List_2021.pdf \
  --quilty-json path/to/fabricSwatches.json \
  --output catalogs/dmc-117-2021-quiltysolid-2025-07.json
```

来源、哈希和许可状态同时写入目录的 `source` 字段。研究结论见
[`docs/references/dmc-fidelity-research-2026-09-12.md`](../docs/references/dmc-fidelity-research-2026-09-12.md)。

# DMC 色库、绣布密度与还原度研究

- 调研日期：2026-09-12
- 目的：为 StitchForge 自用打版工作台确定可运行的 DMC 数据基线，并区分图片针格分辨率、绣布 CT、股数和实物颜色。
- 结论状态：官方线号身份可用；屏幕色值仅为近似；实体绣样外观模型待测量。

## 1. 结论

StitchForge 的优化目标是“在用户给定的成品尺寸、针数、颜色数和材料约束内，最大程度保留输入图片的结构与感知颜色”。14CT、2 股和固定 24 色都不是产品标准，只能作为一个候选配置。

需要分别建模四件事：

1. **针格分辨率**决定能否保留轮廓、五官和细线。固定成品尺寸时，提高 CT 才会增加可用针格；固定针格时，改变 CT 只改变实物尺寸。
2. **线号目录**决定可以购买和标注哪些材料。DMC 以线号识别颜色，英文颜色名只是辅助描述。
3. **材料外观**决定颜色匹配。屏幕 RGB、单根线、整绞线和绣在不同底布上的针脚不是同一种颜色数据。
4. **股数和底布**会改变覆盖率、露底比例、明度和纹理，因此股数不能只作为材料表上的统计字段。

## 2. DMC 目录事实

DMC Mouliné Spécial Article 117 是可拆分的六股、100% 棉绣线，每绞 8 米。DMC 当前产品页以“500 shades”描述系列，但同一官方站点的产品、印刷色卡和实体线样色卡存在 485、489、500 等不同数量口径。因此引擎不能把“DMC 永远有 N 色”写死，必须保存目录版本和采集日期。

DMC 2021 年 1 月发布的 Article 117 Numerical Listing 包含：

- 489 个纯色身份，包括 `01`–`35`、数字色号、`B5200`、`ECRU` 和 `BLANC`；
- 18 个渐变色，单独列出；
- 明确说明 DMC 按编号引用颜色，列表中的名称可能与图纸作者使用的名称不同。

本项目首个目录因此保存 489 个纯色身份。渐变色不能用单个 RGB 和独立针格表达，在建立线段方向、起点和连续色变模型前不进入自动配色。

参考项目 `amazing-images-creator` 的缓存有 462 条，其中只有 461 条属于上述 489 个官方纯色身份；它缺少 28 个官方色号，并额外包含 `WHITE`。该缓存不能直接作为 StitchForge 的目录权威。

## 3. 当前可运行色库

首版目录位于 `catalogs/dmc-117-2021-quiltysolid-2025-07.json`：

- 489 个身份和名称来自 DMC 2021 官方 Numerical Listing；
- 454 个旧系列纯色具有 QuiltySolid 2025-07 数据中的近似 sRGB；
- `01`–`35` 暂无可信开放色值，以 `color_status=unknown` 保存，不参加自动匹配；
- QuiltySolid 的 `WHITE` 在导入时规范成 DMC 官方身份 `BLANC`；
- 所有现有 RGB 均标记为 `approximate`，不声称是实体测量；
- DMC 310 是正常候选色，不设置全局禁黑规则。

QuiltySolid 下载页把完整 JSON 标为 MIT License。生成脚本会同时检查官方 489 条、近似 454 条、重复线号和来源文件哈希，避免上游静默变化。

## 4. CT、尺寸和分辨率

DMC 对 Aida 的定义是每英寸可绣的针数；count 越高，单针越小。DMC 建议初学者使用 14 count，但这只是易用性建议。DMC 自己的照片转图服务允许选择 14、16、18 count Aida，以及 28、32 count linen。

对 Aida over-one：

```text
width_cells  = floor(width_mm / 25.4 × CT)
height_cells = floor(height_mm / 25.4 × CT)
```

因此图像还原优先的流程应先让用户给出成品包围框和可接受针数，再列出多个 CT/针格组合。不能先固定 160 格，再让 CT 假装提高细节。

引擎第一版将 Aida 的结构范围放宽为 6–40 CT，并提供物理尺寸规划器。界面推荐值仍应来自实际可以买到的布料目录。Linen/evenweave 的 over-two 语义与 Aida 不同，等数据模型扩展后再开放。

## 5. 股数和实物颜色

DMC 只说明六股线可以拆分，并通过改变股数得到不同深度和细节；它没有给出适用于所有 CT、布料、张力和目标效果的统一股数表。因此 StitchForge 不把“14CT=2 股”当作硬规则。

当前版本允许用户为一次转换明确选择 1–6 股。下一版材料外观模型需要保存：

```text
AppearanceProfile =
  thread identity
  + fabric identity/color/count
  + strands
  + stitch geometry/top-leg direction
  + lighting/observer/measurement method
  + measured Lab or spectral reflectance
  + coverage ratio and uncertainty
```

在得到实体样片前，配色仍按线的近似 sRGB 计算，同时把股数标为“未参与颜色预测”。得到样片后，候选颜色应使用“绣好后的针格外观”匹配，而不是线束本身颜色。薄覆盖可先在校准后的线性光或反射率域模拟线色与布色的光学混合，再用实测样片修正。

## 6. 还原度优化策略

还原优先候选按以下顺序增加成本：

1. 在成品尺寸和总针格上限内选择更高分辨率；
2. 保留主体和关键区域，对眼睛、文字、轮廓等提高空间误差权重；
3. 直接从实际可用 DMC 材料集合选择颜色，生成颜色数与误差的收益曲线；
4. 用 CIEDE2000 作为当前小色差基线，同时分别报告亮度、色度和结构误差；
5. 空间去碎针只能在总误差改善且不损伤关键区域时执行；
6. 实体外观配置可用后，将底布、CT、股数纳入候选评分；
7. 最终由原图、数字针格预览和代表区域实绣三者共同验收。

CIEDE2000 是 CIE 标准化的感知色差公式，适合做当前确定性基线，但它不能补偿错误的材料测量，也不能单独判断边缘、纹理和语义细节是否被保留。

## 7. 资料

- [DMC Six-Strand Embroidery Floss](https://www.dmc.com/US/en/products/mouline-special)
- [DMC Article 117 Numerical Listing, January 2021](https://www.dmc.com/media/color_charts/DMC_Embroidery_Floss_Numerical_List_2021.pdf)
- [DMC Printed Color Card](https://www.dmc.com/US/en/products/printed-color-card)
- [DMC Threaded Color Card](https://www.dmc.com/US/en/products/floss-color-card-mouline-pearl-cotton)
- [DMC: What Fabric Should You Use for Cross Stitch?](https://www.dmc.com/GB/en-GB/technique-cross-stitch-what-fabric)
- [DMC: How to Use Stranded Embroidery Thread](https://www.dmc.com/US/en/technique-embroidery-use-stranded-thread)
- [DMC Stitch Your Photo](https://www.dmc.com/FI/en-GB/stitch-your-photos)
- [QuiltySolid open JSON](https://www.quiltysolid.com/)
- [ISO/CIE 11664-6:2022 CIEDE2000](https://www.cie.co.at/publications/colorimetry-part-6-ciede2000-colour-difference-formula-1)

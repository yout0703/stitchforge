# Design QA — 看图模式四边坐标

## Evidence

- Source visual truth: `/var/folders/jq/q6dqh9452811qbhd87pd31w40000gn/T/codex-clipboard-0e16c117-f1a3-4ef2-aca7-7560b9737cdd.png`
- Source pixels: 1882 × 2294; conversation preview normalized to 1440 × 1755.
- Implementation: `http://127.0.0.1:5173/#pattern`
- Browser-rendered capture: Codex in-app browser tab 2, 1233 × 1314 JPEG capture retained in the active browser evidence for this task.
- CSS viewport / density: browser-native viewport, no temporary viewport override; screenshot density 1:1 with the captured browser surface.
- State: 看图模式；64 × 64 空白图纸；“适应窗口”状态。另检查了高倍放大与低于可读阈值的极小缩放状态。

## Full-view comparison evidence

The source annotation reserves the top and left margins around the chart for coordinates. The implementation extends this intent to all four sides: column labels sit above and below the chart, row labels sit left and right, and the labels follow the chart while panning and zooming. At fit-to-window size the labels remain outside the stitch area, with a small translucent neutral plate for contrast.

The source is an annotated request rather than a pixel-exact mock. Therefore the comparison evaluates the requested placement, clarity, density, and interaction behavior instead of treating the red markup as production styling.

## Focused-region comparison evidence

- Top edge: source asks for column coordinates in the marked horizontal space; implementation shows `列 1`, `列 10` … aligned to cell centers.
- Left edge: source asks for row coordinates in the marked vertical space; implementation shows `行 1`, `行 10` … aligned to row centers.
- Opposite edges: implementation mirrors the same coordinates on bottom and right so the user does not need to trace across a large pattern.
- Zoom behavior: high zoom increases label density down to every visible row/column; below 4.5 px per cell all labels disappear, preventing illegible clutter.

## Required fidelity surfaces

- Fonts and typography: uses the product’s existing system UI stack at 11 px / 600; short Chinese labels remain legible without dominating the chart.
- Spacing and layout rhythm: labels are offset from chart edges and clamped inside the viewport; four sides remain aligned during pan and zoom.
- Colors and visual tokens: slate text, subtle border, and translucent neutral background match the existing workbench palette and preserve chart contrast.
- Image quality and asset fidelity: no raster assets are introduced; coordinates are native canvas text rendered at device pixel ratio, so they remain sharp while moving and zooming.
- Copy and content: one-based `列 N` / `行 N` language directly matches the user’s mental model and the existing right-side coordinate wording.

## Findings

No actionable P0/P1/P2 visual mismatch remains.

## Comparison history

1. First implementation pass: coordinate placement and adaptive density passed, but extreme zoom-out exposed a P1 interaction issue—the existing “适应窗口” action did not reliably restore the canvas.
2. Fix: replaced remount-dependent fitting with an explicit `fitRequest` passed into `PatternCanvas` and a deterministic viewport reset.
3. Post-fix evidence: after minimum zoom, “适应窗口” restores the complete 64 × 64 chart centered in the canvas with labels visible on all four sides. A full reload produced no new console errors or warnings.

## Implementation checklist

- [x] 四边显示一基坐标。
- [x] 根据缩放自动调整 1 / 2 / 5 / 10 / 20 / 50… 格标注间隔。
- [x] 针格小于 4.5 px 时隐藏坐标。
- [x] 平移、滚轮缩放和适应窗口后保持对齐。
- [x] 编辑模式不显示坐标。
- [x] 通过单元测试、类型检查和生产构建。

## Follow-up polish

No blocking polish remains. A future preference could allow advanced users to choose “仅上左” or “四边” rulers, but this is not needed for the current reader-first flow.

final result: passed

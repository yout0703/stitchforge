import type { Material, PatternDocument, Recipe } from './types'

const MATERIALS: Material[] = [
  { id: 'dmc_310', code: '310', name: 'Black', color: { display_srgb: '#000000' } },
  { id: 'dmc_321', code: '321', name: 'Red', color: { display_srgb: '#C72B3B' } },
  { id: 'dmc_666', code: '666', name: 'Bright Red', color: { display_srgb: '#E31D42' } },
  { id: 'dmc_742', code: '742', name: 'Light Tangerine', color: { display_srgb: '#FFBF57' } },
  { id: 'dmc_904', code: '904', name: 'Very Dark Parrot Green', color: { display_srgb: '#557822' } },
  { id: 'dmc_blanc', code: 'BLANC', name: 'White', color: { display_srgb: '#FCFBF8' } },
]

const RECIPES: Recipe[] = MATERIALS.map((material, index) => ({
  id: `recipe_${material.code.toLowerCase()}`,
  index: index + 1,
  components: [{ material_id: material.id, strands: 2 }],
}))

export function makeDemoPattern(): PatternDocument {
  const width = 48
  const height = 32
  const cells = Array<number>(width * height).fill(0)
  for (let row = 0; row < height; row += 1) {
    for (let col = 0; col < width; col += 1) {
      const x = col - 24
      const y = row - 15
      const distance = Math.hypot(x, y)
      let recipe = 0
      if (distance < 3.5) recipe = 4
      else if (
        Math.hypot(x - 7, y) < 6 ||
        Math.hypot(x + 7, y) < 6 ||
        Math.hypot(x, y - 7) < 6 ||
        Math.hypot(x, y + 7) < 6
      ) recipe = (col + row) % 4 === 0 ? 2 : 3
      else if (Math.abs(x) < 2 && y > 7) recipe = 5
      else if (y > 13 && Math.abs(x + (y - 13) * 0.7) < 3) recipe = 5
      else if (y > 13 && Math.abs(x - (y - 13) * 0.7) < 3) recipe = 5
      if (recipe > 0 && (col * 7 + row * 11) % 13 === 0) recipe = 6
      cells[row * width + col] = recipe
    }
  }
  return {
    schema_version: '0.1.0',
    metadata: { title: '花朵练习图' },
    grid: { width, height },
    fabric: { count: 14, display_srgb: '#E3E6E8' },
    materials: MATERIALS,
    recipes: RECIPES,
    full_stitches: { cells, top_leg: 'slash' },
  }
}

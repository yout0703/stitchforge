import type { PatternDocument } from './types'

// Keep s001–s064 stable; additional colours use unique two-character ASCII labels.
// Same s001–s4096 mapping as stitchforge/symbols.py and exported charts.
const SYMBOLS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz!?'
function symbolAt(position: number): string {
  if (!Number.isInteger(position) || position < 0 || position >= 4096) return '?'
  if (position < SYMBOLS.length) return SYMBOLS[position]
  const pair = position - SYMBOLS.length
  return SYMBOLS[Math.floor(pair / SYMBOLS.length)] + SYMBOLS[pair % SYMBOLS.length]
}
export function recipeSymbols(document: Pick<PatternDocument, 'recipes' | 'legend'>): Map<number, string> {
  const legend = new Map(document.legend?.map((entry) => [entry.recipe_id, entry.symbol_id]) ?? [])
  return new Map(document.recipes.map((recipe) => {
    const id = legend.get(recipe.id)
    const position = id && /^s\d+$/.test(id) ? Number(id.slice(1)) - 1 : recipe.index - 1
    return [recipe.index, symbolAt(position)]
  }))
}

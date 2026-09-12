import type { PatternDocument } from './types'

// Same s001–s064 mapping as stitchforge/symbols.py and exported charts.
const SYMBOLS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz!?'
export function recipeSymbols(document: PatternDocument): Map<number, string> {
  const legend = new Map(document.legend?.map((entry) => [entry.recipe_id, entry.symbol_id]) ?? [])
  return new Map(document.recipes.map((recipe) => {
    const id = legend.get(recipe.id)
    const position = id && /^s\d+$/.test(id) ? Number(id.slice(1)) - 1 : recipe.index - 1
    return [recipe.index, SYMBOLS[position] ?? '?']
  }))
}

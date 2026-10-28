import { expect, it } from 'vitest'
import { recipeSymbols } from './symbols'
import { makeDemoPattern } from './demoPattern'

it('uses assigned legend IDs rather than palette position, with the export symbol order', () => {
  const document = makeDemoPattern()
  document.legend = [
    { recipe_id: document.recipes[0].id, symbol_id: 's027' },
    { recipe_id: document.recipes[1].id, symbol_id: 's037' },
    { recipe_id: document.recipes[2].id, symbol_id: 's064' },
  ]
  const symbols = recipeSymbols(document)
  expect(symbols.get(1)).toBe('0')
  expect(symbols.get(2)).toBe('a')
  expect(symbols.get(3)).toBe('?')
})

it('maps colours above 64 to unique printable labels through the document limit', () => {
  const document = {
    recipes: Array.from({ length: 4096 }, (_, i) => ({ id: `r${i}`, index: i + 1, components: [] })),
    legend: Array.from({ length: 4096 }, (_, i) => ({ recipe_id: `r${i}`, symbol_id: `s${String(i + 1).padStart(3, '0')}` })),
  }
  const symbols = recipeSymbols(document)
  expect(symbols.size).toBe(4096)
  expect(new Set(symbols.values()).size).toBe(4096)
  expect(symbols.get(64)).toBe('?')
  expect(symbols.get(65)).toBe('AA')
  expect(symbols.get(77)).toBe('AM')
  expect([...symbols.values()].every(symbol => /^[A-Za-z0-9!?]{1,2}$/.test(symbol))).toBe(true)
})

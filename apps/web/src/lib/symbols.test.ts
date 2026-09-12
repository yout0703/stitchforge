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

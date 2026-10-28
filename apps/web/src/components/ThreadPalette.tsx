import { useState } from 'react'
import { MagnifyingGlassIcon, XIcon } from '@phosphor-icons/react'
import type { Material, Recipe } from '../lib/types'
import { SelectControl } from './SelectControl'

interface Props {
  palette: (Recipe & { material?: Material })[]
  counts: Map<number, number>
  symbols: Map<number, string>
  selected: number | null
  showSymbols: boolean
  editing: boolean
  onSelect: (index: number) => void
}

export function ThreadPalette({ palette, counts, symbols, selected, showSymbols, editing, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('original')
  const search = query.trim().toLocaleLowerCase()
  const visible = palette.filter(recipe =>
    `${recipe.material?.brand ?? ''} ${recipe.material?.code ?? ''} ${recipe.material?.name ?? ''} ${symbols.get(recipe.index) ?? ''}`
      .toLocaleLowerCase().includes(search),
  )
  if (sort === 'count') visible.sort((a, b) => (counts.get(b.index) ?? 0) - (counts.get(a.index) ?? 0))

  return <section className="inspector-section thread-palette" aria-label="绣线色表">
    <div className="section-heading"><h2>线色</h2><span>{palette.length} 种颜色</span></div>
    <div className="thread-search">
      <MagnifyingGlassIcon size={16} aria-hidden="true" />
      <input type="search" aria-label="搜索线色" placeholder="搜索色号、颜色或符号" value={query} onChange={event => setQuery(event.target.value)} />
      {query && <button type="button" aria-label="清空线色搜索" onClick={() => setQuery('')}><XIcon size={14} aria-hidden="true" /></button>}
    </div>
    <div className="thread-list-controls">
      <span role="status">{query ? `找到 ${visible.length} 种线色` : editing ? '选择颜色后绘制' : '选择线色，高亮同色针格'}</span>
      <SelectControl label="线色排序" value={sort} onChange={setSort}>
        <option value="original">原始顺序</option><option value="count">针数从多到少</option>
      </SelectControl>
    </div>
    <div className="palette-list">
      {visible.map(recipe => <button key={recipe.id} type="button"
        className={selected === recipe.index ? 'palette-row active' : 'palette-row'}
        data-recipe-index={recipe.index} aria-pressed={selected === recipe.index}
        onClick={() => onSelect(recipe.index)}>
        <span className="thread-swatch" style={{ background: recipe.material?.color.display_srgb ?? 'var(--sf-ink-300)' }} />
        <span className="thread-name">
          <strong>{showSymbols && <span className="recipe-symbol">{symbols.get(recipe.index)}</span>}{recipe.material?.brand ?? 'DMC'} {recipe.material?.code ?? '未知'}</strong>
          <small>{recipe.material?.name ?? '未分配材料'}</small>
        </span>
        <span className="thread-quantity"><strong>{(counts.get(recipe.index) ?? 0).toLocaleString()}<small> 针</small></strong><small>{recipe.components[0]?.strands ?? 2} 股</small></span>
      </button>)}
    </div>
    {!visible.length && <div className="thread-search-empty"><p>没有找到匹配的线色</p><button className="quiet-button" type="button" onClick={() => setQuery('')}>显示全部线色</button></div>}
  </section>
}

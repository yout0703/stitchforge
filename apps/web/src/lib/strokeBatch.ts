export class StrokeBatch {
  private cells = new Set<number>()
  private recipe = 0
  private frame: number | null = null
  private paint: (indices: number[], recipe: number) => void
  private schedule: (callback: () => void) => number
  private cancel: (frame: number) => void
  constructor(
    paint: (indices: number[], recipe: number) => void,
    schedule: (callback: () => void) => number = (callback) => requestAnimationFrame(callback),
    cancel: (frame: number) => void = (frame) => cancelAnimationFrame(frame),
  ) { this.paint = paint; this.schedule = schedule; this.cancel = cancel }

  add(indices: readonly number[], recipe: number) {
    if (this.cells.size && this.recipe !== recipe) this.flush()
    this.recipe = recipe
    for (const index of indices) this.cells.add(index)
    if (this.frame === null) this.frame = this.schedule(() => this.flush())
  }

  flush() {
    if (this.frame !== null) this.cancel(this.frame)
    this.frame = null
    const indices = [...this.cells]
    this.cells.clear()
    if (indices.length) this.paint(indices, this.recipe)
  }

  discard() {
    if (this.frame !== null) this.cancel(this.frame)
    this.frame = null
    this.cells.clear()
  }
}

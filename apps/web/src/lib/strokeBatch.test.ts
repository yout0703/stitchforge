import { expect, it, vi } from 'vitest'
import { StrokeBatch } from './strokeBatch'

it('merges pointer updates into one frame and flushes the last segment on pointer-up', () => {
  const paint = vi.fn()
  const schedule = vi.fn(() => 1)
  const cancel = vi.fn()
  const batch = new StrokeBatch(paint, schedule, cancel)
  batch.add([1, 2], 3)
  batch.add([2, 3], 3)
  expect(schedule).toHaveBeenCalledTimes(1)
  expect(paint).not.toHaveBeenCalled()
  batch.flush()
  expect(paint).toHaveBeenCalledExactlyOnceWith([1, 2, 3], 3)
  expect(cancel).toHaveBeenCalledWith(1)
})

it('does not leak queued cells into another project after disposal', () => {
  const paint = vi.fn()
  let frame = () => {}
  const batch = new StrokeBatch(paint, (callback: () => void) => { frame = callback; return 1 }, () => {})
  batch.add([5], 2)
  batch.discard()
  frame()
  expect(paint).not.toHaveBeenCalled()
})

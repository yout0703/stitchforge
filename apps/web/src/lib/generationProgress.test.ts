import { describe, expect, it } from 'vitest'
import { formatElapsed, generationProgress } from './generationProgress'

describe('generationProgress', () => {
  it('stays quiet when no job is running', () => {
    expect(generationProgress({}).status).toBe('')
  })

  it('treats a pending job without a stage as queued', () => {
    expect(generationProgress({ pending: true }).status).toBe('检查图片…')
  })

  it('marks upload as current while the file is sending', () => {
    const view = generationProgress({ uploading: true, elapsedMs: 1200 })
    expect(view.steps[0]).toMatchObject({ id: 'upload', state: 'current' })
    expect(view.steps[1].state).toBe('pending')
    expect(view.status).toBe('上传图片…')
  })

  it('keeps later steps pending and completed steps done', () => {
    const view = generationProgress({ stage: 'quality', elapsedMs: 9000 })
    expect(view.steps.map((step) => step.state)).toEqual([
      'done', 'done', 'done', 'done', 'done', 'current', 'pending',
    ])
    expect(view.status).toBe('整理图纸…')
    expect(view.hint).toContain('不是卡住')
  })

  it('explains a long wait even before the quality step', () => {
    const view = generationProgress({ stage: 'palette', elapsedMs: 46_000 })
    expect(view.hint).toContain('放到后台')
  })

  it('finishes every step when the job succeeded', () => {
    const view = generationProgress({ jobState: 'SUCCEEDED', stage: 'completed' })
    expect(view.steps.every((step) => step.state === 'done')).toBe(true)
    expect(view.status).toBe('图纸已生成')
    expect(view.hint).toBeNull()
  })
})

describe('formatElapsed', () => {
  it('formats minutes and seconds', () => {
    expect(formatElapsed(0)).toBe('0:00')
    expect(formatElapsed(4500)).toBe('0:04')
    expect(formatElapsed(76_000)).toBe('1:16')
  })
})

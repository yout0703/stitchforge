export const GENERATION_STEPS = [
  { id: 'upload', label: '上传图片', stages: ['upload'] },
  { id: 'prepare', label: '检查图片', stages: ['queued', 'starting', 'resuming', 'normalization'] },
  { id: 'planning', label: '分析针格', stages: ['planning'] },
  { id: 'sampling', label: '处理底布', stages: ['sampling'] },
  { id: 'palette', label: '匹配线色', stages: ['palette'] },
  { id: 'quality', label: '整理图纸', stages: ['quality'] },
  { id: 'persisting', label: '保存图纸', stages: ['persisting', 'completed'] },
] as const

export type GenerationStepState = 'done' | 'current' | 'pending'

export interface GenerationStepView {
  id: string
  label: string
  state: GenerationStepState
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

export function generationProgress(input: {
  uploading?: boolean
  pending?: boolean
  jobState?: string | null
  stage?: string | null
  elapsedMs?: number
}): {
  steps: GenerationStepView[]
  status: string
  hint: string | null
  activeIndex: number
} {
  const elapsedMs = input.elapsedMs ?? 0
  if (!input.uploading && !input.pending && !input.jobState && !input.stage) {
    return {
      steps: GENERATION_STEPS.map((step) => ({ id: step.id, label: step.label, state: 'pending' })),
      status: '',
      hint: null,
      activeIndex: -1,
    }
  }
  if (input.jobState === 'FAILED') {
    return {
      steps: GENERATION_STEPS.map((step) => ({ id: step.id, label: step.label, state: 'pending' })),
      status: '生成未完成',
      hint: null,
      activeIndex: -1,
    }
  }
  if (input.jobState === 'CANCELLED') {
    return {
      steps: GENERATION_STEPS.map((step) => ({ id: step.id, label: step.label, state: 'pending' })),
      status: '生成已取消，本次不扣次数。',
      hint: null,
      activeIndex: -1,
    }
  }
  const stage = input.uploading
    ? 'upload'
    : input.jobState === 'SUCCEEDED'
      ? 'completed'
      : input.stage || 'queued'
  const activeIndex = GENERATION_STEPS.findIndex((step) =>
    (step.stages as readonly string[]).includes(stage),
  )
  const index = activeIndex < 0 ? 0 : activeIndex
  const steps = GENERATION_STEPS.map((step, stepIndex) => ({
    id: step.id,
    label: step.label,
    state: (input.jobState === 'SUCCEEDED'
      ? 'done'
      : stepIndex < index
        ? 'done'
        : stepIndex === index
          ? 'current'
          : 'pending') as GenerationStepState,
  }))
  const current = GENERATION_STEPS[index]
  const cancelling = input.jobState === 'CANCEL_REQUESTED'
  const status = cancelling
    ? '正在取消生成…'
    : input.jobState === 'SUCCEEDED'
      ? '图纸已生成'
      : `${current.label}…`
  let hint: string | null = null
  if (!cancelling && input.jobState !== 'SUCCEEDED') {
    if (stage === 'quality' && elapsedMs >= 8000) {
      hint = '图纸较大时，整理线色可能需要一两分钟，不是卡住。'
    } else if (elapsedMs >= 45000) {
      hint = '仍在计算。大图会更久一些，可以放到后台继续等。'
    }
  }
  return { steps, status, hint, activeIndex: input.jobState === 'SUCCEEDED' ? GENERATION_STEPS.length - 1 : index }
}

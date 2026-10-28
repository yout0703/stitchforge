import type { GenerationStepView } from '../lib/generationProgress'

export function GenerationProgress({
  status,
  error,
  hint,
  steps,
  elapsedLabel,
}: {
  status: string
  error?: string
  hint?: string | null
  steps: GenerationStepView[]
  elapsedLabel: string
}) {
  return (
    <div className="generation-progress generation-status">
      <div className="generation-progress-head">
        <p role="status">{status}</p>
        <span className="generation-elapsed">已用时 {elapsedLabel}</span>
      </div>
      <ol className="generation-steps">
        {steps.map((step) => (
          <li key={step.id} data-state={step.state}>
            <span className="generation-step-mark" aria-hidden="true" />
            <span>{step.label}</span>
          </li>
        ))}
      </ol>
      {hint && <p className="generation-progress-hint">{hint}</p>}
      {error && <p className="api-error">{error}</p>}
    </div>
  )
}

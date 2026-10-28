import type { ReactNode } from 'react'
import { CaretDownIcon } from '@phosphor-icons/react'
import './SelectControl.css'

export function SelectControl({ label, value, onChange, disabled, children }: {
  label: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  children: ReactNode
}) {
  return <span className="select-control">
    <select aria-label={label} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>{children}</select>
    <CaretDownIcon size={14} aria-hidden="true" />
  </span>
}

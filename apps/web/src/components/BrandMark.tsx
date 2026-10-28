interface Props {
  className?: string
}

export function BrandMark({ className }: Props) {
  return (
    <img
      className={['brand-mark-image', className].filter(Boolean).join(' ')}
      src="/icon-192.png"
      alt=""
      aria-hidden="true"
      draggable="false"
    />
  )
}

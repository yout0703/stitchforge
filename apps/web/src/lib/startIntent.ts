export type ImportKind = 'image' | 'oxs'

export interface StartIntent {
  kind: ImportKind | 'blank'
  file: File | null
}

export function sourceFileError(file: Pick<File, 'name' | 'size'>, kind: ImportKind): string | null {
  const chart = kind === 'oxs'
  if (!file.size) return '文件是空的，请重新选择。'
  if (!(chart ? /\.(oxs|xsd)$/i : /\.(png|jpe?g)$/i).test(file.name)) {
    return chart ? '请选择 OXS 或 XSD 图纸文件。' : '请选择 JPG 或 PNG 图片。'
  }
  if (file.size > (chart ? 16 : 20) * 1024 * 1024) {
    return `文件超过 ${chart ? 16 : 20} MiB，请选择更小的文件。`
  }
  return null
}

export function formatFileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.ceil(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

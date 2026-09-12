import type {
  ImageConversionResponse,
  PatternDocument,
  ProjectCreationResponse,
  ProjectRecord,
  RevisionRecord,
} from './types'

export class ApiError extends Error {
  readonly code: string
  readonly requestId?: string

  constructor(
    code: string,
    message: string,
    requestId?: string,
  ) {
    super(message)
    this.code = code
    this.requestId = requestId
  }
}

export async function createProject(name: string): Promise<ProjectCreationResponse> {
  const response = await fetch('/api/v1/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, fabric_count: 14, fabric_color: '#DDD5C5' }),
  })
  const body: unknown = await response.json()
  if (!response.ok) {
    const error = body as { code?: string; message?: string; request_id?: string }
    throw new ApiError(
      error.code ?? 'API_ERROR',
      error.message ?? '本机 API 请求失败',
      error.request_id,
    )
  }
  return body as ProjectCreationResponse
}

export async function createRevision(
  projectId: string,
  expectedHeadRevisionId: string,
  document: PatternDocument,
): Promise<{ revision_id: string }> {
  const response = await fetch(`/api/v1/projects/${projectId}/revisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_head_revision_id: expectedHeadRevisionId,
      document,
    }),
  })
  const body: unknown = await response.json()
  if (!response.ok) {
    const error = body as { code?: string; message?: string; request_id?: string }
    throw new ApiError(
      error.code ?? 'API_ERROR',
      error.message ?? '版本保存失败',
      error.request_id,
    )
  }
  return body as { revision_id: string }
}

export async function getProject(projectId: string): Promise<ProjectRecord> {
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}`)
  const body: unknown = await response.json()
  if (!response.ok) {
    const error = body as { code?: string; message?: string; request_id?: string }
    throw new ApiError(
      error.code ?? 'API_ERROR',
      error.message ?? '项目读取失败',
      error.request_id,
    )
  }
  return body as ProjectRecord
}

export async function getRevision(
  projectId: string,
  revisionId: string,
): Promise<RevisionRecord> {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}`,
  )
  const body: unknown = await response.json()
  if (!response.ok) {
    const error = body as { code?: string; message?: string; request_id?: string }
    throw new ApiError(
      error.code ?? 'API_ERROR',
      error.message ?? '版本读取失败',
      error.request_id,
    )
  }
  return body as RevisionRecord
}

export interface ImageConversionOptions {
  expectedHeadRevisionId: string
  gridWidth: number
  gridHeight: number
  colorBudget: number
  fabricCount: number
  strands: number
  removeBorderBackground: boolean
  optimize: boolean
  sourceProfile: 'artwork' | 'rendered_stitch'
}

export function buildImageConversionUrl(
  projectId: string,
  options: ImageConversionOptions | { expectedHeadRevisionId: string; automatic: true },
): string {
  if ('automatic' in options) {
    const query = new URLSearchParams({ expected_head_revision_id: options.expectedHeadRevisionId })
    return `/api/v1/projects/${encodeURIComponent(projectId)}/convert-image?${query}`
  }
  const query = new URLSearchParams({
    expected_head_revision_id: options.expectedHeadRevisionId,
    grid_width: String(options.gridWidth),
    grid_height: String(options.gridHeight),
    color_budget: String(options.colorBudget),
    fabric_count: String(options.fabricCount),
    strands: String(options.strands),
    remove_border_background: String(options.removeBorderBackground),
    optimize: String(options.optimize),
    source_profile: options.sourceProfile,
    palette_strategy: 'clustered',
  })
  return `/api/v1/projects/${encodeURIComponent(projectId)}/convert-image?${query}`
}

export async function convertProjectImage(
  projectId: string,
  file: File,
  options: ImageConversionOptions | { expectedHeadRevisionId: string; automatic: true },
): Promise<ImageConversionResponse> {
  const response = await fetch(buildImageConversionUrl(projectId, options), {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  })
  const body: unknown = await response.json()
  if (!response.ok) {
    const error = body as { code?: string; message?: string; request_id?: string }
    throw new ApiError(
      error.code ?? 'IMAGE_CONVERSION_ERROR',
      error.message ?? '图片转换失败',
      error.request_id,
    )
  }
  return body as ImageConversionResponse
}

export async function importOxsProject(file: File): Promise<ProjectCreationResponse & {
  report: { issues: { severity: string; message: string }[] }
}> {
  const response = await fetch('/api/v1/projects/import-oxs', {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  })
  const body = await response.json()
  if (!response.ok) throw new ApiError(body.code ?? 'OXS_IMPORT_ERROR', body.message ?? 'OXS 导入失败', body.request_id)
  return body
}

async function libraryRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/projects${path}`, options)
  const body = await response.json()
  if (!response.ok) throw new ApiError(body.code ?? 'API_ERROR', body.message ?? '图纸库请求失败', body.request_id)
  return body as T
}

export function listProjects(options: { search: string; deleted: boolean; sort: string; offset: number }) {
  const query = new URLSearchParams({ ...options, deleted: String(options.deleted), offset: String(options.offset), limit: '24' })
  return libraryRequest<{ items: import('./types').LibraryProject[]; total: number }>(`?${query}`)
}

export function renameProject(projectId: string, name: string, head: string) {
  return libraryRequest<{ revision_id: string }>(`/${encodeURIComponent(projectId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, expected_head_revision_id: head }),
  })
}

export function deleteProject(projectId: string) {
  return libraryRequest(`/${encodeURIComponent(projectId)}`, { method: 'DELETE' })
}

export function restoreProject(projectId: string) {
  return libraryRequest(`/${encodeURIComponent(projectId)}/restore`, { method: 'POST' })
}

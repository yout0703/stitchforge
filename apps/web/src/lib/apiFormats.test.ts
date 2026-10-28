import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from './account'
import { ApiError, downloadChart, importPatternProject } from './api'

vi.mock('./account', () => ({ apiFetch: vi.fn() }))
const request = vi.mocked(apiFetch)
afterEach(() => vi.resetAllMocks())

describe('pattern file routing', () => {
  it('uploads uppercase XSD files with the chosen text encoding', async () => {
    const file = new File(['binary'], '我的图纸.XSD')
    request.mockResolvedValue(Response.json({ project: { project_id: 'xsd' } }))
    const result = await importPatternProject(file, 'gb18030')
    expect(request).toHaveBeenCalledWith('/api/v1/projects/import-xsd?legacy_encoding=gb18030', {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
    })
    expect(result.project.project_id).toBe('xsd')
  })

  it('keeps OXS on its existing endpoint', async () => {
    request.mockResolvedValue(Response.json({}))
    await importPatternProject(new File(['xml'], 'pattern.OXS'))
    expect(request.mock.calls[0][0]).toBe('/api/v1/projects/import-oxs')
  })

  it('rejects other extensions without uploading and surfaces unsupported-XSD reports', async () => {
    await expect(importPatternProject(new File(['file'], 'pattern.pat'))).rejects.toThrow('OXS 或 XSD')
    expect(request).not.toHaveBeenCalled()
    request.mockResolvedValue(Response.json({ code: 'INVALID_XSD', message: '仅支持全针' }, { status: 422 }))
    await expect(importPatternProject(new File(['file'], 'pattern.xsd'))).rejects.toMatchObject({
      code: 'INVALID_XSD', message: '仅支持全针',
    } satisfies Partial<ApiError>)
  })

  it('downloads XSD bytes for the requested saved revision', async () => {
    request.mockResolvedValue(new Response(new Uint8Array([0x10, 0x05])))
    const blob = await downloadChart('my project', 'revision/2', 'xsd')
    expect(request).toHaveBeenCalledWith('/api/v1/projects/my%20project/revisions/revision%2F2/download/xsd')
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([0x10, 0x05])
  })
})

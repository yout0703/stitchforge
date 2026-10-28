import { describe, expect, it } from 'vitest'
import { sourceFileError } from './startIntent'

describe('file selection before authentication', () => {
  it('accepts uppercase legacy chart extensions and exact upload size boundaries', () => {
    expect(sourceFileError({ name: '作品.XSD', size: 16 * 1024 * 1024 }, 'oxs')).toBeNull()
    expect(sourceFileError({ name: '作品.OXS', size: 1 }, 'oxs')).toBeNull()
    expect(sourceFileError({ name: '作品.JPG', size: 20 * 1024 * 1024 }, 'image')).toBeNull()
  })
  it('rejects files from the wrong workflow and files larger than the server limit', () => {
    expect(sourceFileError({ name: '作品.png', size: 100 }, 'oxs')).toContain('OXS 或 XSD')
    expect(sourceFileError({ name: '作品.xsd', size: 100 }, 'image')).toContain('JPG 或 PNG')
    expect(sourceFileError({ name: '作品.xsd', size: 16 * 1024 * 1024 + 1 }, 'oxs')).toContain('16 MiB')
    expect(sourceFileError({ name: '作品.png', size: 20 * 1024 * 1024 + 1 }, 'image')).toContain('20 MiB')
  })
  it('rejects empty files before asking the user to log in', () => {
    expect(sourceFileError({ name: 'empty.oxs', size: 0 }, 'oxs')).toContain('空的')
  })
})

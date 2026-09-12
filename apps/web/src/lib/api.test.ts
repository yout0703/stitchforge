import { describe, expect, it } from 'vitest'
import { buildImageConversionUrl } from './api'

describe('buildImageConversionUrl', () => {
  it('encodes the complete deterministic conversion configuration', () => {
    const url = new URL(
      buildImageConversionUrl('project/a', {
        expectedHeadRevisionId: 'revision 1',
        gridWidth: 120,
        gridHeight: 180,
        colorBudget: 30,
        fabricCount: 14,
        strands: 2,
        removeBorderBackground: true,
        optimize: false,
        sourceProfile: 'rendered_stitch',
      }),
      'http://localhost',
    )

    expect(url.pathname).toBe('/api/v1/projects/project%2Fa/convert-image')
    expect(Object.fromEntries(url.searchParams)).toEqual({
      expected_head_revision_id: 'revision 1',
      grid_width: '120',
      grid_height: '180',
      color_budget: '30',
      fabric_count: '14',
      strands: '2',
      remove_border_background: 'true',
      optimize: 'false',
      source_profile: 'rendered_stitch',
      palette_strategy: 'clustered',
    })
  })
})

it('requests automatic planning without making the user supply CT or grid dimensions', () => {
  const url = new URL(buildImageConversionUrl('project1', {
    expectedHeadRevisionId: 'head1', automatic: true,
  }), 'http://localhost')
  expect(Object.fromEntries(url.searchParams)).toEqual({ expected_head_revision_id: 'head1' })
})

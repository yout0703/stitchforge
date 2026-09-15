export type Tool = 'paint' | 'pick' | 'select' | 'pan' | 'erase' | 'fill'

export interface CellSelection {
  startCol: number
  startRow: number
  endCol: number
  endRow: number
}

export interface Material {
  brand?: string
  id: string
  code: string
  name: string
  color: { display_srgb: string }
}

export interface Recipe {
  id: string
  index: number
  components: { material_id: string; strands: number }[]
}

export interface PatternDocument {
  schema_version: string
  metadata: { title: string }
  grid: { width: number; height: number }
  fabric: { count: number; display_srgb: string }
  materials: Material[]
  recipes: Recipe[]
  regions?: { id: string; name?: string; policy: 'locked' | 'preserve' | 'normal';
    spans: { row: number; start_col: number; end_col: number }[] }[]
  extensions?: Record<string, unknown>
  legend?: { recipe_id: string; symbol_id: string }[]
  full_stitches: { cells: number[]; top_leg: 'slash' | 'backslash' }
  [key: string]: unknown
}

export interface ProjectCreationResponse {
  project: { project_id: string; head_revision_id: string }
  initial_revision: { revision_id: string; document: PatternDocument }
}

export interface ProjectRecord {
  deleted_at_ms?: number | null
  project_id: string
  head_revision_id: string | null
}

export interface RevisionRecord {
  revision_id: string
  document: PatternDocument
}

export interface RareColorSummary {
  version: 1
  colors_before: number
  colors_after: number
  changed_stitches: number
  retained_colors: number
}

export interface ImageConversionResponse {
  planning_report?: {
    provider: 'codex' | 'deepseek' | 'local'
    method?: 'counted_stitches'
    structure_review?: { status: 'passed' | 'unavailable'; summary: string } | null
    model?: string
    fallback_reason: string | null
    plan: { grid_width: number; grid_height: number; fabric_count: number; strands: number; color_budget: number; explanation: string }
  } | null
  revision: { revision_id: string; document: PatternDocument }
  source_asset: { sha256: string; url: string; media_type: string }
  normalized_asset: { sha256: string; url: string; media_type: string }
  background_report: {
    background_display_srgb: string
    removed_cells: number
    retained_cells: number
  } | null
  recovery_report: {
    algorithm: string
    adjusted_colors: number
    adjusted_cells: number
  } | null
  palette_report: {
    quality: {
      weighted_mean_delta_e00: number
      p95_delta_e00: number
      selected_materials: number
    }
  }
  statistics: {
    full_stitch_count: number
    isolated_stitches_4: number
  }
  motif_resolution: {
    minimum_effective_width: number
    effective_width: number
    effective_height: number
    component_cells: number
    stitched_cells: number
    component_share: number
    bounding_box: [number, number, number, number] | null
    passes_minimum: boolean
  }
}

export interface LibraryProject extends ProjectRecord {
  name: string
  created_at_ms: number
  updated_at_ms: number
  deleted_at_ms: number | null
  width: number | null
  height: number | null
  fabric_count: number | null
  color_count: number | null
  source_kind: 'image' | 'oxs' | 'blank'
}

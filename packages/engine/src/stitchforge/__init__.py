"""StitchForge deterministic pattern engine."""

from .canonical import canonical_document_bytes, document_hash, normalize_document
from .catalog import MaterialCatalog, load_catalog
from .commands import CommandResult, apply_cell_updates, replay_patch
from .imaging import (
    GridSample,
    ImageNormalizationReport,
    ImageTransformReport,
    apply_alpha_mask,
    crop_normalized_image,
    normalize_image,
    sample_grid,
)
from .io import load_document
from .optimize import OptimizationReport, OptimizationResult, optimize_small_components
from .oxs import OxsImportResult, OxsReport, export_oxs, import_oxs
from .palette import PaletteResult, build_pattern_document, select_palette
from .stats import PatternStatistics, calculate_statistics
from .validation import ValidationReport, validate_document

__all__ = [
    "GridSample",
    "CommandResult",
    "ImageNormalizationReport",
    "ImageTransformReport",
    "MaterialCatalog",
    "OptimizationReport",
    "OptimizationResult",
    "OxsImportResult",
    "OxsReport",
    "PaletteResult",
    "PatternStatistics",
    "ValidationReport",
    "apply_alpha_mask",
    "apply_cell_updates",
    "build_pattern_document",
    "calculate_statistics",
    "canonical_document_bytes",
    "crop_normalized_image",
    "document_hash",
    "export_oxs",
    "import_oxs",
    "load_catalog",
    "load_document",
    "normalize_document",
    "normalize_image",
    "optimize_small_components",
    "replay_patch",
    "sample_grid",
    "select_palette",
    "validate_document",
]

__version__ = "0.1.0"

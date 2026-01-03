"""CLI modules for transactions categorizer."""

import json
import sys
from pathlib import Path

from loguru import logger

from src.models import CategoriesConfig

# Default categories file path (relative to package)
DEFAULT_CATEGORIES_PATH = Path(__file__).parent.parent.parent / "categories" / "default.json"


def load_categories(categories_path: Path | None, required: bool = True) -> CategoriesConfig | None:
    """Load categories from JSON file or use defaults.

    Args:
        categories_path: Path to custom categories file, or None for defaults
        required: If True, exit on missing file; if False, return None

    Returns:
        CategoriesConfig with loaded categories, or None if not required and missing
    """
    if categories_path is None:
        categories_path = DEFAULT_CATEGORIES_PATH
        if not categories_path.exists():
            if required:
                logger.error(f"Default categories file not found: {categories_path}")
                sys.exit(1)
            return None

    if not categories_path.exists():
        if required:
            logger.error(f"Categories file not found: {categories_path}")
            sys.exit(1)
        return None

    try:
        with open(categories_path) as f:
            data = json.load(f)
        return CategoriesConfig.model_validate(data)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse categories file: {e}")
        sys.exit(1)

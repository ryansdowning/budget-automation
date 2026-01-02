"""Logging configuration using loguru."""

import sys
from pathlib import Path

from loguru import logger


def configure_logging(verbose: bool = False, debug: bool = False) -> None:
    """Configure logging based on verbosity level.

    Args:
        verbose: Enable info-level logging
        debug: Enable debug-level logging (overrides verbose)
    """
    # Remove default handler
    logger.remove()

    # Determine log level
    if debug:
        level = "DEBUG"
    elif verbose:
        level = "INFO"
    else:
        level = "WARNING"

    # Add stderr handler with appropriate level
    logger.add(
        sys.stderr,
        level=level,
        format="<level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )


def get_logger(name: str | None = None):
    """Get a logger instance.

    Args:
        name: Optional name for the logger (used for context)

    Returns:
        Configured logger instance
    """
    if name:
        return logger.bind(name=name)
    return logger


class DebugArtifacts:
    """Manage debug artifact saving."""

    def __init__(self, output_dir: Path | None = None):
        self.enabled = output_dir is not None
        self.output_dir = output_dir
        if self.enabled and output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Debug artifacts will be saved to: {output_dir}")

    def save_image(self, name: str, image) -> Path | None:
        """Save a PIL image as debug artifact.

        Args:
            name: Base name for the file (without extension)
            image: PIL Image to save

        Returns:
            Path to saved file, or None if disabled
        """
        if not self.enabled or not self.output_dir:
            return None
        path = self.output_dir / f"{name}.png"
        image.save(path)
        logger.debug(f"Saved debug image: {path}")
        return path

    def save_text(self, name: str, content: str) -> Path | None:
        """Save text content as debug artifact.

        Args:
            name: Base name for the file (without extension)
            content: Text content to save

        Returns:
            Path to saved file, or None if disabled
        """
        if not self.enabled or not self.output_dir:
            return None
        path = self.output_dir / f"{name}.md"
        path.write_text(content)
        logger.debug(f"Saved debug text: {path}")
        return path

    def save_json(self, name: str, data: dict | list) -> Path | None:
        """Save JSON data as debug artifact.

        Args:
            name: Base name for the file (without extension)
            data: Data to serialize as JSON

        Returns:
            Path to saved file, or None if disabled
        """
        import json

        if not self.enabled or not self.output_dir:
            return None
        path = self.output_dir / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        logger.debug(f"Saved debug JSON: {path}")
        return path

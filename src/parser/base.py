"""Abstract base class for PDF parsers."""

from abc import ABC, abstractmethod
from pathlib import Path

from src.logging_config import DebugArtifacts
from src.models import RawTransaction


class BaseParser(ABC):
    """Abstract base class for statement parsers.

    Subclasses should implement parsing logic for specific
    credit card statement formats.
    """

    def __init__(self, debug_artifacts: DebugArtifacts | None = None):
        self.debug_artifacts = debug_artifacts or DebugArtifacts()

    @abstractmethod
    def parse(self, pdf_path: Path) -> list[RawTransaction]:
        """Extract transactions from a PDF file.

        Args:
            pdf_path: Path to the PDF statement

        Returns:
            List of extracted transactions
        """
        pass

    @abstractmethod
    def supported_formats(self) -> list[str]:
        """Return list of supported statement formats/providers.

        Returns:
            List of format identifiers (e.g., ["generic", "chase", "amex"])
        """
        pass

"""Ollama-based PDF parser using OCR for text extraction."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytesseract
from loguru import logger
from pdf2image import convert_from_path
from PIL import Image

from src.clients.ollama import OllamaClient, OllamaError
from src.logging_config import DebugArtifacts
from src.models import RawTransaction, TransactionExtractionResponse
from src.parser.base import BaseParser
from src.prompts.parse import PARSE_SYSTEM, PARSE_USER


class OllamaParser(BaseParser):
    """PDF parser using OCR + Ollama for text extraction and parsing."""

    def __init__(
        self,
        ollama_client: OllamaClient | None = None,
        debug_artifacts: DebugArtifacts | None = None,
        dpi: int = 200,
        model: str = "mistral",
        host: str = "localhost",
        port: int = 11434,
    ):
        """Initialize the Ollama parser.

        Args:
            ollama_client: Optional pre-configured Ollama client
            debug_artifacts: Optional debug artifact manager
            dpi: DPI for PDF to image conversion (higher = better OCR)
            model: Ollama model to use for parsing
            host: Ollama server host
            port: Ollama server port
        """
        super().__init__(debug_artifacts)
        self._ollama = ollama_client
        self._owns_client = ollama_client is None
        self.dpi = dpi
        self.model = model
        self.host = host
        self.port = port

    def _ensure_client(self) -> OllamaClient:
        """Get or create the Ollama client."""
        if self._ollama is None:
            self._ollama = OllamaClient(
                host=self.host,
                port=self.port,
                model=self.model,
                timeout=120.0,
            )
        return self._ollama

    def _ocr_image(self, image: Image.Image) -> str:
        """Extract text from image using OCR."""
        if image.mode != "RGB":
            image = image.convert("RGB")
        return pytesseract.image_to_string(image)

    def parse(self, pdf_path: Path) -> list[RawTransaction]:
        """Extract transactions from a PDF file.

        Args:
            pdf_path: Path to the PDF statement

        Returns:
            List of extracted transactions
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        logger.info(f"Parsing PDF: {pdf_path.name}")

        try:
            images = convert_from_path(pdf_path, dpi=self.dpi)
            logger.info(f"Converted PDF to {len(images)} page(s)")
        except Exception as e:
            logger.error(f"Failed to convert PDF to images: {e}")
            raise OllamaError(f"PDF conversion failed: {e}") from e

        all_text_parts: list[str] = []
        for i, image in enumerate(images):
            page_name = f"{pdf_path.stem}_page_{i + 1}"
            logger.debug(f"OCR page {i + 1}/{len(images)}")

            self.debug_artifacts.save_image(f"{page_name}_input", image)
            text = self._ocr_image(image)
            all_text_parts.append(f"=== PAGE {i + 1} ===\n{text}")
            self.debug_artifacts.save_text(f"{page_name}_ocr", text)

        full_text = "\n\n".join(all_text_parts)
        self.debug_artifacts.save_text(f"{pdf_path.stem}_full_ocr", full_text)
        logger.debug(f"Total OCR text length: {len(full_text)} chars")

        client = self._ensure_client()

        try:
            prompt = f"{PARSE_USER}\n\nDocument text:\n{full_text}"
            extraction = client.generate_structured(
                prompt=prompt,
                response_model=TransactionExtractionResponse,
                system=PARSE_SYSTEM,
                temperature=0.1,
            )
            self.debug_artifacts.save_json(
                f"{pdf_path.stem}_llm_response",
                extraction.model_dump(),
            )
            logger.debug(f"LLM extracted {len(extraction.transactions)} transactions")
        except OllamaError as e:
            logger.error(f"Failed to parse with LLM: {e}")
            raise

        all_transactions: list[RawTransaction] = []
        seen: set[tuple] = set()

        for tx in extraction.transactions:
            parsed = self._parse_transaction(
                {"date": tx.date, "description": tx.description, "amount": tx.amount},
                full_text,
            )
            if parsed:
                key = (parsed.date, parsed.description, parsed.amount)
                if key not in seen:
                    seen.add(key)
                    all_transactions.append(parsed)

        logger.info(f"Total transactions extracted: {len(all_transactions)}")
        self.debug_artifacts.save_json(
            f"{pdf_path.stem}_transactions",
            [tx.model_dump(mode="json") for tx in all_transactions],
        )

        return all_transactions

    def _parse_transaction(
        self,
        data: dict,
        raw_text: str = "",
        statement_year: int | None = None,
    ) -> RawTransaction | None:
        """Parse a single transaction dict into a RawTransaction."""
        try:
            date_str = str(data.get("date", "")).strip()
            if not date_str:
                return None

            if statement_year is None:
                statement_year = datetime.now().year

            parsed_date = None
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%m-%d-%Y"]:
                try:
                    parsed_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue

            # Try partial dates (MM/DD) using statement year
            if not parsed_date:
                for fmt in ["%m/%d", "%m-%d"]:
                    try:
                        partial = datetime.strptime(date_str, fmt)
                        parsed_date = partial.replace(year=statement_year).date()
                        break
                    except ValueError:
                        continue

            if not parsed_date:
                logger.warning(f"Could not parse date: {date_str}")
                return None

            description = str(
                data.get("description") or data.get("merchant") or data.get("name") or ""
            ).strip()
            if not description:
                return None

            amount_raw = data.get("amount")
            if amount_raw is None:
                return None

            try:
                if isinstance(amount_raw, str):
                    amount_raw = amount_raw.replace("$", "").replace(",", "").strip()
                amount = Decimal(str(amount_raw))
            except (InvalidOperation, ValueError) as e:
                logger.warning(f"Could not parse amount: {amount_raw}, error: {e}")
                return None

            return RawTransaction(
                date=parsed_date,
                description=description,
                amount=amount,
                raw_text=raw_text[:500] if raw_text else "",
            )

        except Exception as e:
            logger.warning(f"Failed to parse transaction: {data}, error: {e}")
            return None

    def supported_formats(self) -> list[str]:
        """Return list of supported statement formats."""
        return ["generic"]

    def close(self) -> None:
        """Clean up resources."""
        if self._owns_client and self._ollama is not None:
            self._ollama.close()
            self._ollama = None

    def __enter__(self) -> "OllamaParser":
        return self

    def __exit__(self, *args) -> None:
        self.close()

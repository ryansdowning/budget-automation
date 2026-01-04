"""PDF parser using pdfplumber for text extraction + LLM for parsing."""

import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber
from loguru import logger
from pydantic import BaseModel, Field

from src.clients.ollama import OllamaClient, OllamaError
from src.logging_config import DebugArtifacts
from src.models import RawTransaction, TransactionExtractionResponse
from src.parser.base import BaseParser
from src.prompts.parse import PARSE_SYSTEM, PARSE_USER

# Patterns that indicate a line is NOT a valid transaction
INVALID_TRANSACTION_PATTERNS = [
    r"^\+",  # Starts with + (reward point summaries)
    r"Points earned",  # Point earning summaries
    r"\bPts\b.*for\b",  # "2X Pts for..." patterns
    r"^Rewards®",  # Card name lines
    r"Credit Card$",  # Lines ending with "Credit Card"
    r"^Total (Purchases|Balance|Due|Fees|Interest|Credits)",  # Total summary lines (not "TOTAL TURF")
    r"^Balance (Forward|Transfers|Due)",  # Balance summary lines
    r"^Minimum Payment",  # Minimum payment info
    r"^Payment Due",  # Payment due info
    r"RAPID\s*(REWARDS|SMT)",  # Southwest card branding lines
]

# Compile patterns for efficiency
_INVALID_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in INVALID_TRANSACTION_PATTERNS]


def is_valid_transaction(description: str) -> bool:
    """Check if a transaction description is valid (not a statement artifact).

    Args:
        description: Transaction description to validate

    Returns:
        True if valid transaction, False if it's a statement artifact
    """
    for pattern in _INVALID_PATTERNS_COMPILED:
        if pattern.search(description):
            return False
    return True


class PageHasTransactions(BaseModel):
    """Response model for transaction table detection."""

    has_transactions: bool = Field(
        description="True if this page contains a financial transactions table/list"
    )


PAGE_CHECK_SYSTEM = """You are a document analyzer. Your task is to determine if a page contains a financial transactions table or list.

A transactions table/list typically has:
- Dates (like 01/15, 02/03, etc.)
- Merchant/description text
- Dollar amounts

Return has_transactions=true if you see what appears to be a list of financial transactions.
Return has_transactions=false ONLY if you are confident this page does NOT contain transactions .

When in doubt, return true."""

PAGE_CHECK_USER = """Does this page contain a financial transactions table or list?

Page text:
{page_text}"""


class PdfPlumberParser(BaseParser):
    """PDF parser using pdfplumber for text extraction + LLM for parsing."""

    def __init__(
        self,
        ollama_client: OllamaClient | None = None,
        debug_artifacts: DebugArtifacts | None = None,
        model: str = "mistral",
        host: str = "localhost",
        port: int = 11434,
    ):
        super().__init__(debug_artifacts)
        self._ollama = ollama_client
        self._owns_client = ollama_client is None
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
                timeout=600.0,
            )
        return self._ollama

    def _check_page_has_transactions(
        self,
        page_num: int,
        page_text: str,
        client: OllamaClient,
    ) -> tuple[int, bool]:
        """Check if a page contains a transactions table.

        Args:
            page_num: Page number (1-indexed)
            page_text: Extracted text from the page
            client: Ollama client to use

        Returns:
            Tuple of (page_num, has_transactions)
        """
        # Skip very short pages
        if len(page_text.strip()) < 100:
            logger.debug(f"Page {page_num}: skipping (too short)")
            return (page_num, False)

        try:
            prompt = PAGE_CHECK_USER.format(page_text=page_text[:4000])  # Limit text size
            result = client.generate_structured(
                prompt=prompt,
                response_model=PageHasTransactions,
                system=PAGE_CHECK_SYSTEM,
                temperature=0.1,
            )
            logger.debug(f"Page {page_num}: has_transactions={result.has_transactions}")
            return (page_num, result.has_transactions)
        except OllamaError as e:
            logger.warning(f"Page {page_num}: check failed ({e}), assuming has transactions")
            return (page_num, True)  # Default to including page on error

    def _filter_transaction_pages(
        self,
        pages_text: dict[int, str],
    ) -> list[int]:
        """Filter pages to only those containing transactions.

        Args:
            pages_text: Dict mapping page number to extracted text

        Returns:
            List of page numbers that contain transactions
        """
        client = self._ensure_client()
        results: dict[int, bool] = {}

        logger.info(f"Checking {len(pages_text)} pages for transaction tables...")

        # Check pages sequentially (Ollama processes one at a time anyway)
        for page_num, text in pages_text.items():
            _, has_transactions = self._check_page_has_transactions(page_num, text, client)
            results[page_num] = has_transactions

        # Return page numbers that have transactions, sorted
        transaction_pages = sorted([p for p, has in results.items() if has])
        logger.info(f"Found {len(transaction_pages)} pages with transactions: {transaction_pages}")

        return transaction_pages

    def parse(self, pdf_path: Path, statement_year: int | None = None) -> list[RawTransaction]:
        """Extract transactions from a PDF file.

        Args:
            pdf_path: Path to the PDF statement
            statement_year: Year to use for dates without year (e.g., MM/DD format)

        Returns:
            List of extracted transactions
        """
        self._statement_year = statement_year
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        total_start = time.perf_counter()
        logger.info(f"Parsing PDF: {pdf_path.name}")

        # Step 1: Extract text from all pages using pdfplumber
        extract_start = time.perf_counter()
        pages_text: dict[int, str] = {}

        with pdfplumber.open(pdf_path) as pdf:
            logger.info(f"PDF has {len(pdf.pages)} page(s)")

            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                text = page.extract_text() or ""
                pages_text[page_num] = text
                self.debug_artifacts.save_text(f"{pdf_path.stem}_page_{page_num}_text", text)

        extract_time = time.perf_counter() - extract_start
        logger.info(f"[TIMING] Text extraction: {extract_time:.2f}s")

        # Step 2: Filter to only pages containing transactions
        filter_start = time.perf_counter()
        transaction_pages = self._filter_transaction_pages(pages_text)
        filter_time = time.perf_counter() - filter_start
        logger.info(f"[TIMING] Page filtering: {filter_time:.2f}s")

        if not transaction_pages:
            logger.warning("No pages with transactions found")
            return []

        # Build text from only transaction pages
        filtered_text_parts = [
            f"=== PAGE {p} ===\n{pages_text[p]}"
            for p in transaction_pages
        ]
        full_text = "\n\n".join(filtered_text_parts)
        self.debug_artifacts.save_text(f"{pdf_path.stem}_filtered_text", full_text)
        logger.info(f"Filtered text: {len(full_text)} chars from {len(transaction_pages)} pages")

        # Step 3: Use LLM to parse transactions from text
        llm_start = time.perf_counter()
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
        except OllamaError as e:
            logger.error(f"Failed to parse with LLM: {e}")
            raise

        llm_time = time.perf_counter() - llm_start
        logger.info(f"[TIMING] LLM parsing: {llm_time:.2f}s ({len(extraction.transactions)} raw transactions)")

        # Step 4: Convert extracted transactions to RawTransaction objects
        process_start = time.perf_counter()
        all_transactions: list[RawTransaction] = []
        seen: set[tuple] = set()
        filtered_count = 0

        for tx in extraction.transactions:
            # Validate transaction description before processing
            if not is_valid_transaction(tx.description):
                logger.info(f"Filtered invalid: {tx.description[:60]}")
                filtered_count += 1
                continue

            parsed = self._parse_transaction(
                {"date": tx.date, "description": tx.description, "amount": tx.amount},
                full_text,
                statement_year=self._statement_year,
            )
            if parsed:
                key = (parsed.date, parsed.description, parsed.amount)
                if key not in seen:
                    seen.add(key)
                    all_transactions.append(parsed)

        if filtered_count > 0:
            logger.info(f"Filtered {filtered_count} invalid transaction(s) (statement artifacts)")

        process_time = time.perf_counter() - process_start
        self.debug_artifacts.save_json(
            f"{pdf_path.stem}_transactions",
            [tx.model_dump(mode="json") for tx in all_transactions],
        )

        total_time = time.perf_counter() - total_start
        logger.info(f"[TIMING] Post-processing: {process_time:.2f}s")
        logger.info(f"[TIMING] Total parse time: {total_time:.2f}s")
        logger.info(f"Extracted {len(all_transactions)} transactions")

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

            # Clean date string - extract just the date portion
            # Handles cases like "04/24/25 1" -> "04/24/25"
            date_match = re.match(r"(\d{1,4}[-/]\d{1,2}(?:[-/]\d{2,4})?)", date_str)
            if date_match:
                date_str = date_match.group(1)

            if statement_year is None:
                statement_year = datetime.now().year

            parsed_date = None
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%y", "%d/%m/%Y", "%m-%d-%Y", "%m%d%y"]:
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

    def __enter__(self) -> "PdfPlumberParser":
        return self

    def __exit__(self, *_) -> None:
        self.close()

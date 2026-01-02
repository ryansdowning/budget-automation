"""Pipeline orchestrator for parsing and categorizing transactions."""

import csv
from pathlib import Path

from loguru import logger

from src.categorizer import Categorizer
from src.clients.ollama import OllamaClient
from src.logging_config import DebugArtifacts
from src.models import CategoriesConfig, CategorizedTransaction, RawTransaction
from src.parser.ollama import OllamaParser


class Pipeline:
    """Orchestrates the PDF parsing and categorization flow."""

    def __init__(
        self,
        categories: CategoriesConfig,
        ollama_host: str = "localhost",
        ollama_port: int = 11434,
        ollama_model: str = "mistral",
        debug_artifacts: DebugArtifacts | None = None,
    ):
        """Initialize the pipeline.

        Args:
            categories: Category configuration
            ollama_host: Ollama server host
            ollama_port: Ollama server port
            ollama_model: Ollama model name
            debug_artifacts: Optional debug artifact manager
        """
        self.categories = categories
        self.debug_artifacts = debug_artifacts or DebugArtifacts()

        # Initialize clients
        self._ollama = OllamaClient(
            host=ollama_host,
            port=ollama_port,
            model=ollama_model,
        )
        self._parser = OllamaParser(
            host=ollama_host,
            port=ollama_port,
            model=ollama_model,
            debug_artifacts=self.debug_artifacts,
        )
        self._categorizer = Categorizer(
            categories=categories,
            ollama_client=self._ollama,
            debug_artifacts=self.debug_artifacts,
        )

    def process(
        self,
        pdf_paths: list[Path],
        dry_run: bool = False,
    ) -> list[CategorizedTransaction]:
        """Process PDF files and return categorized transactions.

        Args:
            pdf_paths: List of PDF file paths to process
            dry_run: If True, only parse (skip categorization)

        Returns:
            List of categorized transactions
        """
        logger.info(f"Processing {len(pdf_paths)} PDF file(s)")

        # Step 1: Parse all PDFs
        all_transactions: list[RawTransaction] = []
        for i, pdf_path in enumerate(pdf_paths):
            logger.info(f"[{i + 1}/{len(pdf_paths)}] {pdf_path.name}")
            try:
                transactions = self._parser.parse(pdf_path)
                all_transactions.extend(transactions)
            except Exception as e:
                logger.error(f"Failed to parse {pdf_path.name}: {e}")
                continue

        logger.info(f"Parsed {len(all_transactions)} total transactions")

        if dry_run:
            # Convert to CategorizedTransaction with placeholder category
            return [
                CategorizedTransaction(
                    date=tx.date,
                    description=tx.description,
                    amount=tx.amount,
                    category="(dry-run)",
                )
                for tx in all_transactions
            ]

        # Step 2: Categorize transactions
        if not all_transactions:
            return []

        # Check Ollama connection
        if not self._ollama.check_connection():
            logger.error("Cannot connect to Ollama. Is it running?")
            raise RuntimeError("Ollama connection failed")

        categorized = self._categorizer.categorize(all_transactions)

        logger.info(f"Categorized {len(categorized)} transactions")

        return categorized

    def write_csv(
        self,
        transactions: list[CategorizedTransaction],
        output_path: Path,
    ) -> None:
        """Write categorized transactions to CSV.

        Args:
            transactions: List of categorized transactions
            output_path: Path for output CSV file
        """
        if not transactions:
            logger.warning("No transactions to write")
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["date", "description", "amount", "category"],
            )
            writer.writeheader()
            for tx in transactions:
                writer.writerow(tx.to_csv_row())

        logger.info(f"Wrote {len(transactions)} transactions to {output_path}")

    def write_summary_csv(
        self,
        transactions: list[CategorizedTransaction],
        output_path: Path,
    ) -> None:
        """Write category totals summary to CSV.

        Args:
            transactions: List of categorized transactions
            output_path: Path for output CSV file
        """
        category_totals: dict[str, float] = {}
        for tx in transactions:
            category_totals[tx.category] = (
                category_totals.get(tx.category, 0) + float(tx.amount)
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["category", "total"])
            writer.writeheader()
            for category_name in self.categories.get_category_names():
                writer.writerow({
                    "category": category_name,
                    "total": f"{category_totals.get(category_name, 0):.2f}",
                })

        logger.info(f"Wrote summary to {output_path}")

    def print_summary(self, transactions: list[CategorizedTransaction]) -> None:
        """Print a summary of categorized transactions."""
        if not transactions:
            print("No transactions processed.")
            return

        # Count by category
        category_counts: dict[str, int] = {}
        category_totals: dict[str, float] = {}

        for tx in transactions:
            category_counts[tx.category] = category_counts.get(tx.category, 0) + 1
            category_totals[tx.category] = (
                category_totals.get(tx.category, 0) + float(tx.amount)
            )

        # Sort by count descending
        sorted_categories = sorted(
            category_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        print(f"Total transactions: {len(transactions)}")
        print("\nBy category:")

        for category, count in sorted_categories:
            pct = count / len(transactions) * 100
            total = category_totals[category]
            print(f"  {category:20s} {count:4d} ({pct:5.1f}%)  ${total:>10,.2f}")

        print("=" * 50)

    def close(self) -> None:
        """Clean up resources."""
        self._parser.close()
        self._ollama.close()

    def __enter__(self) -> "Pipeline":
        return self

    def __exit__(self, *args) -> None:
        self.close()

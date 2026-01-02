"""Transaction categorization using Ollama LLM."""

import json

from loguru import logger

from src.clients.ollama import OllamaClient, OllamaError
from src.logging_config import DebugArtifacts
from src.models import CategorizedTransaction, CategoriesConfig, RawTransaction
from src.prompts.categorize import CATEGORIZE_SYSTEM, CATEGORIZE_USER

DEFAULT_BATCH_SIZE = 15


class CategorizationError(Exception):
    """Error during transaction categorization."""

    def __init__(self, transaction: RawTransaction, reason: str):
        self.transaction = transaction
        self.reason = reason
        super().__init__(f"Failed to categorize '{transaction.description}': {reason}")


def build_categorization_schema(category_names: list[str]) -> dict:
    """Build a JSON schema for categorization with dynamic category enum.

    Args:
        category_names: List of valid category names

    Returns:
        JSON schema dict for structured output
    """
    return {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "The transaction description",
                        },
                        "category": {
                            "type": "string",
                            "enum": category_names,
                            "description": "The assigned category",
                        },
                    },
                    "required": ["description", "category"],
                },
            },
        },
        "required": ["assignments"],
    }


def build_single_categorization_schema(category_names: list[str]) -> dict:
    """Build a JSON schema for single transaction categorization.

    Args:
        category_names: List of valid category names

    Returns:
        JSON schema dict for structured output
    """
    return {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": category_names,
                "description": "The assigned category",
            },
        },
        "required": ["category"],
    }


class Categorizer:
    """Categorizes transactions using Ollama LLM."""

    def __init__(
        self,
        categories: CategoriesConfig,
        ollama_client: OllamaClient,
        batch_size: int = DEFAULT_BATCH_SIZE,
        debug_artifacts: DebugArtifacts | None = None,
    ):
        self.categories = categories
        self.client = ollama_client
        self.batch_size = batch_size
        self.debug_artifacts = debug_artifacts or DebugArtifacts()
        self._valid_categories = list(categories.get_category_names())
        self._batch_schema = build_categorization_schema(self._valid_categories)
        self._single_schema = build_single_categorization_schema(self._valid_categories)

    def categorize(
        self,
        transactions: list[RawTransaction],
    ) -> list[CategorizedTransaction]:
        """Categorize a list of transactions in batches.

        Args:
            transactions: List of raw transactions to categorize

        Returns:
            List of categorized transactions
        """
        if not transactions:
            return []

        results: list[CategorizedTransaction] = []
        batches = list(self._batch(transactions, self.batch_size))

        logger.info(f"Categorizing {len(transactions)} transactions in {len(batches)} batches")

        for i, batch in enumerate(batches):
            logger.debug(f"Processing batch {i + 1}/{len(batches)}, size={len(batch)}")
            batch_results = self._categorize_batch(batch, batch_num=i + 1)
            results.extend(batch_results)

        return results

    def _categorize_batch(
        self,
        transactions: list[RawTransaction],
        batch_num: int = 0,
    ) -> list[CategorizedTransaction]:
        """Categorize a batch of transactions using structured output."""
        system = CATEGORIZE_SYSTEM.format(categories=self.categories.to_prompt_text())
        transactions_text = "\n".join(f"- {t.description}" for t in transactions)
        prompt = CATEGORIZE_USER.format(transactions=transactions_text)

        self.debug_artifacts.save_json(
            f"categorize_batch_{batch_num}_request",
            {"system": system, "prompt": prompt, "schema": self._batch_schema},
        )

        try:
            response_text = self.client.generate(
                prompt=prompt,
                system=system,
                schema=self._batch_schema,
            )
            response = json.loads(response_text)
        except OllamaError as e:
            logger.error(f"Batch {batch_num} categorization failed: {e}")
            return self._categorize_individually(transactions)
        except json.JSONDecodeError as e:
            logger.error(f"Batch {batch_num} JSON parse failed: {e}")
            return self._categorize_individually(transactions)

        self.debug_artifacts.save_json(
            f"categorize_batch_{batch_num}_response",
            response,
        )

        assignments = response.get("assignments", [])
        category_map = {a["description"]: a["category"] for a in assignments}

        results: list[CategorizedTransaction] = []
        for transaction in transactions:
            category = category_map.get(transaction.description)

            # Try partial match if no exact match
            if not category:
                for desc, cat in category_map.items():
                    if desc in transaction.description or transaction.description in desc:
                        category = cat
                        break

            if not category:
                logger.warning(f"No category match for '{transaction.description}'")
                category = self._categorize_single(transaction)

            results.append(
                CategorizedTransaction(
                    date=transaction.date,
                    description=transaction.description,
                    amount=transaction.amount,
                    category=category,
                )
            )

        return results

    def _categorize_individually(
        self,
        transactions: list[RawTransaction],
    ) -> list[CategorizedTransaction]:
        """Categorize transactions one at a time (fallback)."""
        results: list[CategorizedTransaction] = []
        for transaction in transactions:
            category = self._categorize_single(transaction)
            results.append(
                CategorizedTransaction(
                    date=transaction.date,
                    description=transaction.description,
                    amount=transaction.amount,
                    category=category,
                )
            )
        return results

    def _categorize_single(self, transaction: RawTransaction) -> str:
        """Categorize a single transaction using structured output."""
        system = CATEGORIZE_SYSTEM.format(categories=self.categories.to_prompt_text())
        prompt = f"Categorize this transaction: {transaction.description}"

        try:
            response_text = self.client.generate(
                prompt=prompt,
                system=system,
                schema=self._single_schema,
            )
            response = json.loads(response_text)
            category = response.get("category")
            if category:
                return category
        except (OllamaError, json.JSONDecodeError) as e:
            logger.warning(f"Single categorization failed for '{transaction.description}': {e}")

        logger.warning(f"Defaulting to 'Other' for: {transaction.description}")
        return "Other"

    @staticmethod
    def _batch(items: list, size: int):
        """Yield successive batches of items."""
        for i in range(0, len(items), size):
            yield items[i : i + size]

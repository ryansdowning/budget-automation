"""Pydantic data models for transactions and categories."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class RawTransaction(BaseModel):
    """Transaction extracted from PDF before categorization."""

    date: date
    description: str
    amount: Decimal
    raw_text: str = Field(default="", description="Original text for debugging")

    def __hash__(self) -> int:
        """Hash for deduplication."""
        return hash((self.date, self.description, self.amount))

    def __eq__(self, other: object) -> bool:
        """Equality check for deduplication."""
        if not isinstance(other, RawTransaction):
            return False
        return (
            self.date == other.date
            and self.description == other.description
            and self.amount == other.amount
        )


class CategorizedTransaction(BaseModel):
    """Transaction after categorization."""

    date: date
    description: str
    amount: Decimal
    category: str

    def to_csv_row(self) -> dict[str, str]:
        """Convert to CSV row dict."""
        return {
            "date": self.date.isoformat(),
            "description": self.description,
            "amount": str(self.amount),
            "category": self.category,
        }


class Category(BaseModel):
    """Category definition for transaction classification."""

    name: str
    description: str
    keywords: list[str] = Field(default_factory=list)


class CategoriesConfig(BaseModel):
    """Container for category definitions."""

    categories: list[Category]

    def get_category_names(self) -> list[str]:
        """Get list of category names."""
        return [c.name for c in self.categories]

    def to_prompt_text(self) -> str:
        """Format categories for LLM prompt."""
        lines = []
        for cat in self.categories:
            line = f"- {cat.name}: {cat.description}"
            if cat.keywords:
                line += f" (examples: {', '.join(cat.keywords[:3])})"
            lines.append(line)
        return "\n".join(lines)


# LLM Response Schemas for structured output


class ExtractedTransaction(BaseModel):
    """Single transaction extracted by LLM from OCR text."""

    date: str = Field(description="Transaction date in MM/DD or MM/DD/YYYY format")
    description: str = Field(description="Merchant name or transaction description")
    amount: float = Field(description="Transaction amount (positive for charges, negative for credits)")


class TransactionExtractionResponse(BaseModel):
    """LLM response containing extracted transactions."""

    transactions: list[ExtractedTransaction] = Field(
        description="List of all transactions found in the document"
    )


class CategoryAssignment(BaseModel):
    """Single transaction category assignment."""

    description: str = Field(description="The transaction description")
    category: str = Field(description="Assigned category name")


class CategorizationResponse(BaseModel):
    """LLM response for batch categorization."""

    assignments: list[CategoryAssignment] = Field(
        description="Category assignments for each transaction"
    )

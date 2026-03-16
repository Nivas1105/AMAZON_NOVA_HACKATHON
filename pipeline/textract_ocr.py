from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Optional
import boto3


@dataclass
class TransactionRow:
    """A single parsed transaction from Textract output."""
    date: Optional[str] = None
    description: Optional[str] = None
    debit: Optional[float] = None
    credit: Optional[float] = None
    balance: Optional[float] = None
    raw_row: list[str] = field(default_factory=list)
    confidence: float = 0.0 


@dataclass
class TextractResult:
    """Full result from Stage 1 Textract analysis."""
    rows: list[TransactionRow] = field(default_factory=list)
    avg_confidence: float = 0.0
    is_bank_statement: bool = False
    document_type: str = "unknown"
    low_confidence_fields: list[dict] = field(default_factory=list)
    raw_text: str = ""
    total_pages: int = 1
    warning: Optional[str] = None  


BANK_STATEMENT_KEYWORDS = [
    "balance", "transaction", "account", "statement", "debit", "credit",
    "deposit", "withdrawal", "opening balance", "closing balance", "reference",
    "date", "description", "amount", "bank", "savings", "checking"
]

AMOUNT_PATTERN = re.compile(r"^\$?[\d,]+\.?\d{0,2}$")
DATE_PATTERN = re.compile(
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}"
)


def _parse_amount(text: str) -> Optional[float]:
    """Convert a string like '$1,234.56' or '(500.00)' to float."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("$", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _is_date(text: str) -> bool:
    return bool(DATE_PATTERN.search(text.strip()))


def _is_amount(text: str) -> bool:
    clean = text.strip().replace(",", "").replace("$", "").lstrip("-").lstrip("(").rstrip(")")
    try:
        float(clean)
        return True
    except ValueError:
        return False


class TextractOCR:
    """Wraps Amazon Textract to extract structured transaction data."""

    def __init__(self, bedrock_client=None):
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3, os
            from dotenv import load_dotenv
            load_dotenv()
            self._client = boto3.client(
                "textract",
                region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
            )
        return self._client

    def analyze(self, image_bytes: bytes, mime_type: str = "image/png") -> TextractResult:
        result = TextractResult()

        try:
            client = self._get_client()
            response = client.analyze_document(
                Document={"Bytes": image_bytes},
                FeatureTypes=["TABLES", "FORMS"],
            )
        except Exception as e:
            result.warning = f"Textract unavailable: {str(e)}"
            result.raw_text = f"[Textract error: {str(e)}]"
            return result

        blocks = response.get("Blocks", [])
        block_map = {b["Id"]: b for b in blocks}

        lines = [
            b["Text"] for b in blocks
            if b["BlockType"] == "LINE" and "Text" in b
        ]
        result.raw_text = "\n".join(lines)

        lower_text = result.raw_text.lower()
        keyword_hits = sum(1 for kw in BANK_STATEMENT_KEYWORDS if kw in lower_text)
        result.is_bank_statement = keyword_hits >= 4
        result.document_type = "bank_statement" if result.is_bank_statement else "unknown_document"

        table_blocks = [b for b in blocks if b["BlockType"] == "TABLE"]
        all_confidences = []

        for table in table_blocks:
            rows_dict: dict[int, dict[int, tuple[str, float]]] = {}

            for rel in table.get("Relationships", []):
                if rel["Type"] != "CHILD":
                    continue
                for cell_id in rel["Ids"]:
                    cell = block_map.get(cell_id)
                    if not cell or cell["BlockType"] != "CELL":
                        continue

                    row_idx = cell.get("RowIndex", 0)
                    col_idx = cell.get("ColumnIndex", 0)
                    confidence = cell.get("Confidence", 0.0)
                    all_confidences.append(confidence)

                    # Extract cell text
                    cell_text = ""
                    for cr in cell.get("Relationships", []):
                        if cr["Type"] == "CHILD":
                            for word_id in cr["Ids"]:
                                word = block_map.get(word_id)
                                if word and word["BlockType"] == "WORD":
                                    cell_text += word.get("Text", "") + " "
                    cell_text = cell_text.strip()

                    if confidence < 80.0 and cell_text:
                        result.low_confidence_fields.append({
                            "row": row_idx, "col": col_idx,
                            "text": cell_text, "confidence": round(confidence, 1)
                        })

                    if row_idx not in rows_dict:
                        rows_dict[row_idx] = {}
                    rows_dict[row_idx][col_idx] = (cell_text, confidence)

            header_row = rows_dict.get(1, {})
            col_map = _detect_column_mapping(header_row)

            for row_idx in sorted(rows_dict.keys()):
                if row_idx == 1 and col_map:
                    continue
                cells = rows_dict[row_idx]
                row_texts = [cells.get(c, ("", 0.0))[0] for c in sorted(cells.keys())]
                row_conf = [cells.get(c, ("", 0.0))[1] for c in sorted(cells.keys())]

                txn = _map_row_to_transaction(row_texts, col_map)
                if txn:
                    txn.raw_row = row_texts
                    txn.confidence = sum(row_conf) / len(row_conf) if row_conf else 0.0
                    result.rows.append(txn)

        result.avg_confidence = (
            sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        )

        return result


def _detect_column_mapping(header_cells: dict[int, tuple[str, float]]) -> dict[str, int]:
    """Map header labels to column indices."""
    mapping = {}
    for col_idx, (text, _) in header_cells.items():
        lower = text.lower()
        if "date" in lower:
            mapping["date"] = col_idx
        elif any(w in lower for w in ["description", "particular", "detail", "narration", "merchant"]):
            mapping["description"] = col_idx
        elif any(w in lower for w in ["debit", "withdrawal", "dr"]):
            mapping["debit"] = col_idx
        elif any(w in lower for w in ["credit", "deposit", "cr"]):
            mapping["credit"] = col_idx
        elif "balance" in lower:
            mapping["balance"] = col_idx
    return mapping


def _map_row_to_transaction(
    row_texts: list[str],
    col_map: dict[str, int]
) -> Optional[TransactionRow]:
    """Convert a list of cell texts to a TransactionRow."""
    if not any(row_texts):
        return None

    txn = TransactionRow()

    if col_map:
        def get(key):
            idx = col_map.get(key)
            if idx is None:
                return None
            real_idx = idx - 1
            return row_texts[real_idx] if 0 <= real_idx < len(row_texts) else None

        txn.date = get("date")
        txn.description = get("description")
        txn.debit = _parse_amount(get("debit") or "")
        txn.credit = _parse_amount(get("credit") or "")
        txn.balance = _parse_amount(get("balance") or "")
    else:
        amounts = []
        for text in row_texts:
            if not txn.date and _is_date(text):
                txn.date = text
            elif _is_amount(text):
                amounts.append(_parse_amount(text))
            elif text and not txn.description and not _is_date(text):
                txn.description = text

        if len(amounts) >= 2:
            txn.balance = amounts[-1]
            val = amounts[-2]
            if val and val < 0:
                txn.debit = abs(val)
            else:
                txn.credit = val
        elif len(amounts) == 1:
            txn.balance = amounts[0]

    if not txn.date and not txn.description and txn.debit is None and txn.credit is None:
        return None

    return txn

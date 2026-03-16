
import csv
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

import boto3
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from pipeline.textract_ocr import TextractOCR
from pipeline.rules import RuleEngine
from pipeline.nova_analyzer import NovaAnalyzer

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("nova-risk")

app = FastAPI(title="AI Financial Risk Analyst — 3-Stage Pipeline", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_path = Path(__file__).parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
bedrock = boto3.client(
    service_name="bedrock-runtime",
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
)

textract_ocr = TextractOCR()
rule_engine  = RuleEngine()
nova         = NovaAnalyzer(bedrock)

@app.on_event("startup")
async def startup_event():
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    has_creds = bool(os.getenv("AWS_ACCESS_KEY_ID"))
    log.info("━" * 60)
    log.info("  🏆  Nova Risk Analyst  v2.0.0  — AWS Nova Hackathon 2025")
    log.info(f"  📍  Region  : {region}")
    log.info(f"  🤖  Model   : us.amazon.nova-2-lite-v1:0")
    log.info(f"  🔑  Creds   : {'✅ loaded from .env' if has_creds else '⚠️  not set — demo mode only'}")
    log.info(f"  🌐  UI      : http://localhost:8000")
    log.info("━" * 60)


class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = ""

class ChatResponse(BaseModel):
    answer: str

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

def parse_csv_to_rows(content: str) -> list[dict]:
    """
    Parse a CSV file into a list of transaction dicts.
    Handles common column name variations.
    """
    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for r in reader:
        # Normalise keys to lowercase
        row = {k.strip().lower(): (v.strip() if v else "") for k, v in r.items()}

        def get(*keys):
            for k in keys:
                if k in row and row[k]:
                    return row[k]
            return None

        amount_str = get("amount", "debit", "withdrawal", "credit", "deposit")
        amount = None
        if amount_str:
            try:
                amount = float(amount_str.replace(",", "").replace("$", ""))
            except ValueError:
                pass

        balance_str = get("balance", "running balance", "closing balance")
        balance = None
        if balance_str:
            try:
                balance = float(balance_str.replace(",", "").replace("$", ""))
            except ValueError:
                pass

        rows.append({
            "date": get("date", "transaction date", "txn date", "posting date") or "",
            "time": get("time", "transaction time") or "",
            "merchant": get("merchant", "description", "particulars", "narration", "payee") or "",
            "category": get("category", "type", "transaction type") or "",
            "amount": amount,
            "balance": balance,
            "transaction_id": get("transaction_id", "txn_id", "ref", "reference") or "",
        })
    return rows


def rows_to_text_summary(rows: list[dict], max_rows: int = 60) -> str:
    """Convert row dicts to a readable text block for Nova 2 Lite prompts."""
    lines = ["Date       | Time  | Merchant                  | Amount      | Balance"]
    lines.append("-" * 75)
    for r in rows[:max_rows]:
        lines.append(
            f"{str(r.get('date','')):<10} | "
            f"{str(r.get('time','')):<5} | "
            f"{str(r.get('merchant','')):<25} | "
            f"{str(r.get('amount','')):<11} | "
            f"{str(r.get('balance',''))}"
        )
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows)")
    return "\n".join(lines)


def _build_pipeline_response(
    nova_result: dict,
    rule_result,
    textract_result=None,
    raw_nova: str = "",
) -> dict:
    """
    Merge Nova 2 Lite result with rule engine and textract metadata into
    the final response structure used by the frontend.
    """
    # Combine additional_fraud_flags with any existing fraud_flags
    fraud_flags = nova_result.get("additional_fraud_flags", []) or []

    # Add rule findings as fraud_flags (for backward-compat with frontend)
    for finding in rule_result.findings:
        fraud_flags.append({
            "type": finding.title,
            "description": finding.description,
            "severity": finding.severity,
            "source": "rule_engine",
            "evidence": finding.evidence[:3],
        })

    response = {
        # Core risk fields
        "risk_score": nova_result.get("risk_score", 50),
        "risk_level": nova_result.get("risk_level", "MEDIUM"),
        "summary": nova_result.get("summary", ""),
        "recommendations": nova_result.get("recommendations", []),

        # Fraud flags (AI + rules merged)
        "fraud_flags": fraud_flags,

        # Anomalies from Nova
        "anomalies": nova_result.get("anomalies", []),

        # Audit trail (new — shows each pipeline stage)
        "audit_trail": nova_result.get("audit_trail", []),

        # Rule engine output
        "rule_findings": {
            "findings": [f.to_dict() for f in rule_result.findings],
            "total": len(rule_result.findings),
            "score_adjustment": rule_result.score_adjustment,
            "summary": rule_result.summary,
            "transactions_analyzed": rule_result.transactions_analyzed,
        },

        # Transaction stats
        "transaction_stats": nova_result.get("transaction_stats", {}),

        # Pipeline metadata (for frontend stage display)
        "pipeline": {
            "stages_completed": ["Stage 1: OCR/Parsing", "Stage 2: Rule Engine", "Stage 3: Nova 2 Lite AI"],
            "textract_used": textract_result is not None and textract_result.warning is None,
            "textract_confidence": (
                round(textract_result.avg_confidence, 1) if textract_result else None
            ),
            "textract_rows_extracted": len(textract_result.rows) if textract_result else None,
            "is_bank_statement": (
                textract_result.is_bank_statement if textract_result else None
            ),
            "rules_triggered": rule_result.rules_triggered,
        },

        # For chat context
        "raw_analysis": raw_nova,
    }

    # Clamp risk score 0–100
    response["risk_score"] = max(0, min(100, response["risk_score"]))
    return response


@app.get("/")
async def serve_frontend():
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "AI Financial Risk Analyst API v2.0 — 3-Stage Pipeline"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "pipeline": ["Textract OCR", "Rule Engine", "Nova 2 Lite"],
        "model": "us.amazon.nova-2-lite-v1:0",
        "region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    }


@app.post("/demo")
async def demo():
    """
    Returns a realistic pre-baked analysis result so judges can explore the
    full dashboard without uploading a real file or having AWS credentials.
    """
    return {
        "risk_score": 74,
        "risk_level": "HIGH",
        "summary": (
            "This account exhibits multiple high-severity risk indicators including "
            "structuring activity near the $10,000 reporting threshold, an unusual "
            "velocity spike on 2024-01-15, and 4 large international wire transfers "
            "totaling $47,200. Immediate compliance review is recommended."
        ),
        "recommendations": [
            "File a Suspicious Activity Report (SAR) with FinCEN within 30 days.",
            "Freeze any outbound international wire transfers pending review.",
            "Request source-of-funds documentation for the 3 large deposits (Jan 10–17).",
            "Review all transactions with 'OFFSHORE SWIFT' and 'WIRE INTL' descriptors.",
            "Escalate to the AML Compliance Officer for enhanced due diligence.",
        ],
        "fraud_flags": [
            {
                "type": "Structuring Pattern — Near $10,000 Threshold",
                "description": "8 withdrawals between $9,500–$9,999 across 12 days, totaling $78,214. Classic structuring to avoid CTR filing.",
                "severity": "HIGH",
                "source": "rule_engine",
                "evidence": [
                    "2024-01-03 | CASH WD | $9,850.00",
                    "2024-01-07 | ATM WD  | $9,920.00",
                    "2024-01-11 | CASH WD | $9,750.00",
                ],
            },
            {
                "type": "International Wire Transfer",
                "description": "4 international wire transfers totaling $47,200 sent to accounts in jurisdictions flagged for financial opacity.",
                "severity": "HIGH",
                "source": "rule_engine",
                "evidence": [
                    "2024-01-05 | SWIFT WIRE INTL | $12,500.00",
                    "2024-01-12 | OFFSHORE SWIFT  | $15,700.00",
                ],
            },
            {
                "type": "Transaction Velocity Spike",
                "description": "January 15 saw 23 transactions — 6.8× the daily average of 3.4. Possible account compromise or coordinated activity.",
                "severity": "HIGH",
                "source": "rule_engine",
                "evidence": ["2024-01-15: 23 transactions (avg=3.4/day)"],
            },
            {
                "type": "Round-Trip Fund Movement",
                "description": "AI detected a pattern of large deposits followed by near-equal withdrawals within 24–48 hours — consistent with layering.",
                "severity": "HIGH",
                "source": "nova_ai",
            },
            {
                "type": "Unusual Income Pattern",
                "description": "3 large credit deposits ($18,000, $22,000, $15,000) within 7 days with no apparent payroll or business description.",
                "severity": "MEDIUM",
                "source": "nova_ai",
            },
        ],
        "anomalies": [
            {"category": "Late-Night Transactions", "detail": "6 transactions between 01:00–04:30 AM", "amount": "$34,200"},
            {"category": "Round-Number Withdrawals", "detail": "42% of debits are exact round amounts ($500, $1,000, $5,000)", "amount": None},
            {"category": "Duplicate Transactions", "detail": "2 duplicate pairs: same amount + merchant within 24 hours", "amount": "$3,400"},
        ],
        "rule_findings": {
            "findings": [
                {
                    "rule_id": "structuring_10000",
                    "title": "Structuring Pattern — Near $10,000 Threshold",
                    "severity": "HIGH",
                    "description": "8 transactions found between $9,500.00 and $9,999.99, suggesting deliberate structuring to stay below the $10,000 reporting threshold.",
                    "evidence": ["2024-01-03 | CASH WD | $9,850.00", "2024-01-07 | ATM WD | $9,920.00", "2024-01-11 | CASH WD | $9,750.00"],
                    "affected_count": 8,
                    "total_amount": "$78,214.00",
                },
                {
                    "rule_id": "international_wire",
                    "title": "International Wire / Foreign Transfers",
                    "severity": "HIGH",
                    "description": "4 international or foreign transfer(s) detected, totaling $47,200.",
                    "evidence": ["2024-01-05 | SWIFT WIRE INTL | $12,500.00", "2024-01-12 | OFFSHORE SWIFT | $15,700.00"],
                    "affected_count": 4,
                    "total_amount": "$47,200.00",
                },
                {
                    "rule_id": "velocity_spike",
                    "title": "Transaction Velocity Spike",
                    "severity": "HIGH",
                    "description": "On 1 day(s), transaction count was 3× or more above the daily average of 3.4.",
                    "evidence": ["2024-01-15: 23 transactions (avg=3.4/day)"],
                    "affected_count": 1,
                },
                {
                    "rule_id": "round_numbers",
                    "title": "Unusual Round-Number Transaction Pattern",
                    "severity": "MEDIUM",
                    "description": "31 of 74 debit transactions (42%) are perfectly round amounts, which is statistically unusual for legitimate spending.",
                    "evidence": ["2024-01-03 | CASH WD | $5,000.00", "2024-01-08 | ATM | $1,000.00"],
                    "affected_count": 31,
                },
                {
                    "rule_id": "unusual_income",
                    "title": "Unusual Income / Large Credit Cluster",
                    "severity": "MEDIUM",
                    "description": "3 large credits (>$2,000 each) clustered within 7 days, totaling $55,000. May indicate income inflation.",
                    "evidence": ["2024-01-10 | DEPOSIT | +$18,000.00", "2024-01-14 | WIRE IN | +$22,000.00", "2024-01-17 | TRANSFER | +$15,000.00"],
                    "affected_count": 3,
                    "total_amount": "$55,000.00",
                },
                {
                    "rule_id": "duplicate_transaction",
                    "title": "Duplicate Transaction(s) Detected",
                    "severity": "HIGH",
                    "description": "2 potential duplicate transaction(s) found with same amount and merchant within 24 hours.",
                    "evidence": ["2024-01-09 | CASINO ROYALE | $1,700.00 (duplicate)"],
                    "affected_count": 2,
                },
            ],
            "total": 6,
            "score_adjustment": 64,
            "summary": "3 HIGH-severity finding(s): Structuring Pattern, International Wire, Velocity Spike. 2 MEDIUM-severity finding(s): Round-Number Pattern, Unusual Income.",
            "transactions_analyzed": 91,
        },
        "audit_trail": [
            {"stage": "Stage 1 OCR", "finding": "91 transactions parsed from CSV export", "severity": "INFO"},
            {"stage": "Stage 2 Rules", "finding": "Structuring pattern detected — 8 sub-$10k withdrawals", "severity": "HIGH"},
            {"stage": "Stage 2 Rules", "finding": "Velocity spike on 2024-01-15 (23 txns)", "severity": "HIGH"},
            {"stage": "Stage 2 Rules", "finding": "4 international wire transfers flagged", "severity": "HIGH"},
            {"stage": "Stage 2 Rules", "finding": "2 duplicate transactions identified", "severity": "HIGH"},
            {"stage": "Stage 3 AI", "finding": "Round-trip layering pattern confirmed by Amazon Nova 2 Lite", "severity": "HIGH"},
            {"stage": "Stage 3 AI", "finding": "Unexplained income cluster ($55,000 in 7 days)", "severity": "MEDIUM"},
        ],
        "transaction_stats": {
            "total_transactions": 91,
            "total_amount": "$312,450.00",
            "suspicious_count": 23,
            "high_risk_categories": ["International Wire", "Cash Withdrawals", "Casino", "Crypto Exchange"],
        },
        "pipeline": {
            "stages_completed": ["Stage 1: OCR/Parsing", "Stage 2: Rule Engine", "Stage 3: Nova 2 Lite AI"],
            "textract_used": False,
            "textract_confidence": None,
            "textract_rows_extracted": None,
            "is_bank_statement": None,
            "rules_triggered": ["structuring_10000", "international_wire", "velocity_spike", "round_numbers", "unusual_income", "duplicate_transaction"],
        },
        "raw_analysis": "Demo mode — pre-baked result. Upload a real file to run the live 3-stage pipeline.",
    }


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    """
    Full 3-stage analysis pipeline:
      - CSV  → parse → rules → Amazon Nova 2 Lite (text)
      - PDF  → Textract → rules → Amazon Nova 2 Lite (text)
      - Image → Textract → rules → Amazon Nova 2 Lite (image + text)
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    filename = (file.filename or "").lower()
    file_bytes = await file.read()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 20 MB.")

    log.info(f"Analyze request: {file.filename}  ({len(file_bytes):,} bytes)")

    try:
        # ── CSV path ───────────────────────────────────────────────
        if filename.endswith(".csv"):
            content = file_bytes.decode("utf-8", errors="ignore")
            rows = parse_csv_to_rows(content)
            if not rows:
                raise HTTPException(status_code=400, detail="CSV appears empty or could not be parsed.")

            # Stage 2: rules
            rule_result = rule_engine.run(rows)

            # Stage 3: Amazon Nova 2 Lite (text)
            text_summary = rows_to_text_summary(rows)
            nova_result, raw_nova = nova.analyze_text(
                structured_text=text_summary,
                rule_summary=rule_result.summary,
                rule_findings=[f.to_dict() for f in rule_result.findings],
                file_type="CSV transaction export",
            )

            return _build_pipeline_response(nova_result, rule_result, raw_nova=raw_nova)

        # ── PDF path ───────────────────────────────────────────────
        elif filename.endswith(".pdf"):
            # Stage 1: Textract
            from pdf2image import convert_from_bytes
            from PIL import Image

            pages = convert_from_bytes(file_bytes, dpi=150, fmt="png", first_page=1, last_page=3)
            textract_result = None
            all_rows = []
            combined_text = ""

            for page_img in pages:
                import io as _io
                buf = _io.BytesIO()
                page_img.save(buf, format="PNG")
                page_bytes = buf.getvalue()

                tr = textract_ocr.analyze(page_bytes)
                if textract_result is None:
                    textract_result = tr
                else:
                    textract_result.rows.extend(tr.rows)
                all_rows.extend(tr.rows)
                combined_text += tr.raw_text + "\n"

            # Stage 2: rules
            rule_result = rule_engine.run(all_rows)

            # Stage 3: Amazon Nova 2 Lite (text with Textract output)
            text_to_analyze = combined_text if combined_text.strip() else "(Textract could not extract text)"
            nova_result, raw_nova = nova.analyze_text(
                structured_text=text_to_analyze,
                rule_summary=rule_result.summary,
                rule_findings=[f.to_dict() for f in rule_result.findings],
                file_type="PDF bank statement",
            )

            return _build_pipeline_response(nova_result, rule_result, textract_result, raw_nova)

        # ── Image path ─────────────────────────────────────────────
        elif any(filename.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
            # Ensure PNG for Textract/Nova
            from PIL import Image
            import io as _io

            img = Image.open(_io.BytesIO(file_bytes))
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()

            # Stage 1: Textract
            textract_result = textract_ocr.analyze(png_bytes)

            # Stage 2: rules (on Textract rows)
            rule_result = rule_engine.run(textract_result.rows)

            # Stage 3: Amazon Nova 2 Lite (multimodal image + rule context)
            nova_result, raw_nova = nova.analyze_image(
                image_bytes_list=[png_bytes],
                rule_summary=rule_result.summary,
                rule_findings=[f.to_dict() for f in rule_result.findings],
                file_type="bank statement image",
            )

            return _build_pipeline_response(nova_result, rule_result, textract_result, raw_nova)

        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Please upload a .csv, .pdf, .png, .jpg, or .jpeg file."
            )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Pipeline error for {file.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Follow-up Q&A about the analyzed document."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    try:
        answer = nova.chat(req.message, req.context)
        return ChatResponse(answer=answer)
    except Exception as e:
        log.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")

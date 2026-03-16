
from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class RuleFinding:
    """A single finding from the rule engine."""
    rule_id: str
    title: str
    severity: str          
    description: str
    evidence: list[str] = field(default_factory=list)
    affected_count: int = 0
    total_amount: Optional[float] = None

    def to_dict(self) -> dict:
        d = {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity,
            "description": self.description,
            "evidence": self.evidence[:5],  
            "affected_count": self.affected_count,
        }
        if self.total_amount is not None:
            d["total_amount"] = f"${self.total_amount:,.2f}"
        return d


@dataclass
class RuleEngineResult:
    """Result from Stage 2 rule engine."""
    findings: list[RuleFinding] = field(default_factory=list)
    score_adjustment: int = 0       
    transactions_analyzed: int = 0
    rules_triggered: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "score_adjustment": self.score_adjustment,
            "transactions_analyzed": self.transactions_analyzed,
            "rules_triggered": self.rules_triggered,
        }


ROUND_AMOUNTS = {100, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 2500,
                 3000, 4000, 5000, 7500, 10000}

def _parse_date(date_str: str) -> Optional[datetime]:
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y",
               "%d-%m-%Y", "%Y/%m/%d", "%b %d", "%B %d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _rows_to_transactions(rows) -> list[dict]:
    txns = []
    for r in rows:
        if hasattr(r, "__dataclass_fields__"):
            amount = None
            if r.debit is not None:
                amount = -abs(r.debit)
            elif r.credit is not None:
                amount = abs(r.credit)
            txns.append({
                "date": r.date or "",
                "time": "",
                "merchant": r.description or "",
                "amount": amount,
                "balance": r.balance,
            })
        else:
            txns.append(r)
    return txns

def _rule_balance_mismatch(txns: list[dict]) -> Optional[RuleFinding]:
    """Check if stated balance matches computed running balance."""
    mismatches = []
    running = None

    for t in txns:
        amt = t.get("amount")
        bal = t.get("balance")
        if amt is None or bal is None:
            continue
        if running is None:
            running = bal - amt  
        running += amt
        if abs(running - bal) > 1.0:   
            mismatches.append(
                f"{t.get('date','?')} — expected ${running:,.2f}, stated ${bal:,.2f}"
            )
            running = bal  

    if not mismatches:
        return None
    return RuleFinding(
        rule_id="balance_mismatch",
        title="Balance Integrity Failure",
        severity="HIGH",
        description=f"Computed running balance does not match stated balance on {len(mismatches)} occasion(s). This may indicate statement tampering or data errors.",
        evidence=mismatches[:5],
        affected_count=len(mismatches),
    )


def _rule_structuring(txns: list[dict], threshold: float, label: str) -> Optional[RuleFinding]:
    """Detect structuring: multiple transactions just below a reporting threshold."""
    lo = threshold * 0.95
    hi = threshold - 0.01
    hits = [t for t in txns if t.get("amount") and lo <= abs(t["amount"]) <= hi]
    if len(hits) < 2:
        return None
    total = sum(abs(t["amount"]) for t in hits)
    evidence = [
        f"{t.get('date','?')} | {t.get('merchant','?')} | ${abs(t['amount']):,.2f}"
        for t in hits[:5]
    ]
    return RuleFinding(
        rule_id=f"structuring_{int(threshold)}",
        title=f"Structuring Pattern — Near ${threshold:,.0f} Threshold",
        severity="HIGH",
        description=(
            f"{len(hits)} transactions found between ${lo:,.2f} and ${hi:,.2f}, "
            f"suggesting deliberate structuring to stay below the ${threshold:,.0f} reporting threshold."
        ),
        evidence=evidence,
        affected_count=len(hits),
        total_amount=total,
    )


def _rule_round_numbers(txns: list[dict]) -> Optional[RuleFinding]:
    """Flag if > 30% of transactions are perfect round numbers."""
    debits = [t for t in txns if t.get("amount") and t["amount"] < 0]
    if len(debits) < 5:
        return None
    rounds = [t for t in debits if abs(t["amount"]) in ROUND_AMOUNTS]
    ratio = len(rounds) / len(debits)
    if ratio < 0.30:
        return None
    evidence = [
        f"{t.get('date','?')} | {t.get('merchant','?')} | ${abs(t['amount']):,.2f}"
        for t in rounds[:5]
    ]
    return RuleFinding(
        rule_id="round_numbers",
        title="Unusual Round-Number Transaction Pattern",
        severity="MEDIUM",
        description=(
            f"{len(rounds)} of {len(debits)} debit transactions ({ratio:.0%}) are perfectly round amounts, "
            f"which is statistically unusual for legitimate spending."
        ),
        evidence=evidence,
        affected_count=len(rounds),
    )


def _rule_late_night(txns: list[dict]) -> Optional[RuleFinding]:
    """Flag transactions between midnight and 5 AM."""
    late = []
    for t in txns:
        time_str = t.get("time", "")
        if not time_str:
            continue
        try:
            hour = int(time_str.split(":")[0])
            if 0 <= hour < 5:
                late.append(t)
        except (ValueError, IndexError):
            continue
    if len(late) < 2:
        return None
    evidence = [
        f"{t.get('date','?')} {t.get('time','?')} | {t.get('merchant','?')} | ${abs(t.get('amount',0)):,.2f}"
        for t in late[:5]
    ]
    return RuleFinding(
        rule_id="late_night",
        title="Late-Night Transaction Activity",
        severity="MEDIUM",
        description=f"{len(late)} transactions occurred between midnight and 5 AM, which is atypical for legitimate account usage.",
        evidence=evidence,
        affected_count=len(late),
    )


def _rule_velocity_spike(txns: list[dict]) -> Optional[RuleFinding]:
    """Detect days with unusually high transaction volume."""
    daily: dict[str, list] = defaultdict(list)
    for t in txns:
        date = t.get("date", "")
        if date:
            daily[date[:10]].append(t) 

    if len(daily) < 3:
        return None

    counts = {d: len(ts) for d, ts in daily.items()}
    avg = sum(counts.values()) / len(counts)
    spikes = {d: c for d, c in counts.items() if c >= max(3, avg * 3)}

    if not spikes:
        return None
    evidence = [f"{d}: {c} transactions (avg={avg:.1f}/day)" for d, c in sorted(spikes.items())[:5]]
    return RuleFinding(
        rule_id="velocity_spike",
        title="Transaction Velocity Spike",
        severity="HIGH",
        description=f"On {len(spikes)} day(s), transaction count was 3x or more above the daily average of {avg:.1f}.",
        evidence=evidence,
        affected_count=len(spikes),
    )


def _rule_large_cash(txns: list[dict], threshold: float = 5000.0) -> Optional[RuleFinding]:
    """Flag large ATM or cash transactions."""
    cash_keywords = {"cash", "atm", "withdrawal", "currency"}
    hits = []
    for t in txns:
        if abs(t.get("amount") or 0) >= threshold:
            merchant = (t.get("merchant") or "").lower()
            if any(kw in merchant for kw in cash_keywords):
                hits.append(t)
    if not hits:
        return None
    total = sum(abs(t["amount"]) for t in hits)
    evidence = [
        f"{t.get('date','?')} | {t.get('merchant','?')} | ${abs(t['amount']):,.2f}"
        for t in hits[:5]
    ]
    return RuleFinding(
        rule_id="large_cash",
        title=f"Large Cash Transaction(s) Above ${threshold:,.0f}",
        severity="MEDIUM",
        description=f"{len(hits)} large cash transaction(s) detected, totaling ${total:,.2f}.",
        evidence=evidence,
        affected_count=len(hits),
        total_amount=total,
    )


def _rule_duplicate_transaction(txns: list[dict]) -> Optional[RuleFinding]:
    """Flag duplicate transactions: same amount + merchant within 24 hours."""
    duplicates = []
    seen: list[tuple[str, float, datetime]] = []

    for t in txns:
        amt = t.get("amount")
        merchant = (t.get("merchant") or "").lower().strip()
        date_str = t.get("date", "")
        dt = _parse_date(date_str)
        if amt is None or not merchant or dt is None:
            continue

        for prev_merchant, prev_amt, prev_dt in seen:
            if (abs(prev_amt - amt) < 0.01 and
                    prev_merchant == merchant and
                    abs((dt - prev_dt).total_seconds()) <= 86400):
                duplicates.append(
                    f"{date_str} | {t.get('merchant','?')} | ${abs(amt):,.2f} (duplicate)"
                )
                break
        seen.append((merchant, amt, dt))

    if len(duplicates) < 1:
        return None
    return RuleFinding(
        rule_id="duplicate_transaction",
        title="Duplicate Transaction(s) Detected",
        severity="HIGH",
        description=f"{len(duplicates)} potential duplicate transaction(s) found with same amount and merchant within 24 hours.",
        evidence=duplicates[:5],
        affected_count=len(duplicates),
    )


def _rule_negative_balance(txns: list[dict]) -> Optional[RuleFinding]:
    """Flag instances where balance goes negative."""
    negatives = [
        t for t in txns
        if t.get("balance") is not None and t["balance"] < 0
    ]
    if not negatives:
        return None
    evidence = [
        f"{t.get('date','?')} | Balance: ${t['balance']:,.2f}"
        for t in negatives[:5]
    ]
    return RuleFinding(
        rule_id="negative_balance",
        title="Negative Balance / Overdraft",
        severity="LOW",
        description=f"Account balance went negative on {len(negatives)} occasion(s).",
        evidence=evidence,
        affected_count=len(negatives),
    )


def _rule_international_wire(txns: list[dict]) -> Optional[RuleFinding]:
    """Flag international wire transfers and foreign transactions."""
    intl_keywords = {
        "wire", "international", "foreign", "swift", "iban",
        "remittance", "overseas", "transfer int", "wire transfer"
    }
    hits = [
        t for t in txns
        if any(kw in (t.get("merchant") or "").lower() for kw in intl_keywords)
    ]
    if not hits:
        return None
    total = sum(abs(t.get("amount") or 0) for t in hits)
    evidence = [
        f"{t.get('date','?')} | {t.get('merchant','?')} | ${abs(t.get('amount',0)):,.2f}"
        for t in hits[:5]
    ]
    return RuleFinding(
        rule_id="international_wire",
        title="International Wire / Foreign Transfers",
        severity="MEDIUM",
        description=f"{len(hits)} international or foreign transfer(s) detected, totaling ${total:,.2f}.",
        evidence=evidence,
        affected_count=len(hits),
        total_amount=total,
    )


def _rule_unusual_income(txns: list[dict]) -> Optional[RuleFinding]:
    """Flag multiple large credits clustered in a short window (possible income inflation)."""
    credits = [t for t in txns if (t.get("amount") or 0) > 2000]
    if len(credits) < 3:
        return None

    dated = []
    for t in credits:
        dt = _parse_date(t.get("date", ""))
        if dt:
            dated.append((dt, t))
    dated.sort(key=lambda x: x[0])

    clusters = []
    for i, (dt, t) in enumerate(dated):
        window = [x for x in dated if abs((x[0] - dt).days) <= 7]
        if len(window) >= 3:
            clusters.append(window)

    if not clusters:
        return None
    best = max(clusters, key=len)
    total = sum(t.get("amount", 0) for _, t in best)
    evidence = [
        f"{t.get('date','?')} | {t.get('merchant','?')} | +${t.get('amount',0):,.2f}"
        for _, t in best[:5]
    ]
    return RuleFinding(
        rule_id="unusual_income",
        title="Unusual Income / Large Credit Cluster",
        severity="MEDIUM",
        description=f"{len(best)} large credits (>${2000:,} each) clustered within 7 days, totaling ${total:,.2f}. May indicate income inflation.",
        evidence=evidence,
        affected_count=len(best),
        total_amount=total,
    )


SEVERITY_SCORE = {"HIGH": 15, "MEDIUM": 8, "LOW": 3}

class RuleEngine:
    """Runs all deterministic rules against a list of transactions."""

    def run(self, rows) -> RuleEngineResult:
        """
        Args:
            rows: List of TransactionRow (from Textract) or dicts (from CSV).
        Returns:
            RuleEngineResult with all triggered findings.
        """
        txns = _rows_to_transactions(rows)
        result = RuleEngineResult(transactions_analyzed=len(txns))

        rule_fns = [
            _rule_balance_mismatch,
            lambda t: _rule_structuring(t, 500, "sub-500"),
            lambda t: _rule_structuring(t, 3000, "sub-3k"),
            lambda t: _rule_structuring(t, 10000, "sub-10k"),
            _rule_round_numbers,
            _rule_late_night,
            _rule_velocity_spike,
            _rule_large_cash,
            _rule_duplicate_transaction,
            _rule_negative_balance,
            _rule_international_wire,
            _rule_unusual_income,
        ]

        for fn in rule_fns:
            try:
                finding = fn(txns)
                if finding:
                    result.findings.append(finding)
                    result.rules_triggered.append(finding.rule_id)
                    result.score_adjustment += SEVERITY_SCORE.get(finding.severity, 5)
            except Exception:
                pass  

        high = [f for f in result.findings if f.severity == "HIGH"]
        med  = [f for f in result.findings if f.severity == "MEDIUM"]
        if not result.findings:
            result.summary = "No rule-based violations detected."
        else:
            parts = []
            if high:
                parts.append(f"{len(high)} HIGH-severity finding(s): {', '.join(f.title for f in high[:3])}")
            if med:
                parts.append(f"{len(med)} MEDIUM-severity finding(s): {', '.join(f.title for f in med[:3])}")
            result.summary = ". ".join(parts) + "."

        return result

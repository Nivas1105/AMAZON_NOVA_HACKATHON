from __future__ import annotations
import base64
import json
import re
from typing import Optional
import boto3


MODEL_ID = "us.amazon.nova-2-lite-v1:0"

_FILTER_SUBS = [
    ("money laundering", "irregular fund movement"),
    ("laundering",       "irregular transfers"),
    ("fraud",            "policy violation"),
    ("fraudulent",       "non-compliant"),
    ("suspicious",       "flagged"),
    ("structuring",      "threshold-adjacent grouping"),
    ("criminal",         "regulatory concern"),
    ("illicit",          "irregular"),
    ("illegal",          "non-compliant"),
    ("drug",             "restricted"),
    ("terrorist",        "restricted entity"),
    ("AML",              "compliance review"),
]


def _sanitize(text: str) -> str:
    for trigger, replacement in _FILTER_SUBS:
        text = re.sub(re.escape(trigger), replacement, text, flags=re.IGNORECASE)
    return text

def _build_enriched_prompt(
    structured_text: str,
    rule_summary: str,
    rule_findings_json: str,
    file_type: str,
) -> str:
    return f"""You are a senior financial risk analyst and AML compliance expert.

A deterministic rule engine has already analyzed the following {file_type} and found the issues listed below.
Your job is to:
1. Validate and explain the rule findings in plain English
2. Identify additional risk patterns the rules may have missed
3. Provide an overall risk score and level
4. Give prioritized remediation recommendations

=== RULE ENGINE FINDINGS (Stage 2) ===
Summary: {rule_summary}

Findings detail:
{rule_findings_json}

=== RAW DOCUMENT DATA (Stage 1 / input) ===
{structured_text[:6000]}

=== YOUR TASK ===
Return your analysis as a single JSON object with EXACTLY this structure:

```json
{{
  "risk_score": <integer 0-100>,
  "risk_level": "<LOW | MEDIUM | HIGH | CRITICAL>",
  "rule_findings_validated": [
    {{"rule_id": "<id>", "confirmed": true/false, "explanation": "<plain English explanation>"}}
  ],
  "additional_fraud_flags": [
    {{"type": "<flag name>", "description": "<what was found>", "severity": "<LOW|MEDIUM|HIGH>"}}
  ],
  "anomalies": [
    {{"category": "<category>", "detail": "<detail>", "amount": "<if applicable>"}}
  ],
  "summary": "<2-3 sentence executive summary for a compliance officer>",
  "recommendations": ["<action 1>", "<action 2>", "<action 3>"],
  "audit_trail": [
    {{"stage": "<Stage 1 OCR|Stage 2 Rules|Stage 3 AI>", "finding": "<what was found>", "severity": "<level>"}}
  ],
  "transaction_stats": {{
    "total_transactions": <number or null>,
    "total_amount": "<formatted string or null>",
    "suspicious_count": <number>,
    "high_risk_categories": ["<cat1>", "<cat2>"]
  }}
}}
```

IMPORTANT:
- risk_score must incorporate the severity of rule engine findings
- audit_trail must include entries for EVERY rule finding confirmed above
- Respond with ONLY the JSON block. No text outside the JSON.
"""


def _build_image_prompt(rule_summary: str, rule_findings_json: str, file_type: str) -> str:
    return f"""You are a senior financial risk analyst and AML compliance expert.

I'm providing you with a bank statement image and findings from a deterministic rule engine that pre-analyzed this document.

=== RULE ENGINE PRE-ANALYSIS (Stage 2) ===
Summary: {rule_summary}
Findings: {rule_findings_json}

=== YOUR TASK ===
1. Read the bank statement image carefully
2. Validate the rule engine findings against what you see
3. Identify any additional risk patterns
4. Return your analysis as a single JSON object:

```json
{{
  "risk_score": <integer 0-100>,
  "risk_level": "<LOW | MEDIUM | HIGH | CRITICAL>",
  "rule_findings_validated": [
    {{"rule_id": "<id>", "confirmed": true/false, "explanation": "<explanation>"}}
  ],
  "additional_fraud_flags": [
    {{"type": "<flag name>", "description": "<description>", "severity": "<LOW|MEDIUM|HIGH>"}}
  ],
  "anomalies": [
    {{"category": "<category>", "detail": "<detail>", "amount": "<if applicable>"}}
  ],
  "summary": "<2-3 sentence executive summary>",
  "recommendations": ["<action 1>", "<action 2>", "<action 3>"],
  "audit_trail": [
    {{"stage": "<Stage>", "finding": "<finding>", "severity": "<level>"}}
  ],
  "transaction_stats": {{
    "total_transactions": <number or null>,
    "total_amount": "<formatted or null>",
    "suspicious_count": <number>,
    "high_risk_categories": ["<cat1>"]
  }}
}}
```

Respond with ONLY the JSON block.
"""


def _parse_nova_response(raw_text: str) -> dict:
    """Extract structured JSON from Nova's response."""
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", raw_text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    obj_match = re.search(r"\{[\s\S]+\}", raw_text)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass
    return {
        "risk_score": 50,
        "risk_level": "MEDIUM",
        "rule_findings_validated": [],
        "additional_fraud_flags": [],
        "anomalies": [],
        "summary": raw_text[:600],
        "recommendations": ["Manual review recommended."],
        "audit_trail": [],
        "transaction_stats": {"suspicious_count": 0, "high_risk_categories": []},
    }


# ── Main class ─────────────────────────────────────────────────────────────────

class NovaAnalyzer:
    """Stage 3: Calls Amazon Nova 2 Lite with structured evidence for reasoning."""

    def __init__(self, bedrock_client):
        self.bedrock = bedrock_client

    def analyze_text(
        self,
        structured_text: str,
        rule_summary: str,
        rule_findings: list[dict],
        file_type: str = "CSV transaction data",
    ) -> tuple[dict, str]:
        """
        Text-based analysis (CSV, or Textract-extracted text).
        Returns (parsed_dict, raw_nova_response).
        """
        rule_findings_json = json.dumps(rule_findings, indent=2)
        prompt = _build_enriched_prompt(
            structured_text, rule_summary, rule_findings_json, file_type
        )
        body = {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 2500, "temperature": 0.1},
        }
        response = self.bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(response["body"].read())["output"]["message"]["content"][0]["text"]
        return _parse_nova_response(raw), raw

    def analyze_image(
        self,
        image_bytes_list: list[bytes],
        rule_summary: str,
        rule_findings: list[dict],
        file_type: str = "bank statement image",
    ) -> tuple[dict, str]:
        """
        Multimodal image analysis: base64-encodes images + rule context.
        Returns (parsed_dict, raw_nova_response).
        """
        rule_findings_json = json.dumps(rule_findings, indent=2)
        content = []
        for img_bytes in image_bytes_list:
            content.append({
                "image": {
                    "format": "png",
                    "source": {"bytes": base64.b64encode(img_bytes).decode("utf-8")},
                }
            })
        content.append({"text": _build_image_prompt(rule_summary, rule_findings_json, file_type)})

        body = {
            "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {"maxTokens": 2500, "temperature": 0.1},
        }
        response = self.bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(response["body"].read())["output"]["message"]["content"][0]["text"]
        return _parse_nova_response(raw), raw

    def chat(self, message: str, context: str) -> str:
        """Follow-up Q&A — sanitizes context and user message before sending to Bedrock with Nova 2 Lite."""
        clean_context = _sanitize(context[:2000] if context else "Not available.")
        clean_message = _sanitize(message)

        full_message = (
            f"You are a financial compliance and audit assistant helping review bank statements "
            f"and transaction records. A 3-stage document review pipeline has produced the following "
            f"analysis summary:\n\n{clean_context}\n\n"
            f"Based on the analysis above, please answer the following question from the compliance "
            f"reviewer in a clear, professional manner. Focus on the transactions and patterns "
            f"identified in the analysis:\n\n"
            f"Question: {clean_message}"
        )
        body = {
            "messages": [{"role": "user", "content": [{"text": full_message}]}],
            "inferenceConfig": {"maxTokens": 1000, "temperature": 0.3},
        }
        response = self.bedrock.invoke_model(
            modelId=MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(response["body"].read())["output"]["message"]["content"][0]["text"]

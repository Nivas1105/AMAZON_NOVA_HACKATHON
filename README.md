# Nova Risk Analyst — AI Financial Risk Detection
### AWS Nova Hackathon 2025 · Multimodal Understanding

> Upload bank statements (PDF/image) or transaction files (CSV) and get instant AI-powered fraud detection, risk scoring, and actionable recommendations — powered by **Amazon Nova 2 Lite** on AWS Bedrock.

---

## 🏆 What It Does

A **3-stage AI pipeline** that analyzes financial documents for fraud, anomalies, and compliance violations:

```
Stage 1: OCR / Parsing  →  Stage 2: Rule Engine  →  Stage 3: Amazon Nova 2 Lite
```

1. **Textract OCR** extracts structured tables from PDFs and images (with confidence scores)
2. **12 Deterministic Rules** check for structuring, velocity spikes, duplicates, and more
3. **Amazon Nova 2 Lite** reasons over the evidence, scores risk, and explains findings in plain English

---

## ✨ Features

| Feature | Detail |
|---------|--------|
| 📊 **Multimodal Analysis** | CSV, PDF, PNG, JPG, WEBP — all supported |
| 🚨 **12 Fraud Rules** | Structuring, velocity spikes, duplicates, international wires, and more |
| 📈 **Risk Scoring** | 0–100 score with LOW / MEDIUM / HIGH / CRITICAL classification |
| 💬 **Chat Interface** | Follow-up Q&A powered by Nova 2 Lite |
| ▶ **Demo Mode** | One-click demo dashboard — no file upload or AWS keys needed |
| 📋 **Export** | Copy summary to clipboard or download full JSON results |
| ⚡ **< 5 Second Analysis** | Results in seconds, not hours |

---

## 🚀 Quick Start

### Option A — Demo Mode (no AWS credentials needed)

```bash
cd "AWS NOVA HACKATHON"
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# Open http://localhost:8000 → click "Try Live Demo"
```

### Option B — Full Live Analysis (requires AWS)

```bash
cd "AWS NOVA HACKATHON"
bash start.sh          # Installs deps, sets up .env, launches server
```

Then edit `.env` with your AWS credentials and restart.

---

## Prerequisites

- Python 3.10+
- AWS account with **Amazon Bedrock** enabled (for live analysis)
- Nova 2 Lite model access (`us.amazon.nova-2-lite-v1:0`) in `us-east-1`
- `poppler` installed (for PDF support): `brew install poppler`

---

## Setup (Manual)

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure AWS credentials
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Generate sample test data
```bash
python generate_sample_data.py
```

### 4. Start the backend
```bash
uvicorn app:app --reload --port 8000
```

### 5. Open the app
Navigate to `http://localhost:8000`

---

## 📖 How to Use

### Analyzing Documents (PNG and CSV)
1. **Launch the App:** Run the backend following the steps above and open the UI in your browser.
2. **Upload Your Data:**
   - **For Bank Statements (PNG/Image):** Locate your `sample_statement.png` or any other statement image. Drag it directly into the dashed "Drop Zone" on the dashboard, or click "Browse" to select it.
   - **For Transaction Exports (CSV):** Locate your `sample_transactions.csv`. Drag it into the same zone.
3. **Run Pipeline:** Once the file is selected, click the **"Analyze with Amazon Nova 2 Lite"** button. 
   - If it's a **PNG/PDF**, the system will run Stage 1 (Textract) first.
   - If it's a **CSV**, it skips straight to Stage 2 (Rule Engine).
4. **Review Results:** Inspect the Risk Score, the Executive Summary, and the specific findings flagged by the AI.
5. **Interactive Chat:** Use the chat drawer to ask specific follow-up questions about the transactions.

### Included Sample Files
- `sample_statement.png`: Use this to test the full 3-stage multimodal pipeline.
- `sample_transactions.csv`: Use this to test high-speed rule-based analysis.


---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (SPA)                    │
│   index.html · style.css · app.js · Chart.js        │
└────────────────────┬────────────────────────────────┘
                     │  HTTP / REST
┌────────────────────▼────────────────────────────────┐
│              FastAPI Backend  (app.py)               │
│                                                     │
│  POST /analyze          POST /demo    POST /chat    │
│                                                     │
│  ┌──────────┐  ┌───────────────┐  ┌─────────────┐  │
│  │ Stage 1  │  │   Stage 2     │  │   Stage 3   │  │
│  │ Textract │→ │  Rule Engine  │→ │  Nova 2 Lite │  │
│  │  OCR     │  │  (12 rules)   │  │  (Bedrock)  │  │
│  └──────────┘  └───────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────┘
         │                                   │
         ▼                                   ▼
  Amazon Textract                    Amazon Bedrock
  (table extraction)               (Nova 2 Lite inference)
```

---

## 📋 The 12 Risk Rules

| Rule | Severity | Description |
|------|----------|-------------|
| `balance_mismatch` | HIGH | Computed balance ≠ stated balance |
| `structuring_10000` | HIGH | Transactions clustered just below $10,000 |
| `structuring_3000` | HIGH | Transactions clustered just below $3,000 |
| `structuring_500` | HIGH | Transactions clustered just below $500 |
| `velocity_spike` | HIGH | Day with > 3× average daily transaction count |
| `duplicate_transaction` | HIGH | Same amount + merchant within 24 hours |
| `international_wire` | MEDIUM | Foreign / international wire transfers |
| `round_numbers` | MEDIUM | > 30% of transactions are round amounts |
| `late_night` | MEDIUM | Transactions between 00:00–05:00 |
| `large_cash` | MEDIUM | Single cash/ATM transaction > $5,000 |
| `unusual_income` | MEDIUM | Multiple large credits in a short window |
| `negative_balance` | LOW | Running balance goes below $0 |

---

## 📁 Project Structure

```
AWS NOVA HACKATHON/
├── app.py                    # FastAPI backend — 3-stage pipeline
├── start.sh                  # One-command start script
├── requirements.txt          # Pinned Python dependencies
├── .env.example              # AWS credentials template
├── generate_sample_data.py   # Synthetic test data generator
├── README.md
├── pipeline/
│   ├── nova_analyzer.py      # Stage 3: Nova 2 Lite AI reasoning
│   ├── rules.py              # Stage 2: 12 deterministic fraud rules
│   └── textract_ocr.py       # Stage 1: Amazon Textract OCR
└── frontend/
    ├── index.html            # Single-page application
    ├── style.css             # Dark-mode premium design
    └── app.js                # Upload, dashboard, chart, chat, export
```

---

## AWS Services Used

| Service | Usage |
|---------|-------|
| Amazon Bedrock | Managed AI inference platform |
| Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`) | Multimodal document analysis + chat |
| Amazon Textract | Structured OCR for PDFs and images |

---

## 🎯 Built For

**AWS Nova Hackathon 2025** · Category: **Multimodal Understanding**

#AmazonNova #AWSBedrock #Hackathon2025

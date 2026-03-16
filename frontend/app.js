/* ── Config ──────────────────────────────────────────── */
const API_BASE = "http://localhost:8000";

/* ── State ───────────────────────────────────────────── */
let selectedFile = null;
let analysisContext = "";
let lastResultData = null;
let isDemoMode = false;
let riskChartInstance = null;

/* ── DOM refs ────────────────────────────────────────── */
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const dzLink = document.getElementById("dzLink");
const selectedFileEl = document.getElementById("selectedFile");
const fileNameEl = document.getElementById("fileName");
const fileSizeEl = document.getElementById("fileSize");
const fileIconEl = document.getElementById("fileIcon");
const removeBtn = document.getElementById("removeFile");
const analyzeBtn = document.getElementById("analyzeBtn");
const analyzeBtnText = document.getElementById("analyzeBtnText");
const demoBtn = document.getElementById("demoBtn");
const demoModePill = document.getElementById("demoModePill");
const demoNotice = document.getElementById("demoNotice");
const loadingBar = document.getElementById("loadingBar");
const loadingFill = document.getElementById("loadingFill");
const loadingMsg = document.getElementById("loadingMsg");
const loadingText = document.getElementById("loadingText");
const resultsSection = document.getElementById("resultsSection");
const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const chatSend = document.getElementById("chatSend");
const chatSuggestions = document.getElementById("chatSuggestions");
const btnCopy = document.getElementById("btnCopy");
const btnDownload = document.getElementById("btnDownload");
const chatFab = document.getElementById("chatFab");
const chatDrawer = document.getElementById("chatDrawer");
const chatClose = document.getElementById("chatClose");
const chatOverlay = document.getElementById("chatOverlay");

/* ── Pipeline loading messages (3 stages) ─────────────── */
const PIPELINE_STAGES = [
  { msg: "Stage 1: OCR / Parsing your document…", pct: 15 },
  { msg: "Stage 1: Extracting text and tables…", pct: 30 },
  { msg: "Stage 2: Running deterministic rule checks…", pct: 45 },
  { msg: "Stage 2: Checking structuring, duplicates…", pct: 58 },
  { msg: "Stage 3: Amazon Nova 2 Lite reasoning…", pct: 72 },
  { msg: "Stage 3: Generating risk explanation…", pct: 88 },
  { msg: "Compiling your risk dashboard…", pct: 95 },
];

/* ── Utilities ───────────────────────────────────────── */
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}
function fileIconFor(name) {
  const ext = name.split(".").pop().toLowerCase();
  if (ext === "csv") return "📊";
  if (ext === "pdf") return "📄";
  return "🖼️";
}
function showToast(msg, type = "error") {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 5000);
}
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* ── File Handling ───────────────────────────────────── */
function handleFile(file) {
  const allowed = ["csv", "pdf", "png", "jpg", "jpeg", "webp"];
  const ext = file.name.split(".").pop().toLowerCase();
  if (!allowed.includes(ext)) { showToast(`Unsupported file type: .${ext}`); return; }
  if (file.size > 20 * 1024 * 1024) { showToast("File too large. Max 20 MB."); return; }
  selectedFile = file;
  fileNameEl.textContent = file.name;
  fileSizeEl.textContent = fmtSize(file.size);
  fileIconEl.textContent = fileIconFor(file.name);
  selectedFileEl.style.display = "flex";
  analyzeBtn.disabled = false;
}

dzLink.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault(); dropZone.classList.remove("drag-over");
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => { if (fileInput.files[0]) handleFile(fileInput.files[0]); });
removeBtn.addEventListener("click", () => {
  selectedFile = null; fileInput.value = "";
  selectedFileEl.style.display = "none";
  analyzeBtn.disabled = true;
  resultsSection.style.display = "none";
});

/* ── Demo Mode ───────────────────────────────────────── */
demoBtn.addEventListener("click", runDemo);

async function runDemo() {
  isDemoMode = true;
  demoBtn.disabled = true;
  demoBtn.textContent = "Loading demo…";
  loadingBar.style.display = "block";
  loadingMsg.style.display = "flex";
  loadingFill.style.width = "40%";
  loadingText.textContent = "Loading demo dataset…";
  resultsSection.style.display = "none";

  try {
    loadingFill.style.width = "80%";
    const res = await fetch(`${API_BASE}/demo`, { method: "POST" });
    loadingFill.style.width = "100%";
    if (!res.ok) throw new Error("Demo failed");
    const data = await res.json();
    analysisContext = JSON.stringify({
      risk_score: data.risk_score,
      risk_level: data.risk_level,
      summary: data.summary,
      rule_findings: data.rule_findings,
      anomalies: data.anomalies,
      transaction_stats: data.transaction_stats,
      recommendations: data.recommendations,
    });
    lastResultData = data;
    demoModePill.style.display = "inline-block";
    demoNotice.style.display = "block";
    renderDashboard(data);
  } catch (err) {
    showToast("⚠️ Could not load demo. Make sure the server is running.");
  } finally {
    setTimeout(() => {
      loadingBar.style.display = "none";
      loadingMsg.style.display = "none";
      loadingFill.style.width = "0%";
    }, 400);
    demoBtn.disabled = false;
    demoBtn.innerHTML = "<span>▶</span><span>Try Live Demo — No File Needed</span><span class=\"demo-badge\">instant</span>";
  }
}

/* ── Analysis ────────────────────────────────────────── */
analyzeBtn.addEventListener("click", runAnalysis);

async function runAnalysis() {
  if (!selectedFile) return;
  isDemoMode = false;
  demoModePill.style.display = "none";
  demoNotice.style.display = "none";

  analyzeBtn.disabled = true;
  analyzeBtnText.textContent = "Running 3-Stage Pipeline…";
  loadingBar.style.display = "block";
  loadingMsg.style.display = "flex";
  resultsSection.style.display = "none";

  // Cycle through pipeline stage messages
  let stageIdx = 0;
  loadingText.textContent = PIPELINE_STAGES[0].msg;
  loadingFill.style.width = PIPELINE_STAGES[0].pct + "%";

  const stageTimer = setInterval(() => {
    stageIdx = Math.min(stageIdx + 1, PIPELINE_STAGES.length - 1);
    loadingText.textContent = PIPELINE_STAGES[stageIdx].msg;
    loadingFill.style.width = PIPELINE_STAGES[stageIdx].pct + "%";
  }, 1800);

  try {
    const form = new FormData();
    form.append("file", selectedFile);
    const res = await fetch(`${API_BASE}/analyze`, { method: "POST", body: form });
    clearInterval(stageTimer);
    loadingFill.style.width = "100%";

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Pipeline failed");
    }

    const data = await res.json();
    // Use structured fields as context (not raw_analysis which contains filter-triggering terms)
    analysisContext = JSON.stringify({
      risk_score: data.risk_score,
      risk_level: data.risk_level,
      summary: data.summary,
      rule_findings: data.rule_findings,
      anomalies: data.anomalies,
      additional_fraud_flags: data.additional_fraud_flags,
      transaction_stats: data.transaction_stats,
      recommendations: data.recommendations,
      audit_trail: data.audit_trail,
    });
    lastResultData = data;
    renderDashboard(data);

  } catch (err) {
    clearInterval(stageTimer);
    showToast(`❌ ${err.message}`);
  } finally {
    setTimeout(() => {
      loadingBar.style.display = "none";
      loadingMsg.style.display = "none";
      loadingFill.style.width = "0%";
    }, 400);
    analyzeBtn.disabled = false;
    analyzeBtnText.textContent = "Analyze with Amazon Nova 2 Lite";
  }
}

/* ── Dashboard Rendering ─────────────────────────────── */
function renderDashboard(data) {
  const score = Math.min(100, Math.max(0, Number(data.risk_score) || 0));
  const level = (data.risk_level || "MEDIUM").toUpperCase();

  // Gauge
  animateGauge(score, level);

  // Summary
  document.getElementById("riskSummary").textContent = data.summary || "No summary available.";

  // Transaction stats chips
  const txnStats = document.getElementById("txnStats");
  txnStats.innerHTML = "";
  const stats = data.transaction_stats || {};
  if (stats.total_transactions) addChip(txnStats, `${stats.total_transactions} Transactions`);
  if (stats.total_amount) addChip(txnStats, `Total: ${stats.total_amount}`);
  if (stats.suspicious_count != null) addChip(txnStats, `${stats.suspicious_count} Suspicious`);
  (stats.high_risk_categories || []).slice(0, 3).forEach(c => addChip(txnStats, c));

  // Pipeline metadata chip
  const pipeline = data.pipeline || {};
  if (pipeline.textract_confidence != null) {
    addChip(txnStats, `OCR Confidence: ${pipeline.textract_confidence}%`);
  }
  if (pipeline.textract_rows_extracted != null) {
    addChip(txnStats, `${pipeline.textract_rows_extracted} Rows Extracted`);
  }

  // Fraud Flags (AI-detected)
  const flagList = document.getElementById("fraudFlags");
  const aiFraudFlags = (data.additional_fraud_flags || []).concat(
    (data.fraud_flags || []).filter(f => !f.source || f.source !== "rule_engine")
  );
  document.getElementById("fraudCount").textContent = aiFraudFlags.length;
  flagList.innerHTML = "";
  if (aiFraudFlags.length === 0) {
    flagList.innerHTML = `<div class="empty-state">✅ Nova AI found no additional fraud patterns</div>`;
  } else {
    aiFraudFlags.forEach(f => {
      const sev = (f.severity || "medium").toLowerCase();
      const li = document.createElement("li");
      li.className = `flag-item ${sev}`;
      li.innerHTML = `<div class="flag-type">${escHtml(f.type || "Unknown")}</div><div class="flag-desc">${escHtml(f.description || "")}</div>`;
      flagList.appendChild(li);
    });
  }

  // Anomalies
  const anomList = document.getElementById("anomaliesList");
  const anomalies = Array.isArray(data.anomalies) ? data.anomalies : [];
  document.getElementById("anomalyCount").textContent = anomalies.length;
  anomList.innerHTML = "";
  if (anomalies.length === 0) {
    anomList.innerHTML = `<div class="empty-state">✅ Amazon Nova 2 Lite detected no anomalies</div>`;
  } else {
    anomalies.forEach(a => {
      const li = document.createElement("li");
      li.className = "flag-item medium";
      const detail = a.amount ? `${a.detail || ""} · ${a.amount}` : (a.detail || "");
      li.innerHTML = `<div class="flag-type">${escHtml(a.category || "Anomaly")}</div><div class="flag-desc">${escHtml(detail)}</div>`;
      anomList.appendChild(li);
    });
  }

  // Risk chart
  renderRiskChart(data);

  // Rule Engine Findings
  renderRuleFindings(data.rule_findings || {});

  // Audit Trail
  renderAuditTrail(data.audit_trail || []);

  // Recommendations
  const recsList = document.getElementById("recsList");
  recsList.innerHTML = "";
  (data.recommendations || []).forEach((rec, i) => {
    const li = document.createElement("li");
    li.className = "rec-item";
    li.innerHTML = `<div class="rec-num">${i + 1}</div><div class="rec-text">${escHtml(rec)}</div>`;
    recsList.appendChild(li);
  });

  // Show section & scroll
  resultsSection.style.display = "block";
  setTimeout(() => resultsSection.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
  resetChat();
  // Show the Ask Nova button in the navbar
  chatFab.style.display = "flex";

  // Nova reasoning block
  const reasoningDetails = document.getElementById("novaReasoningDetails");
  const reasoningPre = document.getElementById("novaReasoningPre");
  const rawText = data.raw_analysis || "";
  if (rawText && rawText !== "Demo mode — pre-baked result. Upload a real file to run the live 3-stage pipeline.") {
    // Try to pretty-print if it's JSON
    let display = rawText;
    try {
      const parsed = JSON.parse(rawText.replace(/```json|```/g, "").trim());
      display = JSON.stringify(parsed, null, 2);
    } catch (_) { /* not JSON, show as-is */ }
    reasoningPre.textContent = display;
    reasoningDetails.style.display = "block";
  } else {
    reasoningDetails.style.display = "none";
  }
}

/* ── Risk Chart ──────────────────────────────────────── */
function renderRiskChart(data) {
  const chartCard = document.getElementById("chartCard");
  const findings = (data.rule_findings && data.rule_findings.findings) || [];
  if (findings.length === 0) { chartCard.style.display = "none"; return; }
  chartCard.style.display = "block";

  const labels = findings.map(f => f.title.length > 28 ? f.title.slice(0, 28) + "…" : f.title);
  const severityColor = { HIGH: "#ef4444", MEDIUM: "#f59e0b", LOW: "#22c55e" };
  const colors = findings.map(f => severityColor[f.severity] || "#6366f1");

  if (riskChartInstance) riskChartInstance.destroy();
  const ctx = document.getElementById("riskChart").getContext("2d");
  riskChartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Affected Transactions",
        data: findings.map(f => f.affected_count || 1),
        backgroundColor: colors.map(c => c + "33"),
        borderColor: colors,
        borderWidth: 2,
        borderRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const f = findings[ctx.dataIndex];
              return f.total_amount ? `Total: ${f.total_amount}` : "";
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: "#94a3b8", font: { size: 11 } },
          grid: { color: "rgba(99,179,237,0.06)" },
        },
        y: {
          ticks: { color: "#94a3b8", stepSize: 1 },
          grid: { color: "rgba(99,179,237,0.06)" },
        },
      },
    },
  });
}

/* ── Rule Findings ───────────────────────────────────── */
function renderRuleFindings(ruleData) {
  const card = document.getElementById("ruleFindingsCard");
  const list = document.getElementById("ruleFindingsList");
  const findings = ruleData.findings || [];
  document.getElementById("ruleCount").textContent = findings.length;

  if (findings.length === 0) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";
  list.innerHTML = "";

  findings.forEach(f => {
    const sev = (f.severity || "MEDIUM").toLowerCase();
    const div = document.createElement("div");
    div.className = `rule-finding-item ${sev}`;
    const evidenceHtml = (f.evidence || []).length
      ? `<div class="rule-evidence">${f.evidence.map(e => `<div class="ev-line">• ${escHtml(e)}</div>`).join("")}</div>`
      : "";
    div.innerHTML = `
      <div class="rule-finding-header">
        <span class="rule-title">${escHtml(f.title || f.rule_id)}</span>
        <span class="rule-sev-badge ${sev}">${f.severity}</span>
        ${f.affected_count ? `<span class="rule-count-badge">${f.affected_count} affected</span>` : ""}
        ${f.total_amount ? `<span class="rule-amount-badge">${escHtml(f.total_amount)}</span>` : ""}
      </div>
      <div class="rule-desc">${escHtml(f.description || "")}</div>
      ${evidenceHtml}`;
    list.appendChild(div);
  });
}

/* ── Audit Trail ─────────────────────────────────────── */
function renderAuditTrail(trail) {
  const card = document.getElementById("auditCard");
  const container = document.getElementById("auditTrail");
  if (!trail || trail.length === 0) { card.style.display = "none"; return; }
  card.style.display = "block";
  container.innerHTML = "";
  trail.forEach(entry => {
    const sev = (entry.severity || "LOW").toLowerCase();
    const div = document.createElement("div");
    div.className = "audit-entry";
    div.innerHTML = `
      <span class="audit-stage">${escHtml(entry.stage || "")}</span>
      <span class="audit-finding">${escHtml(entry.finding || "")}</span>
      <span class="audit-sev ${sev}">${entry.severity || ""}</span>`;
    container.appendChild(div);
  });
}

/* ── Helper chips ────────────────────────────────────── */
function addChip(container, label) {
  const chip = document.createElement("span");
  chip.className = "stat-chip";
  chip.textContent = label;
  container.appendChild(chip);
}

/* ── Risk Gauge Animation ────────────────────────────── */
function animateGauge(target, level) {
  const arc = document.getElementById("gaugeFill");
  const numEl = document.getElementById("gaugeNum");
  const badge = document.getElementById("rsBadge");
  const total = 251;
  let current = 0;
  const step = target / 60;
  const colors = { LOW: "#22c55e", MEDIUM: "#f59e0b", HIGH: "#ef4444", CRITICAL: "#dc2626" };

  arc.style.stroke = colors[level] || colors.MEDIUM;
  const timer = setInterval(() => {
    current = Math.min(current + step, target);
    arc.setAttribute("stroke-dashoffset", (total - (current / 100) * total).toFixed(1));
    numEl.textContent = Math.round(current);
    if (current >= target) clearInterval(timer);
  }, 16);

  badge.textContent = level;
  badge.className = `rs-badge ${level.toLowerCase()}`;
}

/* ── Export Buttons ──────────────────────────────────── */
btnCopy.addEventListener("click", () => {
  if (!lastResultData) return;
  const d = lastResultData;
  const lines = [
    `Nova Risk Analyst — Analysis Report`,
    `${"─".repeat(48)}`,
    `Risk Score : ${d.risk_score}/100  [${d.risk_level}]`,
    ``,
    `EXECUTIVE SUMMARY`,
    d.summary,
    ``,
    `FRAUD FLAGS (${(d.fraud_flags || []).length})`,
    ...(d.fraud_flags || []).map(f => `  • [${f.severity}] ${f.type}: ${f.description}`),
    ``,
    `RECOMMENDATIONS`,
    ...(d.recommendations || []).map((r, i) => `  ${i + 1}. ${r}`),
  ];
  navigator.clipboard.writeText(lines.join("\n"))
    .then(() => showToast("✅ Summary copied to clipboard!", "success"))
    .catch(() => showToast("❌ Copy failed — please copy manually."));
});

btnDownload.addEventListener("click", () => {
  if (!lastResultData) return;
  const json = JSON.stringify(lastResultData, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `nova-risk-analysis-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  showToast("✅ JSON downloaded!", "success");
});

/* ── Chat ────────────────────────────────────────────── */
function resetChat() {
  chatMessages.innerHTML = `
    <div class="chat-msg assistant">
      <div class="msg-avatar">N</div>
      <div class="msg-bubble">
        I've analyzed your document using a 3-stage pipeline (OCR → Rule Engine → Amazon Nova 2 Lite AI). Ask me anything — suspicious transactions, structuring patterns, regulatory actions, or anything else.
      </div>
    </div>`;
  chatSuggestions.style.display = "flex";
}

function appendMsg(role, text) {
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  div.innerHTML = `<div class="msg-avatar">${role === "assistant" ? "N" : "U"}</div><div class="msg-bubble">${escHtml(text)}</div>`;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement("div");
  div.className = "chat-msg assistant chat-typing"; div.id = "typingIndicator";
  div.innerHTML = `<div class="msg-avatar">N</div><div class="msg-bubble"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>`;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}
function removeTyping() { const t = document.getElementById("typingIndicator"); if (t) t.remove(); }

async function sendChatMessage(msg) {
  if (!msg.trim()) return;
  chatInput.value = "";
  chatSuggestions.style.display = "none";
  appendMsg("user", msg);
  showTyping();
  chatSend.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg, context: analysisContext }),
    });
    const data = await res.json();
    removeTyping();
    if (!res.ok) throw new Error(data.detail || "Chat failed");
    appendMsg("assistant", data.answer);
  } catch (err) {
    removeTyping();
    appendMsg("assistant", `⚠️ ${err.message}`);
  } finally {
    chatSend.disabled = false;
  }
}

chatSend.addEventListener("click", () => sendChatMessage(chatInput.value));
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(chatInput.value); }
});
chatSuggestions.querySelectorAll(".suggestion").forEach(btn => {
  btn.addEventListener("click", () => sendChatMessage(btn.dataset.q));
});

/* ── Chat Drawer open / close ─────────────────────────── */
function openChatDrawer() {
  chatDrawer.classList.add("open");
  document.body.style.overflow = "hidden";
  setTimeout(() => chatInput.focus(), 350);
}
function closeChatDrawer() {
  chatDrawer.classList.remove("open");
  document.body.style.overflow = "";
}
// Wire both the pill and circle button to open the chat drawer
document.getElementById("novaChatPill").addEventListener("click", openChatDrawer);
document.getElementById("novaChatCircle").addEventListener("click", openChatDrawer);
chatClose.addEventListener("click", closeChatDrawer);
chatOverlay.addEventListener("click", closeChatDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && chatDrawer.classList.contains("open")) closeChatDrawer();
});

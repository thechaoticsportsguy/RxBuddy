/**
 * AnswerCard — 5-section answer layout
 *
 * Renders one card per medication query in this exact order:
 *   1. answer  — primary one-sentence answer
 *   2. warning — safety callout (hidden when empty / SAFE)
 *   3. details — clinical fact bullets
 *   4. action  — what to do bullets
 *   5. article — mini explanation paragraph
 *
 * Plus: verdict banner (top), citations (bottom), confidence pill, JSON-LD.
 */

import Head from "next/head";

// ── Verdict config ─────────────────────────────────────────────────────────────

const VERDICT_CONFIG = {
  SAFE: {
    bg:     "bg-green-50",
    border: "border-green-300",
    badge:  "bg-green-100 text-green-800 border border-green-300",
    icon:   "✓",
    iconBg: "bg-green-500",
    label:  "SAFE",
    aria:   "Safe to use",
  },
  CAUTION: {
    bg:     "bg-yellow-50",
    border: "border-yellow-300",
    badge:  "bg-yellow-100 text-yellow-800 border border-yellow-300",
    icon:   "!",
    iconBg: "bg-yellow-500",
    label:  "CAUTION",
    aria:   "Use with caution",
  },
  AVOID: {
    bg:     "bg-red-50",
    border: "border-red-300",
    badge:  "bg-red-100 text-red-800 border border-red-300",
    icon:   "✕",
    iconBg: "bg-red-600",
    label:  "AVOID",
    aria:   "Avoid this combination",
  },
  CONSULT_PHARMACIST: {
    bg:     "bg-blue-50",
    border: "border-blue-300",
    badge:  "bg-blue-100 text-blue-800 border border-blue-300",
    icon:   "?",
    iconBg: "bg-blue-500",
    label:  "CONSULT A HEALTHCARE PROVIDER",
    aria:   "Consult a healthcare provider",
  },
  INSUFFICIENT_DATA: {
    bg:     "bg-slate-50",
    border: "border-slate-300",
    badge:  "bg-slate-100 text-slate-700 border border-slate-300",
    icon:   "–",
    iconBg: "bg-slate-400",
    label:  "DATA UNAVAILABLE",
    aria:   "Insufficient data — consult a specialist",
  },
  EMERGENCY: {
    bg:     "bg-red-100",
    border: "border-red-500",
    badge:  "bg-red-600 text-white border border-red-700",
    icon:   "!",
    iconBg: "bg-red-700",
    label:  "EMERGENCY — CALL 911",
    aria:   "Medical emergency — call emergency services immediately",
  },
};

const DEFAULT_VERDICT = VERDICT_CONFIG.CONSULT_PHARMACIST;

// ── Helpers ────────────────────────────────────────────────────────────────────

function splitPipe(str) {
  if (!str) return [];
  return str.split("|").map((s) => s.trim()).filter(Boolean);
}

/**
 * Pull the 5 sections from structured fields, falling back to legacy fields,
 * then to raw-text parsing for old DB rows.
 */
function parseSections(result) {
  const s = result.structured || {};
  const raw = result.answer || "";

  // ── New-format fields ────────────────────────────────────────────────────────
  if (s.answer || s.details?.length || s.action?.length || s.article) {
    return {
      answer:  s.answer  || s.direct || "",
      warning: s.warning || "",
      details: s.details?.length ? s.details : [],
      action:  s.action?.length  ? s.action  : (s.do?.length ? s.do : []),
      article: s.article || s.why || "",
    };
  }

  // ── Legacy structured fields (old DB rows) ───────────────────────────────────
  if (s.direct || s.do?.length) {
    // Build details from avoid + doctor bullets (closest match to "clinical facts")
    const details = [...(s.avoid || []), ...(s.doctor || [])];
    return {
      answer:  s.direct || "",
      warning: details.length ? details[0] : "",
      details: details.slice(1),
      action:  s.do || [],
      article: s.why || "",
    };
  }

  // ── Raw text fallback for plain-text DB answers ──────────────────────────────
  const lines = raw.split("\n");
  const out = { answer: "", warning: "", details: [], action: [], article: "" };
  for (const line of lines) {
    const upper = line.trim().toUpperCase();
    if (upper.startsWith("ANSWER:") || upper.startsWith("DIRECT:"))
      out.answer = line.slice(line.indexOf(":") + 1).trim();
    else if (upper.startsWith("WARNING:"))
      out.warning = line.slice(line.indexOf(":") + 1).trim();
    else if (upper.startsWith("DETAILS:"))
      out.details = splitPipe(line.slice(line.indexOf(":") + 1));
    else if (upper.startsWith("ACTION:") || upper.startsWith("DO:"))
      out.action = splitPipe(line.slice(line.indexOf(":") + 1));
    else if (upper.startsWith("ARTICLE:") || upper.startsWith("WHY:"))
      out.article = line.slice(line.indexOf(":") + 1).trim();
  }
  // Last resort: use first sentence of raw text as the answer
  if (!out.answer && raw) {
    const first = raw.split(/[.!?]/)[0];
    out.answer = first ? first.trim() + "." : raw.slice(0, 200);
  }
  return out;
}

// ── JSON-LD builder ────────────────────────────────────────────────────────────

function buildJsonLd(query, result, verdict) {
  const stop = new Set(["can", "i", "take", "with", "and", "the", "a", "is", "are", "while", "during", "for", "my"]);
  const drugNames = query
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 3 && !stop.has(w))
    .slice(0, 3);

  const s = result?.structured || {};
  const answerText = s.answer || s.direct || result?.answer?.slice(0, 500) || "";

  return {
    "@context": "https://schema.org",
    "@type": "Drug",
    name: drugNames[0] || query,
    alternateName: drugNames.slice(1),
    description: answerText.slice(0, 200),
    warning: verdict === "AVOID" || verdict === "CAUTION" ? answerText : undefined,
    mainEntity: {
      "@type": "Question",
      name: query,
      acceptedAnswer: {
        "@type": "Answer",
        text: answerText,
        url: typeof window !== "undefined" ? window.location.href : "",
        dateCreated: new Date().toISOString().slice(0, 10),
      },
    },
  };
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function VerdictBanner({ config }) {
  return (
    <div
      className={`flex items-center gap-3 rounded-t-xl px-5 py-4 ${config.bg} border-b ${config.border}`}
      role="banner"
      aria-label={config.aria}
    >
      <div
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${config.iconBg} text-white font-bold text-lg`}
        aria-hidden="true"
      >
        {config.icon}
      </div>
      <span
        className={`rounded-full px-3 py-1 text-xs font-bold tracking-wider uppercase ${config.badge}`}
        role="status"
      >
        {config.label}
      </span>
    </div>
  );
}

/** Section 1 — Primary answer sentence */
function PrimaryAnswer({ text }) {
  if (!text) return null;
  return (
    <p className="text-base font-semibold text-slate-900 leading-snug">
      {text}
    </p>
  );
}

/** Section 2 — Warning callout */
function WarningBox({ text, verdict }) {
  if (!text) return null;
  const isEmergency = verdict === "EMERGENCY";
  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        isEmergency
          ? "border-red-400 bg-red-100"
          : "border-yellow-300 bg-yellow-50"
      }`}
      role="alert"
      aria-live={isEmergency ? "assertive" : "polite"}
    >
      <p className={`text-sm font-semibold ${isEmergency ? "text-red-800" : "text-yellow-800"}`}>
        {isEmergency ? "⚠ " : ""}
        {text}
      </p>
      {isEmergency && (
        <p className="mt-1 text-sm text-red-700">
          Poison Control (US): <strong>1-800-222-1222</strong>
        </p>
      )}
    </div>
  );
}

/** Sections 3 & 4 — Bullet lists */
function BulletList({ title, items, colorClass = "text-slate-700", dotClass = "bg-slate-400" }) {
  if (!items?.length) return null;
  const headingId = `section-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <section aria-labelledby={headingId}>
      <h3
        id={headingId}
        className={`mb-1.5 text-xs font-bold uppercase tracking-wider ${colorClass}`}
      >
        {title}
      </h3>
      <ul className="space-y-1" role="list">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
            <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dotClass}`} aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Section 5 — Mini article */
function MiniArticle({ text }) {
  if (!text) return null;
  return (
    <p className="text-sm text-slate-600 leading-relaxed border-t border-slate-100 pt-3">
      {text}
    </p>
  );
}

function CitationList({ citations }) {
  if (!citations?.length) return null;
  return (
    <section aria-labelledby="citations-heading" className="mt-3 border-t border-slate-100 pt-3">
      <h3
        id="citations-heading"
        className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-500"
      >
        Sources
      </h3>
      <ul className="space-y-1" role="list">
        {citations.map((cit, i) => (
          <li key={cit.id || i} className="text-xs text-slate-500">
            <a
              href={cit.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-emerald-600 hover:underline font-medium"
            >
              {cit.source}
            </a>
            {cit.section_label && ` — ${cit.section_label}`}
            {cit.label_revision_date && ` (rev. ${cit.label_revision_date})`}
            {cit.drug_name && ` · ${cit.drug_name}`}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfidencePill({ confidence }) {
  if (!confidence) return null;
  const styles = {
    HIGH:   "bg-green-100 text-green-700",
    MEDIUM: "bg-yellow-100 text-yellow-700",
    LOW:    "bg-slate-100 text-slate-600",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${styles[confidence] || styles.LOW}`}>
      {confidence} confidence
    </span>
  );
}

// ── Main export ────────────────────────────────────────────────────────────────

export default function AnswerCard({ result, query }) {
  if (!result) return null;

  const structured = result.structured || {};
  const rawVerdict = structured.verdict || "CONSULT_PHARMACIST";
  const config = VERDICT_CONFIG[rawVerdict] || DEFAULT_VERDICT;

  const { answer, warning, details, action, article } = parseSections(result);
  const citations  = structured.citations || [];
  const confidence = structured.confidence || "";
  const source     = structured.sources || "";
  const isAiGen    = result.score === 1.0 && !result.id;

  const jsonLd = buildJsonLd(query, result, rawVerdict);

  return (
    <>
      <Head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
      </Head>

      <article
        className={`rounded-xl border shadow-sm overflow-hidden ${config.border}`}
        aria-label={`Answer: ${config.aria}`}
        role="article"
      >
        {/* Verdict banner */}
        <VerdictBanner config={config} />

        {/* Card body */}
        <div className={`p-5 space-y-4 ${config.bg}`}>

          {/* 1. Primary answer */}
          <PrimaryAnswer text={answer} />

          {/* 2. Warning */}
          <WarningBox text={warning} verdict={rawVerdict} />

          {/* 3. Details */}
          <BulletList
            title="Key facts"
            items={details}
            colorClass="text-slate-600"
            dotClass="bg-slate-400"
          />

          {/* 4. What to do */}
          <BulletList
            title="What to do"
            items={action}
            colorClass="text-emerald-700"
            dotClass="bg-emerald-500"
          />

          {/* 5. Mini article */}
          <MiniArticle text={article} />

          {/* Footer: confidence + AI badge + source */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3">
            <div className="flex items-center gap-2">
              <ConfidencePill confidence={confidence} />
              {isAiGen && (
                <span className="rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700">
                  AI-generated
                </span>
              )}
            </div>
            {source && (
              <p className="text-xs text-slate-400 truncate max-w-xs" title={source}>
                {source}
              </p>
            )}
          </div>

          {/* Citations */}
          <CitationList citations={citations} />

          {/* Disclaimer */}
          <p className="text-xs text-slate-400 italic">
            Data sourced from{" "}
            <a href="https://dailymed.nlm.nih.gov" target="_blank" rel="noopener noreferrer" className="underline">
              DailyMed
            </a>{" "}
            and{" "}
            <a href="https://www.fda.gov/drugs" target="_blank" rel="noopener noreferrer" className="underline">
              Drugs@FDA
            </a>
            . Not medical advice — always consult a licensed healthcare provider.
          </p>
        </div>
      </article>
    </>
  );
}

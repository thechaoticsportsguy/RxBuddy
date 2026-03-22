/**
 * AnswerCard — Phase 4 single-answer layout component
 *
 * Renders one comprehensive card per medication query:
 *   • Colour-coded verdict banner (SAFE / CAUTION / AVOID / CONSULT / EMERGENCY)
 *   • Short direct answer
 *   • Structured detail bullets (DO / AVOID / DOCTOR)
 *   • Scrollable article body
 *   • Source citations
 *   • schema.org/Drug JSON-LD (SEO)
 *   • ARIA roles for screen-reader accessibility
 *
 * Props
 * -----
 *   result   – one QuestionMatch object from /search
 *   query    – original user query string
 *   parsed   – optional pre-parsed structured object (from _parse_structured_answer)
 */

import Head from "next/head";

// ── Verdict config ────────────────────────────────────────────────────────────

const VERDICT_CONFIG = {
  SAFE: {
    bg:       "bg-green-50",
    border:   "border-green-300",
    badge:    "bg-green-100 text-green-800 border border-green-300",
    icon:     "✓",
    iconBg:   "bg-green-500",
    label:    "SAFE",
    aria:     "Safe to use",
  },
  CAUTION: {
    bg:       "bg-yellow-50",
    border:   "border-yellow-300",
    badge:    "bg-yellow-100 text-yellow-800 border border-yellow-300",
    icon:     "!",
    iconBg:   "bg-yellow-500",
    label:    "CAUTION",
    aria:     "Use with caution",
  },
  AVOID: {
    bg:       "bg-red-50",
    border:   "border-red-300",
    badge:    "bg-red-100 text-red-800 border border-red-300",
    icon:     "✕",
    iconBg:   "bg-red-600",
    label:    "AVOID",
    aria:     "Avoid this combination",
  },
  CONSULT_PHARMACIST: {
    bg:       "bg-blue-50",
    border:   "border-blue-300",
    badge:    "bg-blue-100 text-blue-800 border border-blue-300",
    icon:     "?",
    iconBg:   "bg-blue-500",
    label:    "CONSULT A HEALTHCARE PROVIDER",
    aria:     "Consult a healthcare provider",
  },
  INSUFFICIENT_DATA: {
    bg:       "bg-slate-50",
    border:   "border-slate-300",
    badge:    "bg-slate-100 text-slate-700 border border-slate-300",
    icon:     "–",
    iconBg:   "bg-slate-400",
    label:    "DATA UNAVAILABLE",
    aria:     "Insufficient data — consult a specialist",
  },
  EMERGENCY: {
    bg:       "bg-red-100",
    border:   "border-red-500",
    badge:    "bg-red-600 text-white border border-red-700",
    icon:     "!",
    iconBg:   "bg-red-700",
    label:    "EMERGENCY — CALL 911",
    aria:     "Medical emergency — call emergency services immediately",
  },
};

const DEFAULT_VERDICT = VERDICT_CONFIG.CONSULT_PHARMACIST;

// ── Helpers ───────────────────────────────────────────────────────────────────

function splitPipe(str) {
  if (!str) return [];
  return str
    .split("|")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Extract DO / AVOID / DOCTOR / WHY from the raw answer text if structured fields are empty. */
function parseRawText(raw = "") {
  const lines = raw.split("\n");
  const out = { why: "", doItems: [], avoidItems: [], doctorItems: [] };
  for (const line of lines) {
    const upper = line.trim().toUpperCase();
    if (upper.startsWith("WHY:")) out.why = line.slice(line.indexOf(":") + 1).trim();
    else if (upper.startsWith("DO:")) out.doItems = splitPipe(line.slice(line.indexOf(":") + 1));
    else if (upper.startsWith("AVOID:")) out.avoidItems = splitPipe(line.slice(line.indexOf(":") + 1));
    else if (upper.startsWith("DOCTOR:")) out.doctorItems = splitPipe(line.slice(line.indexOf(":") + 1));
  }
  return out;
}

// ── JSON-LD builder ───────────────────────────────────────────────────────────

function buildJsonLd(query, result, verdict) {
  const drugNames = extractDrugNames(query);
  return {
    "@context": "https://schema.org",
    "@type": "Drug",
    name: drugNames[0] || query,
    alternateName: drugNames.slice(1),
    description: result?.structured?.direct || result?.answer?.slice(0, 200) || "",
    warning: verdict === "AVOID" || verdict === "CAUTION"
      ? (result?.structured?.direct || "")
      : undefined,
    // FAQ-style Q&A for indexability
    mainEntity: {
      "@type": "Question",
      name: query,
      acceptedAnswer: {
        "@type": "Answer",
        text: result?.structured?.direct || result?.answer?.slice(0, 500) || "",
        url: typeof window !== "undefined" ? window.location.href : "",
        dateCreated: new Date().toISOString().slice(0, 10),
      },
    },
  };
}

function extractDrugNames(query = "") {
  // Very lightweight extraction for JSON-LD — the real extraction is in the backend
  const stop = new Set(["can", "i", "take", "with", "and", "the", "a", "is", "are", "while", "during", "for", "my"]);
  return query
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 3 && !stop.has(w))
    .slice(0, 3);
}

// ── Sub-components ────────────────────────────────────────────────────────────

function VerdictBanner({ verdict, config }) {
  return (
    <div
      className={`flex items-center gap-3 rounded-t-xl px-5 py-4 ${config.bg} border-b ${config.border}`}
      role="banner"
      aria-label={config.aria}
    >
      {/* Icon circle */}
      <div
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${config.iconBg} text-white font-bold text-lg`}
        aria-hidden="true"
      >
        {config.icon}
      </div>

      {/* Verdict badge */}
      <span
        className={`rounded-full px-3 py-1 text-xs font-bold tracking-wider uppercase ${config.badge}`}
        role="status"
      >
        {config.label}
      </span>
    </div>
  );
}

function DirectAnswer({ text }) {
  if (!text) return null;
  return (
    <p className="text-base font-semibold text-slate-900 leading-snug">
      {text}
    </p>
  );
}

function BulletSection({ title, items, colorClass = "text-slate-700", dotClass = "bg-slate-400" }) {
  if (!items?.length) return null;
  return (
    <section aria-labelledby={`section-${title.replace(/\s+/g, "-").toLowerCase()}`}>
      <h3
        id={`section-${title.replace(/\s+/g, "-").toLowerCase()}`}
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

function CitationList({ citations }) {
  if (!citations?.length) return null;
  return (
    <section aria-labelledby="citations-heading" className="mt-4 border-t border-slate-100 pt-3">
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

// ── Main AnswerCard ───────────────────────────────────────────────────────────

export default function AnswerCard({ result, query, parsed }) {
  if (!result) return null;

  const structured = result.structured || {};
  const rawVerdict = structured.verdict || "CONSULT_PHARMACIST";
  const config = VERDICT_CONFIG[rawVerdict] || DEFAULT_VERDICT;

  // Pull direct/why/do/avoid/doctor from structured fields, fall back to raw text parse
  const directAnswer = structured.direct || parsed?.direct || "";
  const rawParsed = parseRawText(result.answer || "");

  const doItems     = structured.do?.length     ? structured.do     : rawParsed.doItems;
  const avoidItems  = structured.avoid?.length  ? structured.avoid  : rawParsed.avoidItems;
  const doctorItems = structured.doctor?.length ? structured.doctor : rawParsed.doctorItems;
  const whyText     = structured.why || rawParsed.why || "";

  const citations   = structured.citations || [];
  const confidence  = structured.confidence || "";
  const source      = structured.sources || "";
  const isAiGen     = result.score === 1.0 && !result.id;

  const jsonLd = buildJsonLd(query, result, rawVerdict);

  return (
    <>
      {/* JSON-LD for schema.org / SEO */}
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
        {/* ── Verdict banner ── */}
        <VerdictBanner verdict={rawVerdict} config={config} />

        {/* ── Card body ── */}
        <div className={`p-5 ${config.bg}`}>
          {/* Direct answer */}
          {directAnswer && (
            <div className="mb-4">
              <DirectAnswer text={directAnswer} />
            </div>
          )}

          {/* Why / explanation */}
          {whyText && (
            <p className="mb-4 text-sm text-slate-600 leading-relaxed">{whyText}</p>
          )}

          {/* Structured bullet sections */}
          <div className="space-y-4">
            <BulletSection
              title="What to do"
              items={doItems}
              colorClass="text-emerald-700"
              dotClass="bg-emerald-500"
            />
            <BulletSection
              title="What to avoid"
              items={avoidItems}
              colorClass="text-red-700"
              dotClass="bg-red-500"
            />
            <BulletSection
              title="See a doctor if"
              items={doctorItems}
              colorClass="text-orange-700"
              dotClass="bg-orange-400"
            />
          </div>

          {/* Emergency escalation — always red, always visible */}
          {rawVerdict === "EMERGENCY" && (
            <div
              className="mt-4 rounded-lg border border-red-400 bg-red-100 p-3"
              role="alert"
              aria-live="assertive"
            >
              <p className="text-sm font-bold text-red-800">
                Call 911 or go to the nearest emergency room immediately.
              </p>
              <p className="mt-1 text-sm text-red-700">
                Poison Control (US): <strong>1-800-222-1222</strong>
              </p>
            </div>
          )}

          {/* Footer row: confidence + source badge */}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3">
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

          {/* Disclaimer note */}
          <p className="mt-3 text-xs text-slate-400 italic">
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

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
import { useState } from "react";

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

// Aliases — backend may return these; map them to a known config key
VERDICT_CONFIG.CONSULT          = VERDICT_CONFIG.CONSULT_PHARMACIST;
VERDICT_CONFIG.CONSULT_CLINICIAN = VERDICT_CONFIG.CONSULT_PHARMACIST;
VERDICT_CONFIG.INSUFFICIENT_DATA_REFUSED = VERDICT_CONFIG.INSUFFICIENT_DATA;
VERDICT_CONFIG.UNKNOWN          = VERDICT_CONFIG.INSUFFICIENT_DATA;

const DEFAULT_VERDICT = VERDICT_CONFIG.CONSULT_PHARMACIST;

// ── Helpers ────────────────────────────────────────────────────────────────────

function splitPipe(str) {
  if (!str) return [];
  return str.split("|").map((s) => s.trim()).filter(Boolean);
}

function stripMarkdown(text) {
  if (!text || typeof text !== "string") return "";
  return text
    .replace(/^\s{0,3}#{1,6}\s*/gm, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/`(.*?)`/g, "$1")
    .trim();
}

function sanitizeItems(items) {
  if (typeof items === "string") {
    return items
      .split(/[;|\n]+/)
      .map((item) => stripMarkdown(String(item || "")))
      .filter(Boolean);
  }
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => stripMarkdown(String(item || "")))
    .filter(Boolean);
}

// Phrases that indicate placeholder/error strings — never render as medical data
const PLACEHOLDER_SE_PATTERNS = [
  /temporary error/i,
  /error loading/i,
  /loading data/i,
  /failed to load/i,
  /could not load/i,
  /undefined/i,
  /null/i,
  /^\s*error\s*$/i,
];

function filterPlaceholders(items) {
  return items.filter(
    (item) => !PLACEHOLDER_SE_PATTERNS.some((re) => re.test(item))
  );
}

// Patterns that indicate a corrupted / raw-internal DB answer — never render these
const _CORRUPTED_PATTERNS = [
  /category\s+[3-6]/i,
  /primary intent category/i,
  /needs review\./i,
  /intent classification/i,
  /answer:\s*why:/i,
];

function _isCorrupted(text) {
  if (!text || typeof text !== "string") return true;
  const t = text.trim();
  if (!t || t.length < 40) return true;
  if (t[0] === ":" || t[0] === "." || t.startsWith("- ") || t.startsWith("---")) return true;
  return _CORRUPTED_PATTERNS.some((re) => re.test(t));
}

/**
 * Pull the 5 sections from structured fields, falling back to legacy fields,
 * then to raw-text parsing for old DB rows.
 */
function parseSections(result) {
  const s = result.structured || {};
  const raw = _isCorrupted(result.answer) ? "" : (result.answer || "");

  // ── New-format fields ────────────────────────────────────────────────────────
  const primaryAnswer = stripMarkdown(s.answer || s.short_answer || s.direct || "");
  if (primaryAnswer || s.details?.length || s.action?.length || s.article) {
    return {
      answer:  primaryAnswer,
      warning: stripMarkdown(s.warning || ""),
      details: sanitizeItems(Array.isArray(s.details) && s.details.length ? s.details : []),
      action:  sanitizeItems(
        Array.isArray(s.action) && s.action.length ? s.action :
               (Array.isArray(s.do) && s.do.length ? s.do : [])
      ),
      article: stripMarkdown(s.article || s.why || ""),
    };
  }

  // ── Legacy structured fields (old DB rows) ───────────────────────────────────
  if (s.direct || s.do?.length) {
    const details = sanitizeItems([...(s.avoid || []), ...(s.doctor || [])]);
    return {
      answer:  stripMarkdown(s.direct || ""),
      warning: details.length ? details[0] : "",
      details: details.slice(1),
      action:  sanitizeItems(s.do || []),
      article: stripMarkdown(s.why || ""),
    };
  }

  // ── Raw text fallback for plain-text DB answers ──────────────────────────────
  if (!raw) {
    return {
      answer: "Answer unavailable. Please consult a pharmacist.",
      warning: "", details: [], action: [], article: "",
    };
  }
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
  if (!out.answer && raw) {
    const first = raw.split(/[.!?]/)[0];
    out.answer = first ? first.trim() + "." : raw.slice(0, 200);
  }
  if (!out.answer) {
    out.answer = "Answer unavailable. Please consult a pharmacist.";
  }
  return {
    answer: stripMarkdown(out.answer),
    warning: stripMarkdown(out.warning),
    details: sanitizeItems(out.details),
    action: sanitizeItems(out.action),
    article: stripMarkdown(out.article),
  };
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

function PrimaryAnswer({ text }) {
  if (!text) return null;
  return (
    <p className="text-base font-semibold text-slate-900 leading-snug">
      {text}
    </p>
  );
}

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

// ── Banned phrases — filter generic disclaimers from side-effects bullets ─────

const BANNED_SE_PHRASES = [
  "side effects vary",
  "consult your pharmacist for a complete list",
  "consult your pharmacist or prescriber for a complete list",
  "serious side effects are possible",
  "contact your provider if you experience unusual symptoms",
  "read the patient information leaflet",
];

function filterBanned(items) {
  return items.filter((item) => {
    const lower = item.toLowerCase();
    return !BANNED_SE_PHRASES.some((phrase) => lower.includes(phrase));
  });
}

// ── TierSection — collapsible side-effect frequency tier ────────────────────

function TierSection({ tier, defaultExpanded, sources }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const headingId = `tier-${tier.label.replace(/\s+/g, "-").toLowerCase()}`;

  return (
    <section
      aria-labelledby={headingId}
      className={`rounded-lg border ${tier.borderColor} ${tier.bgColor} overflow-hidden`}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-2.5 text-left"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-controls={`${headingId}-content`}
      >
        <h3 id={headingId} className={`text-xs font-bold uppercase tracking-wider ${tier.color} flex items-center gap-2`}>
          <span aria-hidden="true">{tier.icon}</span>
          {tier.label}
          <span className="text-xs font-normal text-slate-400">({tier.items.length})</span>
        </h3>
        <span className="text-slate-400 text-sm" aria-hidden="true">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {expanded && (
        <div id={`${headingId}-content`} className="px-4 pb-3">
          <ul className="space-y-1" role="list">
            {tier.items.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${tier.dotColor}`} aria-hidden="true" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ── MechanismSection — expandable mechanism of action ────────────────────────

function MechanismSection({ summary, detail, pharmacologicClass, targets, sources }) {
  const [showDetail, setShowDetail] = useState(false);

  return (
    <section aria-labelledby="moa-heading" className="rounded-lg border border-blue-200 bg-blue-50 overflow-hidden">
      <div className="px-4 py-2.5">
        <h3 id="moa-heading" className="text-xs font-bold uppercase tracking-wider text-blue-700 mb-1.5">
          How It Works
        </h3>
        <p className="text-sm leading-relaxed text-slate-700">{summary}</p>

        {(pharmacologicClass || targets.length > 0) && (
          <div className="flex flex-wrap gap-2 mt-2">
            {pharmacologicClass && (
              <span className="rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-medium text-blue-700 border border-blue-200">
                {pharmacologicClass}
              </span>
            )}
            {targets.map((t, i) => (
              <span key={i} className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-700 border border-indigo-200">
                Target: {t}
              </span>
            ))}
          </div>
        )}

        {detail && detail !== summary && (
          <button
            type="button"
            className="mt-2 text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors"
            onClick={() => setShowDetail(!showDetail)}
          >
            {showDetail ? "Show less ▲" : "Show full detail ▼"}
          </button>
        )}

        {showDetail && detail && (
          <p className="mt-2 text-sm text-slate-600 leading-relaxed border-t border-blue-200 pt-2">
            {detail}
          </p>
        )}
      </div>
    </section>
  );
}

// ── SourceCitations — numbered inline citations with real URLs ────────────────

function SourceCitations({ sources }) {
  if (!sources?.length) return null;
  return (
    <section aria-labelledby="sources-heading" className="border-t border-yellow-200 pt-3">
      <h3 id="sources-heading" className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-500">
        Sources
      </h3>
      <ol className="space-y-1.5" role="list">
        {sources.map((src) => (
          <li key={src.id} className="flex items-start gap-2 text-xs text-slate-500">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 font-bold text-[10px]">
              {src.id}
            </span>
            <div>
              <a
                href={src.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-emerald-600 hover:underline font-medium"
                title={`${src.name} — ${src.section}${src.last_updated ? ` (updated ${src.last_updated})` : ""}`}
              >
                {src.name}
              </a>
              {src.section && <span className="text-slate-400"> — {src.section}</span>}
              {src.last_updated && <span className="text-slate-400"> (updated {src.last_updated})</span>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ── Main export ────────────────────────────────────────────────────────────────

export default function AnswerCard({ result, query }) {
  if (!result) return null;

  const structured = result.structured || {};

  // ── Unified side-effects detection — works for BOTH dataset and AI ──────────
  const isSideEffects =
    structured?.intent === "side_effects" ||
    structured?.intent === "SIDE_EFFECTS";

  if (isSideEffects) {
    const commonSE = filterPlaceholders(filterBanned(sanitizeItems(structured.common_side_effects)));
    const seriousSE = filterPlaceholders(filterBanned(sanitizeItems(structured.serious_side_effects)));
    const warnSigns = filterBanned(sanitizeItems(structured.warning_signs || structured.when_to_get_help));
    const mechText = stripMarkdown(structured.mechanism || structured.mechanism_simple || structured.article || "");
    const studies = Array.isArray(structured.pubmed_studies) ? structured.pubmed_studies : [];

    // ── New tiered data from structured parsing ─────────────────────────────
    const seData = structured.side_effects_data || {};
    const boxedWarnings = Array.isArray(structured.boxed_warnings) ? structured.boxed_warnings : [];
    const moaObj = structured.mechanism_of_action || {};
    const structuredSources = Array.isArray(structured.structured_sources) ? structured.structured_sources : [];
    const brandNames = Array.isArray(structured.brand_names) ? structured.brand_names : [];
    const genericName = structured.generic_name || "";

    // Build tiers — prefer structured tiers, fall back to flat lists
    const tiers = {
      serious: {
        label: seData?.serious?.label || "Serious — Seek Immediate Medical Attention",
        items: filterBanned(sanitizeItems(seData?.serious?.items || seriousSE)),
        urgent: true,
        color: "text-red-700",
        dotColor: "bg-red-500",
        bgColor: "bg-red-50",
        borderColor: "border-red-200",
        icon: "🔴",
      },
      very_common: {
        label: seData?.very_common?.label || "Very Common (>10%)",
        items: filterBanned(sanitizeItems(seData?.very_common?.items || [])),
        color: "text-orange-700",
        dotColor: "bg-orange-400",
        bgColor: "bg-orange-50",
        borderColor: "border-orange-200",
        icon: "🟠",
      },
      common: {
        label: seData?.common?.label || "Common (1-10%)",
        items: filterBanned(sanitizeItems(seData?.common?.items || commonSE)),
        color: "text-yellow-700",
        dotColor: "bg-yellow-400",
        bgColor: "bg-yellow-50",
        borderColor: "border-yellow-200",
        icon: "🟡",
      },
      uncommon: {
        label: seData?.uncommon?.label || "Uncommon / Rare (<1%)",
        items: filterBanned(sanitizeItems(seData?.uncommon?.items || [])),
        color: "text-slate-600",
        dotColor: "bg-slate-400",
        bgColor: "bg-slate-50",
        borderColor: "border-slate-200",
        icon: "⚪",
      },
    };

    const hasAnyData = Object.values(tiers).some(t => t.items.length > 0);
    const cautionCfg = VERDICT_CONFIG.CAUTION;

    // Mechanism display — prefer structured, fall back to flat
    const moaSummary = moaObj.summary || mechText || "";
    const moaDetail = moaObj.detail || "";
    const moaClass = moaObj.pharmacologic_class || "";
    const moaTargets = Array.isArray(moaObj.molecular_targets) ? moaObj.molecular_targets : [];

    // Empty state — no real side effects data
    if (!hasAnyData) {
      return (
        <article className="rounded-xl border border-yellow-300 shadow-sm overflow-hidden" aria-label="Side effects information" role="article">
          <VerdictBanner config={cautionCfg} />
          <div className="bg-yellow-50 p-5">
            <div className="rounded-lg border border-yellow-200 bg-white p-4">
              <p className="text-sm font-semibold text-slate-800">
                {"We don't have detailed side effect data for this medication yet."}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                Please consult your pharmacist or prescriber for a complete list of side effects.
              </p>
            </div>
          </div>
        </article>
      );
    }

    // ── Full tiered side effects card ──────────────────────────────────────────
    return (
      <article className="rounded-xl border border-yellow-300 shadow-sm overflow-hidden" aria-label="Side effects information — use with caution" role="article">
        <VerdictBanner config={cautionCfg} />

        {/* Brand-to-generic resolution header */}
        {(brandNames.length > 0 || genericName) && (
          <div className="bg-yellow-100 px-5 py-2 border-b border-yellow-200">
            <p className="text-xs text-slate-600">
              Showing results for <strong className="text-slate-800">{genericName || structured.drug || ""}</strong>
              {brandNames.length > 0 && (
                <span className="text-slate-500"> ({brandNames.join(", ")})</span>
              )}
            </p>
          </div>
        )}

        {/* BOXED WARNING BANNER */}
        {boxedWarnings.length > 0 && (
          <div className="mx-5 mt-4 rounded-lg border-2 border-black bg-white p-4" role="alert" aria-live="assertive">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg" aria-hidden="true">⚠️</span>
              <h3 className="text-sm font-black uppercase tracking-wider text-black">FDA Boxed Warning</h3>
            </div>
            {boxedWarnings.map((w, i) => (
              <p key={i} className="text-sm text-slate-800 leading-relaxed mt-1">{w}.</p>
            ))}
          </div>
        )}

        <div className="bg-yellow-50 p-5 space-y-3">

          {/* TIERED SIDE EFFECTS */}
          {Object.entries(tiers).map(([key, tier]) => {
            if (tier.items.length === 0) return null;
            const defaultExpanded = key === "serious" || key === "very_common" || key === "common";
            return (
              <TierSection
                key={key}
                tier={tier}
                defaultExpanded={defaultExpanded}
                sources={structuredSources}
              />
            );
          })}

          {/* MECHANISM OF ACTION */}
          {moaSummary && (
            <MechanismSection
              summary={moaSummary}
              detail={moaDetail}
              pharmacologicClass={moaClass}
              targets={moaTargets}
              sources={structuredSources}
            />
          )}

          {/* WHEN TO GET HELP */}
          {warnSigns.length > 0 && (
            <section aria-labelledby="se-help-heading">
              <h3 id="se-help-heading" className="mb-2 text-xs font-bold uppercase tracking-wider text-red-700">WHEN TO GET HELP</h3>
              <ul className="space-y-1" role="list">
                {warnSigns.map((x, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-red-400" aria-hidden="true" />
                    {x}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* RELATED RESEARCH */}
          {studies.length > 0 && (
            <section aria-labelledby="se-studies-heading">
              <h3 id="se-studies-heading" className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-600">RELATED RESEARCH</h3>
              <div className="space-y-2">
                {studies.slice(0, 3).map((study, i) => (
                  <a
                    key={study.pmid || i}
                    href={study.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block rounded-lg border border-slate-200 bg-white p-3 hover:border-emerald-300 hover:shadow transition-all"
                  >
                    <p className="text-sm font-medium text-slate-800 line-clamp-2">{study.title}</p>
                    <p className="mt-1 text-xs text-slate-500">{study.journal}{study.year ? ` • ${study.year}` : ""}</p>
                  </a>
                ))}
              </div>
            </section>
          )}

          {/* INLINE CITATIONS / SOURCES */}
          {structuredSources.length > 0 ? (
            <SourceCitations sources={structuredSources} />
          ) : (
            <div className="border-t border-yellow-200 pt-3">
              <p className="text-xs text-slate-400 italic">
                Data sourced from{" "}
                <a href="https://dailymed.nlm.nih.gov" target="_blank" rel="noopener noreferrer" className="underline">DailyMed</a>{" "}
                and{" "}
                <a href="https://www.fda.gov/drugs" target="_blank" rel="noopener noreferrer" className="underline">Drugs@FDA</a>
                . Not medical advice — always consult a licensed healthcare provider.
              </p>
            </div>
          )}

          {/* MEDICAL DISCLAIMER + MEDWATCH */}
          <div className="border-t border-yellow-200 pt-3">
            <p className="text-xs text-slate-400 italic">
              This information is for educational purposes only and is not a substitute for professional medical advice. Always consult your doctor or pharmacist.{" "}
              <a href="https://www.fda.gov/safety/medwatch-fda-safety-information-and-adverse-event-reporting-program" target="_blank" rel="noopener noreferrer" className="underline text-slate-500">
                Report side effects to FDA MedWatch
              </a>.
            </p>
          </div>
        </div>
      </article>
    );
  }

  // ── Non-side-effects render path (interactions, dosage, general) ─────────────

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
        <VerdictBanner config={config} />

        <div className={`p-5 space-y-4 ${config.bg}`}>
          <PrimaryAnswer text={answer} />
          <WarningBox text={warning} verdict={rawVerdict} />
          <BulletList title="Key facts" items={details} colorClass="text-slate-600" dotClass="bg-slate-400" />
          <BulletList title="What to do" items={action} colorClass="text-emerald-700" dotClass="bg-emerald-500" />
          <MiniArticle text={article} />

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

          <CitationList citations={citations} />

          <p className="text-xs text-slate-400 italic">
            Data sourced from{" "}
            <a href="https://dailymed.nlm.nih.gov" target="_blank" rel="noopener noreferrer" className="underline">DailyMed</a>{" "}
            and{" "}
            <a href="https://www.fda.gov/drugs" target="_blank" rel="noopener noreferrer" className="underline">Drugs@FDA</a>
            . Not medical advice — always consult a licensed healthcare provider.
          </p>
        </div>
      </article>
    </>
  );
}

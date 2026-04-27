import Head from "next/head";
import Link from "next/link";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

export async function getStaticProps() {
  let data = {
    accuracy_pct: null,
    hallucination_rate_pct: null,
    citation_rate_pct: null,
    total_questions: 0,
    last_run_at: null,
    goldset_version: "v1",
    status: "no_eval_run_yet",
  };
  try {
    const res = await fetch(`${API_BASE}/api/eval/latest`, { cache: "no-store" });
    if (res.ok) {
      data = await res.json();
    }
  } catch (e) {
    // Build can run without the backend reachable. Empty state will render.
  }
  return { props: { data }, revalidate: 86400 };
}

function formatRelativeTime(iso) {
  if (!iso) return "never";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "never";
  const seconds = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days >= 2) return `${days} days ago`;
  if (days === 1) return "yesterday";
  if (hours >= 1) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  if (minutes >= 1) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  return "just now";
}

function hallucinationColor(pct) {
  if (pct == null) return "text-slate-400";
  if (pct > 5) return "text-rose-600";
  if (pct >= 2) return "text-amber-600";
  return "text-emerald-600";
}

function StatBlock({ label, value, suffix = "%", valueClassName = "text-slate-900" }) {
  const display = value == null ? "—" : `${value}${suffix}`;
  return (
    <div className="flex-1 rounded-2xl border border-slate-200 bg-white p-8 text-left shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
        {label}
      </p>
      <p className={`mt-3 text-5xl font-bold tabular-nums ${valueClassName}`}>
        {display}
      </p>
    </div>
  );
}

export default function TrustPage({ data }) {
  const noRun = data.status !== "ok";
  const lastRun = noRun ? "never" : formatRelativeTime(data.last_run_at);
  const total = data.total_questions || 0;

  return (
    <>
      <Head>
        <title>Trust — RxBuddy</title>
        <meta
          name="description"
          content="Public eval results for RxBuddy: accuracy, hallucination rate, and citation rate against an FDA-grounded goldset."
        />
      </Head>

      <main className="min-h-screen bg-slate-50 text-slate-900">
        <div className="mx-auto max-w-3xl px-6 py-16 sm:py-24">
          <div className="mb-12">
            <p className="text-xs font-semibold uppercase tracking-widest text-emerald-700">
              Trust
            </p>
            <h1 className="mt-2 text-4xl font-bold tracking-tight sm:text-5xl">
              How accurate is RxBuddy?
            </h1>
            <p className="mt-4 text-lg text-slate-600">
              Every night we re-run the same FDA-grounded benchmark and publish
              the numbers — green or red.
            </p>
          </div>

          <div className="flex flex-col gap-4 sm:flex-row">
            <StatBlock label="Accuracy" value={data.accuracy_pct} />
            <StatBlock
              label="Hallucination Rate"
              value={data.hallucination_rate_pct}
              valueClassName={hallucinationColor(data.hallucination_rate_pct)}
            />
            <StatBlock label="Citation Rate" value={data.citation_rate_pct} />
          </div>

          <p className="mt-8 text-sm text-slate-600">
            {noRun ? (
              <>The eval has not been run yet. The first nightly run will populate this page.</>
            ) : (
              <>
                Last evaluated <span className="font-semibold">{lastRun}</span>{" "}
                on a benchmark of <span className="font-semibold">{total}</span>{" "}
                patient questions across 10 common drugs.
              </>
            )}
          </p>

          <section className="mt-12 space-y-4 text-base leading-relaxed text-slate-700">
            <p>
              RxBuddy publishes its accuracy because a medication assistant
              that won&apos;t show its work isn&apos;t one you should trust.
              The benchmark is a fixed set of {total || "50"} patient questions
              spanning dosing, side effects, drug interactions,
              contraindications, and mechanism of action — every answer is
              verifiable against the FDA-approved drug label.
            </p>
            <p>
              We score three things. <span className="font-semibold">Accuracy</span>{" "}
              measures how often the response contains the keywords a correct
              FDA-grounded answer must contain.{" "}
              <span className="font-semibold">Hallucination rate</span> measures
              how often the response makes a medical claim the FDA label does
              not support — this is the number we watch most carefully.{" "}
              <span className="font-semibold">Citation rate</span> measures how
              often the response cites the source it should have cited
              (DailyMed, openFDA, or RxNav).
            </p>
            <p>
              The judge is Claude Haiku running with a strict JSON rubric.
              Results are committed to git, so you can audit every score.
            </p>
          </section>

          <p className="mt-12 text-sm">
            <Link
              href="/trust/methodology"
              className="font-semibold text-emerald-700 underline-offset-4 hover:underline"
            >
              View methodology →
            </Link>
          </p>

          <p className="mt-16 text-xs text-slate-500">
            Goldset version {data.goldset_version || "v1"}. Numbers refresh
            within 24 hours of each nightly eval run.
          </p>
        </div>
      </main>
    </>
  );
}

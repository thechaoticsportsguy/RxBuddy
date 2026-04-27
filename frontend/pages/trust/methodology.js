import Head from "next/head";
import Link from "next/link";

export default function MethodologyPage() {
  return (
    <>
      <Head>
        <title>Methodology — RxBuddy Trust</title>
      </Head>
      <main className="min-h-screen bg-slate-50 text-slate-900">
        <div className="mx-auto max-w-3xl px-6 py-16 sm:py-24">
          <p className="text-xs font-semibold uppercase tracking-widest text-emerald-700">
            Trust
          </p>
          <h1 className="mt-2 text-4xl font-bold tracking-tight sm:text-5xl">
            Methodology
          </h1>
          <p className="mt-6 text-lg text-slate-600">
            Methodology doc coming soon.
          </p>
          <p className="mt-12 text-sm">
            <Link
              href="/trust"
              className="font-semibold text-emerald-700 underline-offset-4 hover:underline"
            >
              ← Back to Trust
            </Link>
          </p>
        </div>
      </main>
    </>
  );
}

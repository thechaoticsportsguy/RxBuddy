/**
 * Disclaimer — reusable medical information notice.
 * Use `variant="short"` for inline footers, `variant="full"` for dedicated disclaimer blocks.
 */
export default function Disclaimer({ variant = "short" }) {
  if (variant === "full") {
    return (
      <div
        style={{
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: 12,
          padding: "20px 24px",
          maxWidth: 720,
          margin: "0 auto",
        }}
      >
        <p style={{ fontWeight: 700, fontSize: 13, color: "#1e293b", marginBottom: 8, letterSpacing: "0.5px", textTransform: "uppercase" }}>
          Medical Information Disclaimer
        </p>
        <p style={{ fontSize: 13, color: "#475569", lineHeight: 1.65, margin: 0 }}>
          RxBuddy is an informational tool only. It retrieves drug label data published by the{" "}
          <a href="https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm" target="_blank" rel="noopener noreferrer" style={{ color: "#2D6A4F", textDecoration: "underline" }}>
            U.S. National Library of Medicine via DailyMed
          </a>{" "}
          and the{" "}
          <a href="https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-database" target="_blank" rel="noopener noreferrer" style={{ color: "#2D6A4F", textDecoration: "underline" }}>
            FDA's Drugs@FDA database
          </a>
          . RxBuddy does not create, review, or certify any medical content, and is not affiliated with or endorsed by the U.S. Food and Drug Administration (FDA), the National Institutes of Health (NIH), or any government agency.{" "}
          <strong>This tool does not replace the advice of a licensed pharmacist, physician, or other qualified healthcare professional.</strong>{" "}
          Always read the official drug label and consult a healthcare provider before making any medication decision. In a medical emergency, call 911 or your local emergency number immediately.
        </p>
      </div>
    );
  }

  // Short variant — for footers
  return (
    <p style={{ fontSize: 11, color: "rgba(183, 228, 199, 0.65)", textAlign: "center", lineHeight: 1.6, maxWidth: 480, margin: "0 auto" }}>
      RxBuddy displays drug label information from{" "}
      <a href="https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm" target="_blank" rel="noopener noreferrer" style={{ color: "rgba(183,228,199,0.9)", textDecoration: "underline" }}>DailyMed</a>
      {" "}and{" "}
      <a href="https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-database" target="_blank" rel="noopener noreferrer" style={{ color: "rgba(183,228,199,0.9)", textDecoration: "underline" }}>Drugs@FDA</a>
      . Not affiliated with or endorsed by the FDA. Not a substitute for professional medical advice. For emergencies, call 911.
    </p>
  );
}

"use client";

import { useEffect } from "react";

import { TRUST_TIER_META } from "@/lib/types";

// =============================================================================
// MethodologyDrawer — static, truthful explanation of how CRS-01 decides
// what it shows. Every line describes the code's actual behavior:
// tiers come from backend/src/graph/kb.py's evidence rules, confidence from
// the lineage_edges aggregates, and the governing line from AGENTS.md.
// Dark mode only; no new dependencies; Esc closes; aria-labelled.
// =============================================================================

const DRAWER_BG = "#0F2A1F";
const DRAWER_BORDER = "#2A4A3A";
const TEXT_PRIMARY = "#EDE6D8";
const TEXT_MUTED = "#8B9A8E";
const TEXT_FAINT = "#5A6B5F";
const GOLD_DIM = "#8B6914";
const SANS = "var(--font-inter), Inter, sans-serif";
const SERIF = "var(--font-eb-garamond), 'EB Garamond', Georgia, serif";

/** The four trust tiers — display hexes come straight from TRUST_TIER_META
 * (the canonical token source, mirrored by globals.css), so the drawer can
 * never drift from the badges. */
const TIERS: Array<{ name: string; hex: string; rule: string }> = [
  {
    name: "Verified",
    hex: TRUST_TIER_META.VERIFIED.color,
    rule: "Assigned by human curators only — never by the pipeline.",
  },
  {
    name: "Community consensus",
    hex: TRUST_TIER_META.COMMUNITY_CONSENSUS.color,
    rule: "Two or more independent domains agree on the assertion.",
  },
  {
    name: "Anecdotal",
    hex: TRUST_TIER_META.ANECDOTAL.color,
    rule: "A single source. Shown with a caveat.",
  },
  {
    name: "Contradicted",
    hex: TRUST_TIER_META.CONTRADICTED.color,
    rule: "Sources assert conflicting parent sets. Hidden by default — but kept in the record.",
  },
];

export default function MethodologyDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  // Esc closes the drawer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50"
      role="dialog"
      aria-modal="true"
      aria-label="Methodology — how CRS-01 decides what to show"
    >
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Close methodology"
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-black/50"
        style={{ border: "none" }}
      />

      {/* Slide-over panel */}
      <aside
        className="absolute right-0 top-0 flex h-full w-full max-w-[440px] flex-col border-l shadow-2xl"
        style={{ background: DRAWER_BG, borderColor: DRAWER_BORDER }}
      >
        <div
          className="flex shrink-0 items-center justify-between border-b px-5 py-3"
          style={{ borderColor: DRAWER_BORDER }}
        >
          <span
            className="text-[10px] uppercase tracking-[0.18em]"
            style={{ color: GOLD_DIM, fontFamily: SANS }}
          >
            Methodology
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close methodology"
            className="cursor-pointer rounded-md border px-2 py-0.5 text-[13px] leading-tight"
            style={{
              color: TEXT_MUTED,
              borderColor: DRAWER_BORDER,
              background: "transparent",
            }}
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {/* ── Trust tiers ─────────────────────────────────── */}
          <Section title="Trust tiers">
            <ul className="flex flex-col gap-2.5">
              {TIERS.map((t) => (
                <li key={t.name} className="flex items-start gap-2.5">
                  <span
                    aria-hidden
                    className="mt-0.5 inline-block h-3 w-3 shrink-0 rounded-sm border"
                    style={{ background: t.hex, borderColor: `${t.hex}88` }}
                  />
                  <span className="text-[12px] leading-relaxed" style={{ color: TEXT_MUTED }}>
                    <span
                      className="font-medium uppercase tracking-[0.06em]"
                      style={{ color: TEXT_PRIMARY, fontSize: 11 }}
                    >
                      {t.name}
                    </span>{" "}
                    — {t.rule}
                  </span>
                </li>
              ))}
            </ul>
          </Section>

          {/* ── Confidence ──────────────────────────────────── */}
          <Section title="How confidence works">
            <p>
              Every lineage edge&apos;s confidence is the mean confidence of its recorded
              evidence observations. In the graph wheel, radial distance encodes it: the
              outer ring is the 40% baseline, and higher-confidence nodes are pulled
              closer to the center.
            </p>
          </Section>

          {/* ── Conflicts ───────────────────────────────────── */}
          <Section title="Conflicts">
            <p>
              A disagreement is exactly what it sounds like: different sources assert
              different parent sets for the same strain. Those edges show red. A
              contradicted strain stays in the record — it is never deleted for being
              inconvenient.
            </p>
            <p>
              Quarantine removes a single observation from all consensus math without
              deleting it, and a curator can reverse it. The raw row remains on file.
            </p>
          </Section>

          {/* ── Provenance ──────────────────────────────────── */}
          <Section title="Provenance">
            <p>
              Every lineage claim traces to a quoted public web source. Source links open
              the live page; where archive.org had captured it, an archived-copy link is
              shown too. A missing archive link means exactly that: no capture on record.
            </p>
          </Section>

          {/* ── Governing line ──────────────────────────────── */}
          <div
            className="mt-6 border-t pt-5"
            style={{ borderColor: DRAWER_BORDER }}
          >
            <p
              className="text-[15px] italic leading-relaxed"
              style={{ color: TEXT_PRIMARY, fontFamily: SERIF }}
            >
              “A missing connection is more honest than a false one.”
            </p>
          </div>
        </div>
      </aside>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h3
        className="mb-2 text-[9px] uppercase tracking-[0.18em]"
        style={{ color: TEXT_FAINT, fontFamily: SANS }}
      >
        {title}
      </h3>
      <div
        className="flex flex-col gap-2.5 text-[12px] leading-relaxed"
        style={{ color: TEXT_MUTED, fontFamily: SANS }}
      >
        {children}
      </div>
    </section>
  );
}

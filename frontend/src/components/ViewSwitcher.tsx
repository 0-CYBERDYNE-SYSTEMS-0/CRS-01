"use client";

import { TRUST_TIER_META } from "@/lib/types";

export type CanvasView = "graph" | "report" | "sources" | "dashboard";

const VIEWS: { id: CanvasView; label: string; icon: string }[] = [
  { id: "graph", label: "Graph", icon: "◉" },
  { id: "report", label: "Report", icon: "¶" },
  { id: "sources", label: "Sources", icon: "↗" },
  { id: "dashboard", label: "Dashboard", icon: "◫" },
];

// Active-tab gold = verified gold; sourced from the one token table.
const ACCENT = TRUST_TIER_META.VERIFIED.color;

export default function ViewSwitcher({
  view,
  onChange,
  reportBadge = 0,
}: {
  view: CanvasView;
  onChange: (v: CanvasView) => void;
  reportBadge?: number;
}) {
  return (
    <div
      role="tablist"
      aria-label="View"
      className="glass"
      style={{
        display: "inline-flex",
        gap: 4,
        border: "1px solid #2A332C",
        borderRadius: 12,
        padding: 4,
        boxShadow: "0 4px 18px rgba(0,0,0,0.35)",
      }}
    >
      {VIEWS.map((v) => {
        const active = v.id === view;
        return (
          <button
            key={v.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(v.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              padding: "8px 16px",
              borderRadius: 8,
              border: "none",
              cursor: "pointer",
              fontFamily: "var(--font-inter), Inter, sans-serif",
              fontSize: 12.5,
              letterSpacing: "0.04em",
              fontWeight: active ? 600 : 450,
              color: active ? "#0C120E" : "#9AA69E",
              background: active ? ACCENT : "transparent",
              boxShadow: active ? "0 0 18px rgba(212,160,23,0.35)" : "none",
              transition: "background .15s ease, color .15s ease, box-shadow .15s ease",
            }}
            onMouseEnter={(e) => {
              if (!active) e.currentTarget.style.color = "#DCE4DC";
            }}
            onMouseLeave={(e) => {
              if (!active) e.currentTarget.style.color = "#9AA69E";
            }}
            title={active ? undefined : `Show ${v.label} view`}
          >
            <span style={{ fontSize: 11, opacity: active ? 1 : 0.7 }}>
              {v.icon}
            </span>
            {v.label}
            {v.id === "report" && reportBadge > 0 && !active && (
              <span
                style={{
                  marginLeft: 2,
                  minWidth: 16,
                  padding: "0 4px",
                  borderRadius: 999,
                  background: "transparent",
                  border: `1px solid ${ACCENT}`,
                  color: ACCENT,
                  fontFamily: "var(--font-inter), Inter, sans-serif",
                  fontSize: 9,
                  lineHeight: "14px",
                  textAlign: "center",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {reportBadge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

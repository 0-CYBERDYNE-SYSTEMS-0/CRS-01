import type { Metadata } from "next";
import { Inter, EB_Garamond } from "next/font/google";
import { TRUST_TIER_META } from "@/lib/types";
import "@/styles/globals.css";

// The gold "S" mark uses the verified-gold accent — hex sourced from the
// single token table (URL-encoded for the data URI).
const FAVICON = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%230F2A1F'/><text x='16' y='22' text-anchor='middle' font-size='18' fill='${TRUST_TIER_META.VERIFIED.color.replace("#", "%23")}'>S</text></svg>`;

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-inter",
});

const ebGaramond = EB_Garamond({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
  display: "swap",
  variable: "--font-eb-garamond",
});

export const metadata: Metadata = {
  title: "CRS-01 — Cannabis Research Sentinel",
  description:
    "Graph-first intelligence layer for cannabis breeding knowledge. Built with taste that lasts.",
  icons: {
    icon: [
      {
        url: FAVICON,
        type: "image/svg+xml",
      },
    ],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${ebGaramond.variable} dark`}
      suppressHydrationWarning
    >
      <head>
        <style>{`html { background-color: #0F2A1F; } body { background-color: #0F2A1F; }`}</style>
      </head>
      <body className="bg-[#0F2A1F] text-[#EDE6D8]">{children}</body>
    </html>
  );
}
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // CRS-01 Earth Palette
        "bg-deep": "var(--bg-deep)",
        "bg-surface": "var(--bg-surface)",
        "accent": "var(--accent)",
        "accent-dim": "var(--accent-dim)",
        "text-primary": "var(--text-primary)",
        "text-muted": "var(--text-muted)",
        "text-faint": "var(--text-faint)",
        "border": "var(--border)",
        "trust-verified": "var(--trust-verified)",
        "trust-community": "var(--trust-community)",
        "trust-anecdotal": "var(--trust-anecdotal)",
        "trust-contradicted": "var(--trust-contradicted)",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        inter: ["var(--font-inter)", "system-ui", "sans-serif"],
        serif: ["var(--font-eb-garamond)", "EB Garamond", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
      keyframes: {
        "spin": {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
      },
      animation: {
        "spin": "spin 0.7s linear infinite",
      },
    },
  },
  plugins: [],
};

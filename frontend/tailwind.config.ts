import type { Config } from "tailwindcss";

/**
 * Restrained palette. Ink/paper neutrals carry the layout; colour is reserved
 * for severity and direction, so it always means something.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#0d1117",
          800: "#161b22",
          700: "#21262d",
          500: "#57606a",
          400: "#6e7781",
          300: "#8c959f",
          200: "#d0d7de",
          100: "#eaeef2",
          50: "#f6f8fa",
        },
        accent: { DEFAULT: "#1f6feb", soft: "#ddf4ff" },
        up: { DEFAULT: "#1a7f37", soft: "#dafbe1" },
        down: { DEFAULT: "#cf222e", soft: "#ffebe9" },
        warn: { DEFAULT: "#9a6700", soft: "#fff8c5" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(31,35,40,0.04), 0 3px 8px rgba(31,35,40,0.05)",
        lift: "0 2px 4px rgba(31,35,40,0.06), 0 8px 24px rgba(31,35,40,0.09)",
      },
      borderRadius: { xl: "12px", "2xl": "16px" },
    },
  },
  plugins: [],
};

export default config;

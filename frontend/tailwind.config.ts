import type { Config } from "tailwindcss";

// Minimalist monochrome (black & white) system. The `accent` color is driven by
// CSS variables (see globals.css) so it flips to near-white in dark mode without
// touching class names — `bg-accent` is ink-on-paper in light, paper-on-ink in dark.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      // One family site-wide. --font-nunito-sans is set on <html> by next/font (app/layout.tsx);
      // Tailwind's preflight applies `font-sans` to <html>, so everything inherits it.
      fontFamily: {
        sans: ["var(--font-nunito-sans)", "'Nunito Sans'", "'Proxima Nova'", "system-ui", "sans-serif"],
      },
      // Type roles. Body text inherits text-body from <body> (globals.css).
      fontSize: {
        h1: ["44px", { lineHeight: "1.15", letterSpacing: "-0.01em", fontWeight: "700" }],
        h2: ["28px", { lineHeight: "1.25", letterSpacing: "-0.01em", fontWeight: "700" }],
        h3: ["20px", { lineHeight: "1.35", letterSpacing: "-0.01em", fontWeight: "700" }],
        label: ["13px", { lineHeight: "1.4", letterSpacing: "0.06em", fontWeight: "600" }], // e.g. "TARGET SIZE"
        ui: ["15px", { lineHeight: "1.4", fontWeight: "600" }], // button and card text
        body: ["16px", { lineHeight: "1.65", letterSpacing: "0.01em" }],
      },
      colors: {
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          fg: "var(--accent-fg)", // readable text/icon color on an accent fill
        },
        heading: "var(--fg-heading)",
        copy: "var(--fg-copy)",
      },
    },
  },
  plugins: [],
};
export default config;

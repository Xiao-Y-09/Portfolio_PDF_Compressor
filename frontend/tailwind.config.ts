import type { Config } from "tailwindcss";

// Minimalist monochrome (black & white) system. The `accent` color is driven by
// CSS variables (see globals.css) so it flips to near-white in dark mode without
// touching class names — `bg-accent` is ink-on-paper in light, paper-on-ink in dark.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          fg: "var(--accent-fg)", // readable text/icon color on an accent fill
        },
      },
    },
  },
  plugins: [],
};
export default config;

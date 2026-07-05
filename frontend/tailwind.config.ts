import type { Config } from "tailwindcss";

// 手册 Phase 14 样式规格：主色调深灰 + 强调色橙，深色模式支持（media 策略）
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        accent: { DEFAULT: "#f97316", hover: "#ea580c" }, // orange-500/600
      },
    },
  },
  plugins: [],
};
export default config;

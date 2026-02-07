import type { Config } from "tailwindcss";
import defaultTheme from "tailwindcss/defaultTheme";

const config: Config = {
  content: [
    "./src/app/**/*.{js,ts,jsx,tsx}",
    "./src/components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "#0f172a",
        surface: "#111827",
        card: "#1f2937",
        accent: "#10b981",
        accent2: "#22d3ee",
        muted: "#94a3b8",
        border: "#1f2937",
      },
      fontFamily: {
        sans: ["var(--font-space-grotesk)", ...defaultTheme.fontFamily.sans],
        display: ["var(--font-space-grotesk)", ...defaultTheme.fontFamily.sans],
      },
      boxShadow: {
        glow: "0 10px 50px rgba(34, 211, 238, 0.35)",
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};

export default config;

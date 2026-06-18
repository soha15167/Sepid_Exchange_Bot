import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      screens: {
        xs: "380px",
      },
      colors: {
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
        },
        accent: {
          cyan: "#22d3ee",
          violet: "#a78bfa",
          pink: "#f472b6",
        },
        ink: {
          950: "#06070d",
          900: "#0c0e16",
          800: "#141824",
          700: "#1c2130",
        },
      },
      boxShadow: {
        glass: "0 8px 32px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.06)",
        glow: "0 0 48px rgba(99,102,241,0.35), 0 0 96px rgba(34,211,238,0.12)",
        "glow-sm": "0 0 24px rgba(99,102,241,0.25)",
        card: "0 4px 24px rgba(0,0,0,0.35)",
      },
      backgroundImage: {
        mesh: `
          radial-gradient(ellipse 80% 50% at 50% -20%, rgba(99,102,241,0.28), transparent),
          radial-gradient(ellipse 60% 40% at 100% 0%, rgba(167,139,250,0.18), transparent),
          radial-gradient(ellipse 50% 50% at 0% 100%, rgba(34,211,238,0.12), transparent),
          linear-gradient(180deg, #06070d 0%, #0c0e16 50%, #06070d 100%)
        `,
        "btn-gradient": "linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #6366f1 100%)",
        "border-glow": "linear-gradient(135deg, rgba(99,102,241,0.5), rgba(34,211,238,0.3), rgba(167,139,250,0.4))",
      },
      animation: {
        aurora: "aurora 14s ease-in-out infinite alternate",
        float: "float 8s ease-in-out infinite",
        shimmer: "shimmer 2.5s linear infinite",
        "pulse-glow": "pulse-glow 3s ease-in-out infinite",
      },
      keyframes: {
        aurora: {
          "0%": { transform: "translate(0, 0) scale(1)", opacity: "0.5" },
          "50%": { transform: "translate(-5%, 3%) scale(1.05)", opacity: "0.7" },
          "100%": { transform: "translate(5%, -2%) scale(1.02)", opacity: "0.55" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-12px)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "200% center" },
          "100%": { backgroundPosition: "-200% center" },
        },
        "pulse-glow": {
          "0%, 100%": { opacity: "0.4", transform: "scale(1)" },
          "50%": { opacity: "0.7", transform: "scale(1.08)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;

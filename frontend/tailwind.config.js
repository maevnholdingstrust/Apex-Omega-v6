/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: "#05060A",
          900: "#0A0C13",
          800: "#10131C",
          700: "#181C28",
          600: "#222637",
        },
        plasma: {
          50: "#E6FBFF",
          100: "#B0F3FF",
          200: "#6DE7FF",
          300: "#33D5FF",
          400: "#00BEEC",
          500: "#0099C2",
          600: "#006E8E",
        },
        violet: {
          500: "#7C3AED",
          600: "#6D28D9",
        },
        ember: "#FF5C2B",
        emerald: "#1FE08F",
        crimson: "#FF3D5A",
        amber: "#FFB020",
      },
      fontFamily: {
        sans: ["Manrope", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
        display: ["Instrument Serif", "ui-serif", "serif"],
      },
      backdropBlur: {
        xs: "2px",
      },
      animation: {
        "pulse-glow": "pulse-glow 2.4s ease-in-out infinite",
        "ticker": "ticker 60s linear infinite",
        "shimmer": "shimmer 2s linear infinite",
        "fade-up": "fade-up 0.5s cubic-bezier(0.22,1,0.36,1) forwards",
      },
      keyframes: {
        "pulse-glow": {
          "0%,100%": { opacity: 0.7, boxShadow: "0 0 0 0 rgba(0,190,236,0.4)" },
          "50%": { opacity: 1, boxShadow: "0 0 0 8px rgba(0,190,236,0)" },
        },
        ticker: {
          "0%": { transform: "translateX(0%)" },
          "100%": { transform: "translateX(-50%)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "fade-up": {
          "0%": { opacity: 0, transform: "translateY(8px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

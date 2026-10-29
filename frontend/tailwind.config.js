/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        // Black base surfaces (used by the app shell + overlay).
        ink: {
          950: "#06050a",
          900: "#0a090f",
          850: "#0f0d15",
          800: "#15121b",
          700: "#1d1925",
        },
        // Warm gold accent ramp: champagne -> gold -> bronze.
        brand: {
          50: "#fffbeb",
          100: "#fef3c7",
          200: "#fde68a",
          300: "#fcd34d",
          400: "#fbbf24",
          500: "#f59e0b",
          600: "#d97706",
          700: "#b45309",
          800: "#92400e",
          900: "#78350f",
        },
      },
      backgroundImage: {
        "gradient-brand":
          "linear-gradient(120deg, #fde68a 0%, #fbbf24 46%, #d97706 100%)",
        "gradient-brand-soft":
          "linear-gradient(120deg, rgba(253,230,138,0.16) 0%, rgba(251,191,36,0.16) 46%, rgba(217,119,6,0.16) 100%)",
        "gradient-copilot":
          "linear-gradient(120deg, rgba(253,230,138,0.14), rgba(245,158,11,0.14))",
        "gradient-gold": "linear-gradient(120deg, #fcd34d 0%, #f59e0b 100%)",
        "gradient-sheen":
          "linear-gradient(110deg, transparent 25%, rgba(255,255,255,0.28) 50%, transparent 75%)",
      },
      boxShadow: {
        glow: "0 10px 44px -14px rgba(251,191,36,0.55)",
        "glow-teal": "0 10px 44px -14px rgba(253,230,138,0.45)",
        "glow-gold": "0 10px 44px -14px rgba(245,158,11,0.5)",
        panel: "0 24px 70px -30px rgba(0,0,0,0.95)",
        "inner-top": "inset 0 1px 0 0 rgba(255,255,255,0.08)",
        raised:
          "0 1px 0 0 rgba(255,255,255,0.08), 0 18px 40px -24px rgba(0,0,0,0.95)",
      },
      keyframes: {
        "aurora-a": {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "33%": { transform: "translate3d(6%, -4%, 0) scale(1.12)" },
          "66%": { transform: "translate3d(-5%, 5%, 0) scale(0.94)" },
        },
        "aurora-b": {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1.05)" },
          "40%": { transform: "translate3d(-7%, 6%, 0) scale(0.9)" },
          "75%": { transform: "translate3d(5%, -6%, 0) scale(1.15)" },
        },
        "grid-pan": {
          "0%": { backgroundPosition: "0 0" },
          "100%": { backgroundPosition: "60px 60px" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-6px)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        sheen: {
          "0%": { transform: "translateX(-130%)" },
          "100%": { transform: "translateX(130%)" },
        },
        breathe: {
          "0%, 100%": { opacity: "0.55", transform: "scale(1)" },
          "50%": { opacity: "1", transform: "scale(1.06)" },
        },
        "ping-soft": {
          "0%": { transform: "scale(0.9)", opacity: "0.9" },
          "80%, 100%": { transform: "scale(2.2)", opacity: "0" },
        },
        wave: {
          "0%, 100%": { transform: "scaleY(0.35)" },
          "50%": { transform: "scaleY(1)" },
        },
        "spin-slow": {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
        rise: {
          "0%": { opacity: "0", transform: "translateY(14px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          // Use the independent `scale` property, not `transform`. Several
          // centered dialogs combine this animation with
          // `-translate-x-1/2 -translate-y-1/2`; animating `transform` here
          // would overwrite that centering once the animation fills.
          "0%": { opacity: "0", scale: "0.96" },
          "100%": { opacity: "1", scale: "1" },
        },
        "cue-in": {
          "0%": { opacity: "0", transform: "translateY(10px) scale(0.96)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "cue-out": {
          "0%": { opacity: "1", transform: "translateY(0) scale(1)" },
          "100%": { opacity: "0", transform: "translateY(-6px) scale(0.97)" },
        },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(56,189,248,0.45)" },
          "70%": { boxShadow: "0 0 0 14px rgba(56,189,248,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(56,189,248,0)" },
        },
        "border-flash": {
          "0%, 100%": { borderColor: "rgba(244,63,94,0.25)" },
          "50%": { borderColor: "rgba(244,63,94,0.9)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "msg-in-left": {
          "0%": { opacity: "0", transform: "translateX(-12px) scale(0.97)" },
          "100%": { opacity: "1", transform: "translateX(0) scale(1)" },
        },
        "msg-in-right": {
          "0%": { opacity: "0", transform: "translateX(12px) scale(0.97)" },
          "100%": { opacity: "1", transform: "translateX(0) scale(1)" },
        },
        "typing-dot": {
          "0%, 60%, 100%": { transform: "translateY(0)", opacity: "0.4" },
          "30%": { transform: "translateY(-4px)", opacity: "1" },
        },
        "orb-drift": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "33%": { transform: "translate(18px, -12px) scale(1.08)" },
          "66%": { transform: "translate(-14px, 10px) scale(0.95)" },
        },
      },
      animation: {
        "aurora-a": "aurora-a 22s ease-in-out infinite",
        "aurora-b": "aurora-b 28s ease-in-out infinite",
        "grid-pan": "grid-pan 24s linear infinite",
        float: "float 6s ease-in-out infinite",
        shimmer: "shimmer 2.4s linear infinite",
        sheen: "sheen 1.1s ease-in-out",
        breathe: "breathe 3.4s ease-in-out infinite",
        "ping-soft": "ping-soft 2.2s cubic-bezier(0,0,0.2,1) infinite",
        wave: "wave 1.1s ease-in-out infinite",
        "spin-slow": "spin-slow 18s linear infinite",
        rise: "rise 0.5s cubic-bezier(0.16,1,0.3,1) both",
        "scale-in": "scale-in 0.28s cubic-bezier(0.16,1,0.3,1) both",
        "cue-in": "cue-in 0.35s cubic-bezier(0.16, 1, 0.3, 1)",
        "cue-out": "cue-out 0.4s ease-in forwards",
        "pulse-ring": "pulse-ring 1.6s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "border-flash": "border-flash 0.9s ease-in-out 3",
        "fade-in": "fade-in 0.3s ease-out",
        "msg-in-left": "msg-in-left 0.32s cubic-bezier(0.16, 1, 0.3, 1)",
        "msg-in-right": "msg-in-right 0.32s cubic-bezier(0.16, 1, 0.3, 1)",
        "typing-dot": "typing-dot 1.1s ease-in-out infinite",
        "orb-drift": "orb-drift 14s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

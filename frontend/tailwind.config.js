/** @type {import('tailwindcss').Config} */
// Every color below resolves through a CSS variable (see src/index.css),
// so the same utility classes (bg-canvas, text-ink-primary, etc.) work
// unchanged in both dark and light mode - only the variable values differ
// per [data-theme]. The `<alpha-value>` placeholder is Tailwind's standard
// mechanism for keeping opacity modifiers (e.g. bg-signal/30) working with
// CSS-variable-based colors.
function withOpacity(variableName) {
  return `rgb(var(${variableName}) / <alpha-value>)`;
}

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        canvas: withOpacity("--color-canvas"),
        surface: withOpacity("--color-surface"),
        raised: withOpacity("--color-raised"),
        border: {
          DEFAULT: withOpacity("--color-border"),
          strong: withOpacity("--color-border-strong"),
        },
        ink: {
          primary: withOpacity("--color-ink-primary"),
          secondary: withOpacity("--color-ink-secondary"),
          muted: withOpacity("--color-ink-muted"),
        },
        signal: {
          DEFAULT: withOpacity("--color-signal"),
          dim: withOpacity("--color-signal-dim"),
        },
        amber: {
          DEFAULT: withOpacity("--color-amber"),
          dim: withOpacity("--color-amber-dim"),
        },
        danger: {
          DEFAULT: withOpacity("--color-danger"),
          dim: withOpacity("--color-danger-dim"),
        },
        violet: {
          DEFAULT: withOpacity("--color-violet"),
          dim: withOpacity("--color-violet-dim"),
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      borderRadius: {
        DEFAULT: "6px",
      },
    },
  },
  plugins: [],
};

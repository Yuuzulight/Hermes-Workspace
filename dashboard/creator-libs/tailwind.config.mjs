/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.tsx"],
  theme: {
    extend: {
      colors: {
        foreground: "#18181b",
        muted: "#71717a",
        accent: "#2563eb",
        border: "#e4e4e7",
        card: "#ffffff",
      },
    },
  },
  plugins: [],
};
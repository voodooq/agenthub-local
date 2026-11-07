/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: 'var(--bg-main)',
        foreground: 'var(--color-foreground)',
        'muted-foreground': 'var(--color-foreground-muted)',
        surface: {
          50: 'var(--bg-surface-50)',
          100: 'var(--bg-surface-100)',
          200: 'var(--bg-surface-200)',
          300: 'var(--bg-surface-300)',
          sidebar: 'var(--bg-sidebar)',
          card: 'var(--bg-card)',
          DEFAULT: 'var(--bg-surface-100)',
        },
        border: 'var(--color-border)',
        'border-hover': 'var(--color-border-hover)',
        accent: {
          DEFAULT: 'var(--color-primary)',
          hover: 'var(--color-primary-hover)',
          subtle: 'var(--color-primary-subtle)',
          glow: 'rgba(16, 185, 129, 0.25)',
        },
        emerald: {
          glow: 'rgba(16, 185, 129, 0.25)',
        },
      },
      fontFamily: {
        sans: ['Google Sans', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      animation: {
        'pulse-subtle': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
        'scale-in': 'scaleIn 0.2s cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.96)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
      },
    },
  },
  plugins: [],
};

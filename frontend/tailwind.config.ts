import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Existing blue utility names now use the shared PKU red palette.
        blue: {
          50: 'var(--pku-red-soft)',
          100: '#f4e4ea',
          200: '#e7cbd5',
          300: '#d8a8b8',
          400: '#bd718d',
          500: '#a84f6f',
          600: 'var(--pku-red)',
          700: 'var(--pku-red-deep)',
          800: '#5b1228',
          900: '#44101f',
        },
      },
    },
  },
  plugins: [],
}
export default config

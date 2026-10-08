/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx,js,jsx}'],
  theme: {
    extend: {
      // Keep existing blue utility names compatible while making the shared
      // interaction palette consistent with the supplied maroon/red deck.
      colors: {
        blue: {
          50: 'var(--pku-red-soft)',
          100: '#f9f5f5',
          200: '#e0cfcf',
          300: '#d8a8b0',
          400: '#c16b7a',
          500: '#c41230',
          600: 'var(--pku-red)',
          700: 'var(--pku-red-deep)',
          800: '#4d101b',
          900: '#321016',
        },
      },
    },
  },
  plugins: [],
}

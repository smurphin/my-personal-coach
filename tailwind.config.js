// tailwind.config.js

/** @type {import('tailwindcss').Config} */
module.exports = {
  // THIS IS THE CRUCIAL FIX:
  content: ["./templates/**/*.html"],

  theme: {
    extend: {
      colors: {
        'brand-dark': '#131313',
        'brand-gray': '#2E2F33',
        'brand-light-gray': '#E0E0E0',
        'brand-blue': '#00A9FF',
        'brand-blue-hover': '#0087CC',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
      typography: ({ theme }) => ({
      invert: {
        css: {
          '--tw-prose-headings': theme('colors.brand-blue'),
          '--tw-prose-links': theme('colors.brand-blue'),
        },
      },
    }),
      backgroundImage: {
        'gradient-radial': 'radial-gradient(circle at top, var(--tw-gradient-stops))',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'), // This is the new line
  ],
}
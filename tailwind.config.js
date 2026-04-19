module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        base:    '#F7F8FA',
        surface: '#FFFFFF',
        subtle:  '#FFFFFF',
        border:  '#E5E7EB',
        accent:  '#0D9488',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      }
    }
  },
  plugins: [],
}

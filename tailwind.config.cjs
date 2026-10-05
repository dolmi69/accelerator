/** Build once; Django serves the resulting CSS without a browser compiler. */
module.exports = {
  content: ["./templates/**/*.html", "./static/founder/js/**/*.js", "./founder/**/*forms.py"],
  theme: { extend: {} },
  plugins: [],
};

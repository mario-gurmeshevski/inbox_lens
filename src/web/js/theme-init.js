(function () {
  var KEY = "inbox-lens-theme";
  var pref = null;
  try {
    pref = localStorage.getItem(KEY);
  } catch (e) {}

  var dark;
  if (pref === "light" || pref === "dark") {
    dark = pref === "dark";
  } else {
    dark = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
      : false;
  }

  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();

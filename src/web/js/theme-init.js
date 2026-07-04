(function () {
  let pref = document.documentElement.getAttribute("data-theme-pref");
  if (pref !== "light" && pref !== "dark") {
    pref = "system";
  }
  let dark;
  if (pref === "light" || pref === "dark") {
    dark = pref === "dark";
  } else {
    dark = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
      : false;
  }
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();

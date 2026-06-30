(function () {
  var STORAGE_KEY = "inbox-lens-settings-tab";
  var tabs = [].slice.call(document.querySelectorAll(".settings-tab"));
  var panels = [].slice.call(document.querySelectorAll("[data-tab-panel]"));
  if (!tabs.length || !panels.length) return;

  function activate(name) {
    var found = false;
    tabs.forEach(function (t) {
      var match = t.getAttribute("data-tab") === name;
      t.classList.toggle("is-active", match);
      if (match) {
        t.setAttribute("aria-selected", "true");
        found = true;
      } else {
        t.setAttribute("aria-selected", "false");
      }
    });
    panels.forEach(function (p) {
      if (p.getAttribute("data-tab-panel") === name) p.removeAttribute("hidden");
      else p.setAttribute("hidden", "");
    });
    if (found) {
      try {
        localStorage.setItem(STORAGE_KEY, name);
      } catch (e) {}
    }
    return found;
  }

  tabs.forEach(function (t) {
    t.addEventListener("click", function () {
      activate(t.getAttribute("data-tab"));
    });
  });

  var saved = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch (e) {}
  if (saved) {
    if (!activate(saved)) activate("system");
  }
})();

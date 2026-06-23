(function () {
  var box = document.getElementById("kw-status");
  if (!box) return;
  try {
    var url = new URL(window.location.href);
    if (url.searchParams.has("import")) {
      url.searchParams.delete("import");
      window.history.replaceState({}, "", url.toString());
    }
  } catch (e) {}
  setTimeout(function () {
    if (!document.body.contains(box)) return;
    box.style.transition = "opacity 0.3s ease";
    box.style.opacity = "0";
    setTimeout(function () {
      if (box.parentElement) box.remove();
    }, 300);
  }, 6000);
})();

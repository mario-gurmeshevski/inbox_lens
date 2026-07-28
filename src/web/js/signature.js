(function () {
  const sigBody = document.getElementById("signature-body");
  if (!sigBody) return;

  const toolbar = document.querySelector("[data-sig-toolbar]");
  if (!toolbar) return;

  toolbar.querySelectorAll(".btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!window.composeToolbar) return;
      const kind = btn.getAttribute("data-md");
      if (kind === "bold") window.composeToolbar.wrap(sigBody, "**");
      else if (kind === "italic") window.composeToolbar.wrap(sigBody, "*");
      else if (kind === "list") window.composeToolbar.insertLinePrefix(sigBody, "- ");
      else if (kind === "link") window.composeToolbar.insertLink(sigBody);
      sigBody.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
})();

(function () {
  const COMPOSE_MODAL_ID = "compose-modal";
  const COMPOSE_BODY_ID = "compose-modal-body";

  let lastFocused = null;

  function getModal() {
    return document.getElementById(COMPOSE_MODAL_ID);
  }

  function getFocusables() {
    const modal = getModal();
    if (!modal) return [];
    const nodes = modal.querySelectorAll(
      'button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])'
    );
    return Array.prototype.filter.call(nodes, function (el) {
      return !el.disabled && el.offsetParent !== null;
    });
  }

  function openComposeModal(html) {
    lastFocused = document.activeElement;
    const body = document.getElementById(COMPOSE_BODY_ID);
    if (body) body.innerHTML = html;
    if (typeof window.lucide !== "undefined" && window.lucide.createIcons) {
      window.lucide.createIcons();
    }
    if (typeof openModal === "function") openModal(COMPOSE_MODAL_ID);
    setTimeout(function () {
      const focusables = getFocusables();
      if (focusables.length) focusables[0].focus();
    }, 0);
  }

  function closeComposeModal() {
    if (typeof closeModal === "function") closeModal(COMPOSE_MODAL_ID);
    const body = document.getElementById(COMPOSE_BODY_ID);
    if (body) body.innerHTML = "";
    if (lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
      lastFocused = null;
    }
  }

  function loadCompose(mode, hash) {
    fetch("/partials/compose/" + encodeURIComponent(hash) + "?mode=" + encodeURIComponent(mode), {
      headers: { Accept: "text/html" },
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("Failed to load compose form");
        return resp.text();
      })
      .then(openComposeModal)
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Could not open composer", "error");
      });
  }

  function submitCompose(form) {
    const hash = form.getAttribute("data-compose-hash");
    const sendBtn = document.getElementById("compose-send-btn");
    if (sendBtn) sendBtn.disabled = true;

    const params = {
      to: (form.elements["to"] || {}).value || "",
      subject: (form.elements["subject"] || {}).value || "",
      body: (form.elements["body"] || {}).value || "",
      mode: form.getAttribute("data-compose-mode") || "reply",
    };

    if (!params.to || !params.subject || !params.body) {
      if (typeof window.showToast === "function") window.showToast("To, subject, and body are required", "error");
      if (sendBtn) sendBtn.disabled = false;
      return;
    }

    window
      .postForm("/emails/" + encodeURIComponent(hash) + "/send", params)
      .then(function (resp) {
        const tone = resp.headers.get("X-Toast-Tone") || (resp.ok ? "success" : "error");
        if (resp.ok && tone === "success") closeComposeModal();
      })
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Network error while sending", "error");
      })
      .finally(function () {
        if (sendBtn) sendBtn.disabled = false;
      });
  }

  document.addEventListener("click", function (e) {
    const opener = e.target.closest("[data-compose-open]");
    if (opener) {
      e.preventDefault();
      loadCompose(opener.getAttribute("data-compose-open"), opener.getAttribute("data-compose-hash"));
      return;
    }
  });

  document.addEventListener("submit", function (e) {
    if (e.target && e.target.id === "compose-form") {
      e.preventDefault();
      submitCompose(e.target);
    }
  });

  document.addEventListener("keydown", function (e) {
    const modal = getModal();
    if (!modal || modal.hasAttribute("hidden")) return;
    if (e.key === "Escape") {
      closeComposeModal();
      return;
    }
    if (e.key === "Tab") {
      const focusables = getFocusables();
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  if (typeof window !== "undefined") {
    window.closeComposeModal = closeComposeModal;
  }
})();

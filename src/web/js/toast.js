(function () {
  const REGION_ID = "toast-region";
  const DEFAULT_DURATION = 2200;
  const MAX_TOASTS = 4;
  const LEAVE_MS = 300;
  const TONES = {
    success: "is-success",
    error: "is-error",
    info: "is-info",
    warning: "is-warning",
  };
  const ICONS = {
    success: "check-circle",
    error: "alert-circle",
    info: "info",
    warning: "alert-triangle",
  };

  function ensureRegion() {
    let region = document.getElementById(REGION_ID);
    if (!region) {
      region = document.createElement("div");
      region.id = REGION_ID;
      region.className = "toast-region";
      region.setAttribute("role", "status");
      region.setAttribute("aria-live", "polite");
      document.body.appendChild(region);
    }
    return region;
  }

  // Remove a toast, cancelling any pending timers so neither auto-dismiss
  function dismiss(el) {
    if (!el) return;
    if (el._leaveTimer) { window.clearTimeout(el._leaveTimer); el._leaveTimer = null; }
    if (el._removeTimer) { window.clearTimeout(el._removeTimer); el._removeTimer = null; }
    if (el.classList.contains("is-leaving") || !el.parentNode) {
      if (el.parentNode) el.parentNode.removeChild(el);
      return;
    }
    el.classList.add("is-leaving");
    el._removeTimer = window.setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, LEAVE_MS);
  }

  function showToast(message, tone, duration) {
    if (!message) return;
    const t = (tone && TONES[tone]) ? tone : "success";
    const ms = typeof duration === "number" ? duration : DEFAULT_DURATION;

    const region = ensureRegion();

    // Enforce the stack cap: drop the oldest visible toast when over limit.
    const visible = region.querySelectorAll(".toast:not(.is-leaving)");
    while (visible.length >= MAX_TOASTS) {
      dismiss(visible[0]);
      visible[0] = visible[visible.length - 1];
      visible.length--;
    }

    const el = document.createElement("div");
    el.className = "toast " + TONES[t];
    el.setAttribute("role", "status");

    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", ICONS[t]);
    icon.className = "toast-icon icon";
    el.appendChild(icon);

    const text = document.createElement("span");
    text.className = "toast-message";
    text.textContent = message;
    el.appendChild(text);

    region.appendChild(el);
    if (window.lucide) lucide.createIcons();

    el._leaveTimer = window.setTimeout(function () {
      dismiss(el);
    }, ms);
  }

  window.showToast = showToast;

  // Auto-show toasts from any HTMX response carrying an X-Toast header.
  document.body.addEventListener("htmx:afterRequest", function (e) {
    const xhr = e.detail && e.detail.xhr;
    if (!xhr || !xhr.getResponseHeader) return;
    const msg = xhr.getResponseHeader("X-Toast");
    if (!msg) return;
    const toneHeader = xhr.getResponseHeader("X-Toast-Tone");
    const failed = e.detail.failed;
    const tone = toneHeader || (failed ? "error" : "success");
    showToast(msg, tone);
  });
})();

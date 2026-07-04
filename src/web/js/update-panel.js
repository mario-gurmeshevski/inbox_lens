(function () {
  function makeIcon(name, cls) {
    const i = document.createElement("i");
    i.setAttribute("data-lucide", name);
    if (cls) i.className = cls;
    return i;
  }

  function makeTextSpan(text) {
    const span = document.createElement("span");
    span.textContent = text == null ? "" : String(text);
    return span;
  }

  function setupDebounce() {
    if (window.__debounceUpdateCheck) return;
    window.__debounceUpdateCheck = true;
    const COOLDOWN = 30000;
    document.addEventListener("submit", function (e) {
      const form = e.target;
      if (!form || form.getAttribute("hx-post") !== "/api/update/check") return;
      const btn = form.querySelector('button[type="submit"]');
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.style.opacity = "0.6";
      setTimeout(function () {
        btn.disabled = false;
        btn.style.opacity = "";
      }, COOLDOWN);
    });
  }

  function setupRestartPolling() {
    const box = document.getElementById("restart-status");
    if (!box || box.dataset.ready) return;
    box.dataset.ready = "1";

    const savedVersion = box.getAttribute("data-version") || "";
    const initialMessage = box.getAttribute("data-message") || "";
    let maxTries = 120;

    function isDone(data) {
      // New version detected, or restart cycle completed, or server moved to a failed phase.
      if (data && data.current_version && data.current_version !== savedVersion) return true;
      if (data && data.update_state) {
        const phase = data.update_state.phase;
        if (phase === "idle") return true;
        if (phase === "failed") return true;
      }
      return false;
    }

    function showSpinner() {
      box.className = "status-box status-info is-spaced";
      box.replaceChildren(
        makeIcon("loader", "icon-md spin"),
        makeTextSpan(initialMessage)
      );
      const actions = document.getElementById("restart-actions");
      if (actions) actions.remove();
      if (window.lucide) lucide.createIcons();
    }

    function showTimeout() {
      box.className = "status-box status-error is-spaced";
      box.replaceChildren(
        makeIcon("alert-circle", "icon-md"),
        makeTextSpan(
          "Restart is taking longer than expected. The update may still be finishing — try reloading, or check the host logs if it doesn't come back."
        )
      );
      if (document.getElementById("restart-actions")) return;
      const actions = document.createElement("div");
      actions.id = "restart-actions";
      actions.className = "settings-network-info";
      const retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.id = "restart-retry";
      retryBtn.className = "btn btn-sm";
      retryBtn.appendChild(makeIcon("rotate-cw", "icon-sm"));
      retryBtn.appendChild(document.createTextNode(" Retry Polling"));
      const reloadBtn = document.createElement("button");
      reloadBtn.type = "button";
      reloadBtn.id = "restart-reload";
      reloadBtn.className = "btn btn-sm";
      reloadBtn.appendChild(makeIcon("refresh-cw", "icon-sm"));
      reloadBtn.appendChild(document.createTextNode(" Reload Page"));
      actions.replaceChildren(retryBtn, reloadBtn);
      box.insertAdjacentElement("afterend", actions);
      document.getElementById("restart-retry").addEventListener("click", function () {
        maxTries = 120;
        showSpinner();
        poll();
      });
      document.getElementById("restart-reload").addEventListener("click", function () {
        window.location.reload();
      });
      if (window.lucide) lucide.createIcons();
    }

    function poll() {
      fetch("/api/update/status")
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (isDone(data)) {
            window.location.reload();
            return;
          }
          if (--maxTries > 0) setTimeout(poll, 1500);
          else showTimeout();
        })
        .catch(function () {
          if (--maxTries > 0) setTimeout(poll, 1500);
          else showTimeout();
        });
    }

    poll();
  }

  function setupCopyError() {
    if (window.__copyUpdateError) return;
    window.__copyUpdateError = true;
    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-copy-error]");
      if (!btn) return;
      const text = btn.getAttribute("data-copy-error") || "";
      if (!text) return;
      const originalNodes = Array.prototype.slice.call(btn.childNodes);
      function restore() {
        btn.replaceChildren.apply(btn, originalNodes);
        if (window.lucide) lucide.createIcons();
      }
      function flash(msg) {
        btn.replaceChildren(makeIcon("copy", "icon-sm"), document.createTextNode(" " + msg));
        setTimeout(restore, 1500);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () {
            flash("Copied");
          },
          function () {
            flash("Copy failed");
          }
        );
      } else {
        flash("Copy unavailable");
      }
    });
  }

  setupDebounce();
  setupRestartPolling();
  setupCopyError();

  document.addEventListener("htmx:afterSettle", setupRestartPolling);
})();

lucide.createIcons();
document.body.addEventListener("htmx:afterSwap", function () {
  lucide.createIcons();
});

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.removeAttribute("hidden");
  document.body.style.overflow = "hidden";
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.setAttribute("hidden", "");
  document.body.style.overflow = "";
}

window.postForm = function (url, params) {
  const body = new URLSearchParams();
  Object.keys(params || {}).forEach(function (k) {
    const v = params[k];
    if (Array.isArray(v)) {
      v.forEach(function (item) { body.append(k, item); });
    } else {
      body.append(k, v);
    }
  });
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  }).then(function (resp) {
    const msg = resp.headers.get("X-Toast");
    if (msg && typeof window.showToast === "function") {
      const tone = resp.headers.get("X-Toast-Tone") || (resp.ok ? "success" : "error");
      window.showToast(msg, tone);
    }
    return resp;
  });
};

function openAccountModal() {
  openModal("account-modal");
}

function closeAccountModal() {
  closeModal("account-modal");
  const body = document.getElementById("account-modal-body");
  if (body) body.innerHTML = "";
}

document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") {
    const confirmModal = document.getElementById("confirm-modal");
    if (confirmModal && !confirmModal.hasAttribute("hidden")) return;
    const modal = document.getElementById("account-modal");
    if (modal && !modal.hasAttribute("hidden")) {
      closeAccountModal();
      return;
    }
    const composeModal = document.getElementById("compose-modal");
    if (composeModal && !composeModal.hasAttribute("hidden")) {
      if (typeof window.closeComposeModal === "function") window.closeComposeModal();
      else closeModal("compose-modal");
    }
  }
});

document.addEventListener("click", function (e) {
  const closer = e.target.closest("[data-modal-close]");
  if (closer) {
    const id = closer.getAttribute("data-modal-close");
    if (!id) return;
    if (id === "account-modal") {
      closeAccountModal();
    } else if (id === "compose-modal" && typeof window.closeComposeModal === "function") {
      window.closeComposeModal();
    } else if (typeof closeModal === "function") {
      closeModal(id);
    }
    return;
  }
  const opener = e.target.closest("[data-modal-open]");
  if (opener) {
    const id = opener.getAttribute("data-modal-open");
    if (!id) return;
    if (id === "account-modal") {
      openAccountModal();
    } else if (typeof openModal === "function") {
      openModal(id);
    }
  }
});

document.addEventListener("htmx:afterRequest", function (e) {
  const target = e.detail && e.detail.target;
  if (
    target &&
    target.id === "account-modal-body" &&
    e.detail.failed &&
    e.detail.xhr &&
    e.detail.xhr.status >= 400
  ) {
    closeAccountModal();
  }
});

function togglePassword(id) {
  const input = document.getElementById(id);
  const btn = input.parentElement.querySelector(".password-toggle");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.innerHTML =
    '<i data-lucide="' +
    (show ? "eye-off" : "eye") +
    '" style="width: 16px; height: 16px"></i>';
  lucide.createIcons();
}

function copyToClipboard(btn, text) {
  if (!btn) return;
  const icon = btn.querySelector("i[data-lucide]");
  const label = Array.prototype.find.call(btn.childNodes, function (n) {
    return n.nodeType === 3 && n.textContent.trim().length > 0;
  });
  const originalIcon = icon ? icon.getAttribute("data-lucide") : "";
  const originalText = label ? label.textContent : "";

  function revert() {
    if (icon) {
      icon.setAttribute("data-lucide", originalIcon);
      lucide.createIcons();
    }
    if (label) label.textContent = originalText;
    btn.disabled = false;
    btn.classList.remove("btn-success");
    btn.removeAttribute("data-copied");
  }

  function flash(success) {
    if (icon) {
      icon.setAttribute("data-lucide", success ? "check" : "x");
      lucide.createIcons();
    }
    if (label) label.textContent = success ? "Copied" : "Failed";
    if (success) btn.classList.add("btn-success");
    btn.setAttribute("data-copied", "true");
    setTimeout(revert, 2000);
  }

  let promise;
  if (navigator.clipboard && window.isSecureContext) {
    promise = navigator.clipboard.writeText(text);
  } else {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "absolute";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      promise = ok
        ? Promise.resolve()
        : Promise.reject(new Error("execCommand failed"));
    } catch (e) {
      promise = Promise.reject(e);
    }
  }

  btn.disabled = true;
  promise.then(
    function () {
      flash(true);
    },
    function () {
      flash(false);
    },
  );
}

function kwEdit(span) {
  const chip = span.closest(".chip");
  if (!chip) return;
  span.style.display = "none";
  const form = chip.querySelector(".edit-form");
  if (!form) return;
  form.classList.add("is-editing");
  const input = form.querySelector(".edit-input");
  input.setAttribute("size", Math.max(4, Math.min(20, input.value.length + 1)));
  input.focus();
  const val = input.value.length;
  input.setSelectionRange(val, val);
}

function kwEditKey(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    e.target.blur();
  }
  if (e.key === "Enter") {
    e.preventDefault();
    e.target.closest("form").requestSubmit();
  }
}

function kwCancelEdit(input) {
  const form = input.closest(".edit-form");
  if (!form) return;
  const old = form.querySelector('input[name="old_word"]');
  if (old) input.value = old.value;
  form.classList.remove("is-editing");
  const chip = form.closest(".chip");
  const span = chip && chip.querySelector(".word");
  if (span) span.style.display = "";
}

(function () {
  const fileInput = document.getElementById("kw-import-file");
  if (!fileInput) return;
  const form = fileInput.closest(".import-form");
  const label = fileInput.parentElement.querySelector(".file-name");

  fileInput.addEventListener("change", function () {
    const name =
      fileInput.files && fileInput.files[0]
        ? fileInput.files[0].name
        : "No file chosen";
    if (label) {
      label.textContent = name;
      label.style.color = "";
    }
  });

  if (form) {
    form.addEventListener("submit", function (e) {
      if (!fileInput.files || !fileInput.files[0]) {
        e.preventDefault();
        if (label) {
          label.textContent = "Please choose a file first.";
          label.style.color = "var(--danger, #dc2626)";
        }
        fileInput.focus();
      }
    });
  }
})();

(function () {
  let sseRetries = 0;
  const maxRetries = 10;

  function reconnectSSE() {
    let container = document.querySelector("[sse-connect]");
    if (!container) container = document.querySelector(".container");
    if (container) {
      sseRetries++;
      console.log("SSE reconnecting... attempt " + sseRetries);
      htmx.removeExtension("sse");
      htmx.defineExtension("sse", htmx._sseExtension);
      htmx.process(container);
    }
  }

  document.body.addEventListener("htmx:sseError", function (e) {
    if (sseRetries < maxRetries) {
      const delay = Math.min(5000 * (sseRetries + 1), 30000);
      setTimeout(reconnectSSE, delay);
    }
  });

  document.body.addEventListener("htmx:sseOpen", function () {
    sseRetries = 0;
  });
})();

(function () {
  const modal = document.getElementById("confirm-modal");
  if (!modal) return;
  const titleEl = document.getElementById("confirm-title");
  const msgEl = document.getElementById("confirm-message");
  const iconEl = document.getElementById("confirm-icon");
  const okBtn = document.getElementById("confirm-ok-btn");
  let lastFocus = null;
  let current = null;

  const ICONS = {
    danger: "alert-triangle",
    warning: "alert-triangle",
  };

  function open(opts) {
    opts = opts || {};
    const tone = opts.tone || "danger";
    titleEl.textContent = opts.title || "Are you sure?";
    msgEl.textContent = opts.message || "";
    iconEl.className =
      "confirm-icon " + (tone === "warning" ? "is-warning" : "is-danger");
    iconEl.innerHTML =
      '<i data-lucide="' + (ICONS[tone] || "alert-triangle") + '" class="icon-md"></i>';
    okBtn.textContent = opts.confirmLabel || "Confirm";
    okBtn.className = tone === "danger" ? "btn btn-danger" : "btn";
    lucide.createIcons();

    lastFocus = document.activeElement;
    modal.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    setTimeout(function () {
      okBtn.focus();
    }, 0);
  }

  function close(result) {
    modal.setAttribute("hidden", "");
    document.body.style.overflow = "";
    const resolve = current;
    current = null;
    if (resolve) resolve(result);
    if (lastFocus && typeof lastFocus.focus === "function") {
      lastFocus.focus();
      lastFocus = null;
    }
  }

  window.confirmDialog = function (opts) {
    return new Promise(function (resolve) {
      current = resolve;
      open(opts);
    });
  };

  okBtn.addEventListener("click", function () {
    close(true);
  });

  modal.querySelectorAll("[data-confirm-cancel]").forEach(function (el) {
    el.addEventListener("click", function () {
      close(false);
    });
  });

  document.addEventListener("keydown", function (e) {
    if (!current) return;
    if (e.key === "Escape") {
      e.preventDefault();
      close(false);
    }
  });

  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) {
      return;
    }
    e.preventDefault();
    window
      .confirmDialog({
        title: form.getAttribute("data-confirm-title") || "Are you sure?",
        message: form.getAttribute("data-confirm") || "",
        tone: form.getAttribute("data-confirm-tone") || "danger",
        confirmLabel: form.getAttribute("data-confirm-label") || "Confirm",
      })
      .then(function (ok) {
        if (ok) {
          form.removeAttribute("data-confirm");
          form.submit();
        }
      });
  }, true);

  document.body.addEventListener("htmx:confirm", function (e) {
    const question = e.detail && e.detail.question;
    if (!question) return;
    e.preventDefault();
    const elt = e.detail.elt;
    window
      .confirmDialog({
        title: (elt && elt.getAttribute("data-confirm-title")) || "Are you sure?",
        message: question,
        tone: (elt && elt.getAttribute("data-confirm-tone")) || "danger",
        confirmLabel: (elt && elt.getAttribute("data-confirm-label")) || "Confirm",
      })
      .then(function (ok) {
        if (ok && e.detail.issueRequest) {
          e.detail.issueRequest(true);
        }
      });
  });
})();

(function () {
  const mq = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

  const bc = (typeof BroadcastChannel !== "undefined")
    ? new BroadcastChannel("inbox-lens-theme")
    : null;

  function getPref() {
    const v = document.documentElement.getAttribute("data-theme-pref");
    if (v === "light" || v === "dark" || v === "system") return v;
    return "system";
  }

  function setPref(p, broadcast) {
    document.documentElement.setAttribute("data-theme-pref", p);
    if (broadcast && bc) bc.postMessage(p);
  }

  function apply() {
    const p = getPref();
    const resolved = (p === "light" || p === "dark") ? p : (mq && mq.matches ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", resolved);
    const select = document.getElementById("theme-select");
    if (select && select.value !== p) select.value = p;
  }

  if (bc) {
    bc.onmessage = function (e) {
      const p = e.data;
      if (p === "light" || p === "dark" || p === "system") {
        setPref(p, false); // local-only; don't re-broadcast
        apply();
      }
    };
  }

  function init() {
    apply();
    const select = document.getElementById("theme-select");
    if (select) {
      select.value = getPref();
      select.addEventListener("change", function () {
        const prev = getPref();
        setPref(select.value, true);
        apply();
        const form = select.form;
        if (!form) return;
        function onAfter(e) {
          const detail = e.detail || {};
          const path = detail.requestConfig && detail.requestConfig.path;
          if (path !== "/settings/theme") return;
          form.removeEventListener("htmx:afterRequest", onAfter);
          if (detail.failed) {
            setPref(prev, true);
            apply();
          }
        }
        form.addEventListener("htmx:afterRequest", onAfter);
      });
    }
    if (mq) mq.addEventListener("change", function () { if (getPref() === "system") apply(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

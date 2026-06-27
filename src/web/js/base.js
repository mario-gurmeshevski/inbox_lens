lucide.createIcons();
document.body.addEventListener("htmx:afterSwap", function () {
  lucide.createIcons();
});

function openAccountModal() {
  var modal = document.getElementById("account-modal");
  if (!modal) return;
  modal.removeAttribute("hidden");
  document.body.style.overflow = "hidden";
}

function closeAccountModal() {
  var modal = document.getElementById("account-modal");
  if (!modal) return;
  modal.setAttribute("hidden", "");
  document.body.style.overflow = "";
  var body = document.getElementById("account-modal-body");
  if (body) body.innerHTML = "";
}

document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") {
    var modal = document.getElementById("account-modal");
    if (modal && !modal.hasAttribute("hidden")) {
      closeAccountModal();
    }
  }
});

document.addEventListener("htmx:afterRequest", function (e) {
  var target = e.detail && e.detail.target;
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
  var icon = btn.querySelector("i[data-lucide]");
  var label = Array.prototype.find.call(btn.childNodes, function (n) {
    return n.nodeType === 3 && n.textContent.trim().length > 0;
  });
  var originalIcon = icon ? icon.getAttribute("data-lucide") : "";
  var originalText = label ? label.textContent : "";

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

  var promise;
  if (navigator.clipboard && window.isSecureContext) {
    promise = navigator.clipboard.writeText(text);
  } else {
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "absolute";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
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
  var fileInput = document.getElementById("kw-import-file");
  if (!fileInput) return;
  var form = fileInput.closest(".import-form");
  var label = fileInput.parentElement.querySelector(".file-name");

  fileInput.addEventListener("change", function () {
    var name =
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
  var sseRetries = 0;
  var maxRetries = 10;

  function reconnectSSE() {
    var container = document.querySelector("[sse-connect]");
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
      var delay = Math.min(5000 * (sseRetries + 1), 30000);
      setTimeout(reconnectSSE, delay);
    }
  });

  document.body.addEventListener("htmx:sseOpen", function () {
    sseRetries = 0;
  });
})();

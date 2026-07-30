(function () {
  const form = document.getElementById("smtp-settings-form");
  if (!form) return;

  const serverEl = document.getElementById("smtp_server");
  const portEl = document.getElementById("smtp_port");
  const securityEl = document.getElementById("smtp_security");
  const saveBtn = document.getElementById("smtp-save-btn");
  const testBtn = document.getElementById("smtp-test-btn");
  const statusEl = document.getElementById("smtp-test-status");

  function fields() {
    const body = new FormData();
    body.append("smtp_server", (serverEl.value || "").trim());
    body.append("smtp_port", (portEl.value || "").trim());
    body.append("smtp_security", securityEl.value);
    return body;
  }

  function showToast(resp, fallbackTone) {
    const msg = resp.headers.get("X-Toast");
    const tone = resp.headers.get("X-Toast-Tone") || fallbackTone || "success";
    if (msg && window.showToast) window.showToast(msg, tone);
  }

  function setStatus(text, tone) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.className = "settings-inline-status" + (tone ? " is-" + tone : "");
  }

  const securityWarning = document.getElementById("smtp-security-warning");
  function syncSecurityWarning() {
    if (!securityEl || !securityWarning) return;
    securityWarning.hidden = securityEl.value !== "none";
  }
  if (securityEl) {
    syncSecurityWarning();
    securityEl.addEventListener("change", syncSecurityWarning);
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", function () {
      saveBtn.disabled = true;
      fetch("/settings/smtp", { method: "POST", body: fields() })
        .then(function (r) {
          showToast(r, "success");
        })
        .catch(function () {
          if (window.showToast) window.showToast("Could not save SMTP settings", "error");
        })
        .finally(function () {
          saveBtn.disabled = false;
        });
    });
  }

  if (testBtn) {
    testBtn.addEventListener("click", function () {
      testBtn.disabled = true;
      const original = testBtn.textContent;
      testBtn.textContent = "Testing…";
      setStatus("Connecting…", "info");
      fetch("/settings/smtp/test", { method: "POST", body: fields() })
        .then(function (r) {
          showToast(r, "info");
          const msg = r.headers.get("X-Toast") || "";
          const tone = r.headers.get("X-Toast-Tone") || "";
          if (tone === "success") setStatus(msg || "Connected", "success");
          else if (tone === "warning") setStatus(msg || "Waiting…", "warning");
          else setStatus(msg || "Failed", "error");
        })
        .catch(function () {
          setStatus("Could not test SMTP connection.", "error");
          if (window.showToast) window.showToast("Could not test SMTP connection", "error");
        })
        .finally(function () {
          testBtn.disabled = false;
          testBtn.textContent = original;
        });
    });
  }
})();

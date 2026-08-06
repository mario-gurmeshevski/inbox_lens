(function () {
  const form = document.getElementById("smtp-settings-form");
  if (!form) return;

  const securityEl = document.getElementById("smtp_security");
  const testBtn = document.getElementById("smtp-test-btn");
  const statusEl = document.getElementById("smtp-test-status");

  function fields() {
    const body = new FormData();
    body.append("smtp_server", (document.getElementById("smtp_server").value || "").trim());
    body.append("smtp_port", (document.getElementById("smtp_port").value || "").trim());
    body.append("smtp_security", securityEl.value);
    return body;
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

  if (testBtn) {
    testBtn.addEventListener("click", function () {
      testBtn.disabled = true;
      const original = testBtn.textContent;
      testBtn.textContent = "Testing…";
      setStatus("Connecting…", "info");
      fetch("/settings/smtp/test", { method: "POST", body: fields() })
        .then(function (r) {
          let msg = r.headers.get("X-Toast") || "";
          let tone = r.headers.get("X-Toast-Tone") || "";
          if (msg && window.showToast) window.showToast(msg, tone);
          if (tone === "success") setStatus(msg || "Connected", "success");
          else if (tone === "warning") setStatus(msg || "Waiting…", "warning");
          else setStatus(msg || "Failed", "error");
        })
        .catch(function () {
          setStatus("Could not test SMTP connection.", "error");
          if (window.showToast) window.showToast("Could not test SMTP connection.", "error");
        })
        .finally(function () {
          testBtn.disabled = false;
          testBtn.textContent = original;
        });
    });
  }
})();

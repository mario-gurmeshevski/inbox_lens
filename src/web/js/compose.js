(function () {
  const COMPOSE_MODAL_ID = "compose-modal";
  const COMPOSE_BODY_ID = "compose-modal-body";

  let lastFocused = null;
  let autosaveTimer = null;
  let autosaveDirty = false;
  let pendingFiles = [];
  let storedAttachments = [];
  let draftPersisted = false;

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
    wireToolbar();
    wireCcToggle();
    wireAttachments();
    placeCursor();
    const form = document.getElementById("compose-form");
    if (form) {
      form.addEventListener("input", scheduleAutosave);
    }
    setTimeout(function () {
      const focusables = getFocusables();
      if (focusables.length) focusables[0].focus();
    }, 0);
  }

  function closeComposeModal() {
    const draftId = currentDraftId();
    const wasPersisted = draftPersisted;
    if (typeof closeModal === "function") closeModal(COMPOSE_MODAL_ID);
    const body = document.getElementById(COMPOSE_BODY_ID);
    if (body) body.innerHTML = "";
    resetAttachmentState();
    if (lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
      lastFocused = null;
    }
    if (!wasPersisted && draftId) {
      try {
        fetch("/drafts/" + encodeURIComponent(draftId) + "/abandon", { method: "POST" });
      } catch (e) {
      }
    }
  }

  function loadCompose(mode, hash) {
    let url;
    if (mode === "new" || !hash) {
      url = "/partials/compose-new";
    } else {
      url = "/partials/compose/" + encodeURIComponent(hash) + "?mode=" + encodeURIComponent(mode);
    }
    fetch(url, { headers: { Accept: "text/html" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("Failed to load compose form");
        return resp.text();
      })
      .then(function (html) {
        openComposeModal(html);
        if (mode === "forward" && hash) {
          loadForwardAttachments(hash);
        }
      })
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Could not open composer", "error");
      });
  }

  function loadForwardAttachments(hash) {
    const draftIdEl = document.getElementById("compose-draft-id");
    const draftId = draftIdEl ? draftIdEl.value : "";
    const qs = draftId ? "?draft_id=" + encodeURIComponent(draftId) : "";
    fetch("/partials/forward-attachments/" + encodeURIComponent(hash) + qs, {
      method: "POST",
      headers: { Accept: "application/json" },
    })
      .then(function (resp) {
        if (!resp.ok) return { attachments: [] };
        return resp.json();
      })
      .then(function (data) {
        if (data.draft_id && draftIdEl && !draftIdEl.value) {
          draftIdEl.value = data.draft_id;
        }
        (data.attachments || []).forEach(function (att) {
          storedAttachments.push(att);
        });
        renderAttachmentChips();
        if (data.notice && typeof window.showToast === "function") {
          window.showToast(data.notice, "warning");
        }
      })
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Couldn't load original attachments", "warning");
      });
  }

  window.composeToolbar = {
    wrap: function (textarea, before, after) {
      after = after || before;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const value = textarea.value;
      const selected = value.slice(start, end);
      textarea.value = value.slice(0, start) + before + selected + after + value.slice(end);
      const cursor = start + before.length;
      textarea.selectionStart = cursor;
      textarea.selectionEnd = cursor + selected.length;
      textarea.focus();
    },
    insertLinePrefix: function (textarea, prefix) {
      const start = textarea.selectionStart;
      const value = textarea.value;
      const lineStart = value.lastIndexOf("\n", start - 1) + 1;
      textarea.value = value.slice(0, lineStart) + prefix + value.slice(lineStart);
      textarea.selectionStart = textarea.selectionEnd = start + prefix.length;
      textarea.focus();
    },
    insertLink: function (textarea) {
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const value = textarea.value;
      const selected = value.slice(start, end) || "text";
      const replacement = "[" + selected + "](https://)";
      textarea.value = value.slice(0, start) + replacement + value.slice(end);
      textarea.selectionStart = start + selected.length + 3;
      textarea.selectionEnd = start + selected.length + 11;
      textarea.focus();
    },
  };

  function wireToolbar() {
    const form = document.getElementById("compose-form");
    if (!form) return;
    const textarea = form.elements["body"];
    if (!textarea) return;
    form.querySelectorAll("button[data-md]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const kind = btn.getAttribute("data-md");
        if (kind === "bold") window.composeToolbar.wrap(textarea, "**");
        else if (kind === "italic") window.composeToolbar.wrap(textarea, "*");
        else if (kind === "code") window.composeToolbar.wrap(textarea, "`");
        else if (kind === "list") window.composeToolbar.insertLinePrefix(textarea, "- ");
        else if (kind === "quote") window.composeToolbar.insertLinePrefix(textarea, "> ");
        else if (kind === "link") window.composeToolbar.insertLink(textarea);
      });
    });
  }

  function wireCcToggle() {
    const toggle = document.getElementById("compose-cc-toggle");
    const row = document.getElementById("compose-cc-row");
    if (!toggle || !row) return;
    const cc = document.getElementById("compose-cc");
    const bcc = document.getElementById("compose-bcc");
    if ((cc && cc.value) || (bcc && bcc.value)) {
      row.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    }
    if (toggle.dataset.wired === "1") return;
    toggle.dataset.wired = "1";
    toggle.addEventListener("click", function () {
      const willShow = row.hasAttribute("hidden");
      row.hidden = !willShow;
      toggle.setAttribute("aria-expanded", String(willShow));
      if (willShow && cc) cc.focus();
    });
  }

  function placeCursor() {
    const form = document.getElementById("compose-form");
    if (!form) return;
    const textarea = form.elements["body"];
    if (!textarea) return;
    textarea.selectionStart = textarea.selectionEnd = 0;
    textarea.focus();
  }

  function formatSize(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function resetAttachmentState() {
    pendingFiles = [];
    storedAttachments = [];
    draftPersisted = false;
  }

  function renderAttachmentChips() {
    const list = document.getElementById("compose-attachment-list");
    if (!list) return;
    list.innerHTML = "";
    storedAttachments.forEach(function (att) {
      list.appendChild(buildChip(att.filename, att.size, true, function () {
        removeStored(att.id);
      }));
    });
    pendingFiles.forEach(function (file, idx) {
      list.appendChild(buildChip(file.name, file.size, false, function () {
        removePending(idx);
      }));
    });
  }

  function buildChip(name, size, stored, onRemove) {
    const li = document.createElement("li");
    li.className = "attachment-chip" + (stored ? " is-stored" : "");
    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", stored ? "check" : "file");
    icon.className = "icon-sm";
    const nameEl = document.createElement("span");
    nameEl.className = "attachment-chip-name";
    nameEl.textContent = name;
    const sizeEl = document.createElement("span");
    sizeEl.className = "attachment-chip-size";
    sizeEl.textContent = formatSize(size);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "attachment-chip-remove";
    btn.setAttribute("aria-label", "Remove " + name);
    btn.innerHTML = '<i data-lucide="x" class="icon-sm"></i>';
    btn.addEventListener("click", function () {
      onRemove();
    });
    li.appendChild(icon);
    li.appendChild(nameEl);
    li.appendChild(sizeEl);
    li.appendChild(btn);
    return li;
  }

  function wireAttachments() {
    resetAttachmentState();
    const list = document.getElementById("compose-attachment-list");
    if (list) {
      Array.prototype.forEach.call(list.children, function (chip) {
        const id = chip.getAttribute("data-attachment-id");
        if (!id) return;
        const name = chip.getAttribute("data-filename") || "attachment";
        const size = parseInt(chip.getAttribute("data-size") || "0", 10) || null;
        storedAttachments.push({ id: id, filename: name, size: size });
      });
    }
    const input = document.getElementById("compose-attachments-input");
    if (input && input.dataset.wired !== "1") {
      input.dataset.wired = "1";
      input.addEventListener("change", function () {
        if (!input.files || !input.files.length) return;
        for (let i = 0; i < input.files.length; i++) {
          pendingFiles.push(input.files[i]);
        }
        input.value = "";
        renderAttachmentChips();
        scheduleAutosave();
      });
    }
    renderAttachmentChips();
  }

  function removePending(index) {
    pendingFiles.splice(index, 1);
    renderAttachmentChips();
    scheduleAutosave();
  }

  function removeStored(attachmentId) {
    const draftId = currentDraftId();
    if (!draftId || !attachmentId) {
      storedAttachments = storedAttachments.filter(function (a) { return a.id !== attachmentId; });
      renderAttachmentChips();
      return;
    }
    fetch("/drafts/" + encodeURIComponent(draftId) + "/attachments/" + encodeURIComponent(attachmentId), {
      method: "DELETE",
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("remove failed");
        storedAttachments = storedAttachments.filter(function (a) { return a.id !== attachmentId; });
        renderAttachmentChips();
      })
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Couldn't remove attachment", "error");
      });
  }

  function currentDraftId() {
    const el = document.getElementById("compose-draft-id");
    return el ? el.value : "";
  }

  function gatherDraftParams() {
    const form = document.getElementById("compose-form");
    if (!form) return null;
    const draftIdEl = document.getElementById("compose-draft-id");
    return {
      draft_id: draftIdEl ? draftIdEl.value : "",
      to: (form.elements["to"] || {}).value || "",
      cc: (form.elements["cc"] || {}).value || "",
      bcc: (form.elements["bcc"] || {}).value || "",
      subject: (form.elements["subject"] || {}).value || "",
      body: (form.elements["body"] || {}).value || "",
      mode: form.elements["mode"] ? form.elements["mode"].value : "new",
      original_message_id: (form.elements["original_message_id"] || {}).value || "",
      thread_id: (form.elements["thread_id"] || {}).value || "",
    };
  }

  function ensureDraftId() {
    const el = document.getElementById("compose-draft-id");
    if (el && !el.value) {
      el.value =
        (crypto.randomUUID && crypto.randomUUID()) ||
        "d-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    }
    return el ? el.value : "";
  }

  function scheduleAutosave() {
    autosaveDirty = true;
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(doAutosave, 2000);
  }

  function doAutosave() {
    autosaveTimer = null;
    if (!autosaveDirty) return;
    autosaveDirty = false;
    const params = gatherDraftParams();
    if (!params) return;
    const hasText = params.to || params.cc || params.bcc || params.subject || params.body;
    const hasPending = pendingFiles.length > 0;
    if (!hasText && !hasPending) return;
    params.draft_id = ensureDraftId();
    const draftId = params.draft_id;
    const textPromise = window.postForm("/drafts/save", params);
    textPromise.then(function (resp) {
      if (resp.ok) draftPersisted = true;
    });
    textPromise.catch(function () {
      if (typeof window.showToast === "function") window.showToast("Couldn't save draft", "warning");
    });
    if (hasPending) {
      uploadPendingToDraft(draftId);
    }
  }

  function uploadPendingToDraft(draftId) {
    const files = pendingFiles;
    pendingFiles = [];
    const fd = new FormData();
    files.forEach(function (file) { fd.append("attachments", file); });
    fetch("/drafts/" + encodeURIComponent(draftId) + "/attachments", { method: "POST", body: fd })
      .then(function (resp) {
        if (!resp.ok) throw new Error("upload failed");
        return resp.json();
      })
      .then(function (data) {
        (data.attachments || []).forEach(function (att) {
          storedAttachments.push(att);
        });
        renderAttachmentChips();
      })
      .catch(function () {
        pendingFiles = files.concat(pendingFiles);
        renderAttachmentChips();
        if (typeof window.showToast === "function") window.showToast("Couldn't save attachment to draft", "warning");
      });
  }

  function loadDraft(draftId) {
    fetch("/partials/compose-draft/" + encodeURIComponent(draftId), {
      headers: { Accept: "text/html" },
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("Failed to load draft");
        return resp.text();
      })
      .then(function (html) {
        openComposeModal(html);
        draftPersisted = true;
        const form = document.getElementById("compose-form");
        if (form) {
          const cc = form.elements["cc"];
          const bcc = form.elements["bcc"];
          if ((cc && cc.value) || (bcc && bcc.value)) wireCcToggle();
        }
        placeCursor();
      })
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Could not load draft", "error");
      });
  }

  function loadOutbox(outboxId) {
    fetch("/partials/compose-outbox/" + encodeURIComponent(outboxId), {
      method: "POST",
      headers: { Accept: "text/html" },
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("Failed to load outbox message");
        return resp.text();
      })
      .then(function (html) {
        openComposeModal(html);
        draftPersisted = true;
        const form = document.getElementById("compose-form");
        if (form) {
          const cc = form.elements["cc"];
          const bcc = form.elements["bcc"];
          if ((cc && cc.value) || (bcc && bcc.value)) wireCcToggle();
        }
        placeCursor();
      })
      .catch(function () {
        if (typeof window.showToast === "function") window.showToast("Could not load outbox message", "error");
      });
  }

  function submitCompose(form) {
    const sendBtn = document.getElementById("compose-send-btn");
    if (sendBtn) sendBtn.disabled = true;

    const draftIdEl = document.getElementById("compose-draft-id");
    const fields = {
      to: (form.elements["to"] || {}).value || "",
      cc: (form.elements["cc"] || {}).value || "",
      bcc: (form.elements["bcc"] || {}).value || "",
      subject: (form.elements["subject"] || {}).value || "",
      body: (form.elements["body"] || {}).value || "",
      mode: form.elements["mode"] ? form.elements["mode"].value : (form.getAttribute("data-compose-mode") || "new"),
      original_message_id: (form.elements["original_message_id"] || {}).value || "",
      thread_id: (form.elements["thread_id"] || {}).value || "",
      draft_id: draftIdEl ? draftIdEl.value : "",
    };

    if (!fields.to || !fields.subject || !fields.body) {
      if (typeof window.showToast === "function") window.showToast("To, subject, and body are required", "error");
      if (sendBtn) sendBtn.disabled = false;
      return;
    }

    const fd = new FormData();
    Object.keys(fields).forEach(function (k) { fd.append(k, fields[k]); });
    pendingFiles.forEach(function (file) { fd.append("attachments", file); });

    window
      .postFormData("/emails/send", fd)
      .then(function (resp) {
        const tone = resp.headers.get("X-Toast-Tone") || (resp.ok ? "success" : "error");
        if (resp.ok && tone === "success") {
          closeComposeModal();
        }
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

  document.addEventListener("click", function (e) {
    const resumeBtn = e.target.closest("[data-draft-resume]");
    if (resumeBtn) {
      e.preventDefault();
      loadDraft(resumeBtn.getAttribute("data-draft-resume"));
    }
  });

  document.addEventListener("click", function (e) {
    const editBtn = e.target.closest("[data-outbox-edit]");
    if (editBtn) {
      e.preventDefault();
      loadOutbox(editBtn.getAttribute("data-outbox-edit"));
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
    window.loadCompose = loadCompose;
    window.loadDraft = loadDraft;
  }
})();

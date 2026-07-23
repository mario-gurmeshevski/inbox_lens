(function () {
  "use strict";

  const selected = new Set();
  let moveTarget = null; // "bulk" or a single email_hash

  function tableContainer() {
    return document.getElementById("email-table");
  }

  function toolbar() {
    return document.querySelector("[data-bulk-toolbar]");
  }

  function rowCheckboxes() {
    const container = tableContainer();
    if (!container) return [];
    return Array.prototype.slice.call(
      container.querySelectorAll('input[data-bulk-row]')
    );
  }

  function selectAllCheckbox() {
    const container = tableContainer();
    return container
      ? container.querySelector('input[data-bulk-select-all]')
      : null;
  }

  function syncCheckboxes() {
    rowCheckboxes().forEach(function (cb) {
      cb.checked = selected.has(cb.value);
    });
    const all = selectAllCheckbox();
    if (all) {
      const visible = rowCheckboxes();
      all.checked =
        visible.length > 0 && visible.every(function (cb) { return cb.checked; });
    }
  }

  function updateToolbar() {
    const bar = toolbar();
    if (!bar) return;
    const count = selected.size;
    if (count > 0) {
      bar.removeAttribute("hidden");
    } else {
      bar.setAttribute("hidden", "");
    }
    const countEl = bar.querySelector("[data-bulk-count]");
    if (countEl) {
      countEl.textContent =
        count + (count === 1 ? " email selected" : " emails selected");
    }
  }

  function refresh() {
    syncCheckboxes();
    updateToolbar();
  }

  function toggleRow(value, checked) {
    if (!value) return;
    if (checked) {
      selected.add(value);
    } else {
      selected.delete(value);
    }
    updateToolbar();
  }

  function toggleAll(checked) {
    rowCheckboxes().forEach(function (cb) {
      cb.checked = checked;
      if (checked) {
        selected.add(cb.value);
      } else {
        selected.delete(cb.value);
      }
    });
    updateToolbar();
  }

  function clearSelection() {
    selected.clear();
    refresh();
  }

  function postBulk(action, extra) {
    const params = { action: action, hashes: Array.from(selected) };
    if (extra) {
      Object.keys(extra).forEach(function (k) { params[k] = extra[k]; });
    }
    return window.postForm("/emails/bulk", params).then(function (resp) {
      if (resp.ok) clearSelection();
    });
  }

  function confirmAndPost(btn) {
    const action = btn.getAttribute("data-bulk-action");
    if (action === "move") {
      openFolderModal("bulk");
      return;
    }
    const question = btn.getAttribute("data-confirm");
    if (!question) {
      postBulk(action);
      return;
    }
    if (!window.confirmDialog) {
      if (window.confirm(question)) {
        postBulk(action);
      }
      return;
    }
    window
      .confirmDialog({
        title: btn.getAttribute("data-confirm-title") || "Are you sure?",
        message: question,
        tone: btn.getAttribute("data-confirm-tone") || "warning",
        confirmLabel: btn.getAttribute("data-confirm-label") || "Confirm",
      })
      .then(function (ok) {
        if (ok) postBulk(action);
      });
  }

  function openFolderModal(target) {
    moveTarget = target;
    const body = document.getElementById("folder-modal-body");
    if (typeof openModal !== "function" || !body) return;
    body.innerHTML = '<p class="folder-loading">Loading folders…</p>';
    openModal("folder-modal");

    fetch("/folders")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderFolderList(data.folders || []);
      })
      .catch(function () {
        body.innerHTML =
          '<p class="folder-loading">Failed to load folders.</p>';
      });
  }

  function closeFolderModal() {
    if (typeof closeModal === "function") closeModal("folder-modal");
    moveTarget = null;
  }

  function renderFolderList(folders) {
    const body = document.getElementById("folder-modal-body");
    if (!body) return;
    if (!folders.length) {
      body.innerHTML = '<p class="folder-loading">No folders found.</p>';
      return;
    }
    body.innerHTML = "";
    folders.forEach(function (name) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm folder-option";
      btn.textContent = name;
      btn.addEventListener("click", function () {
        chooseFolder(name);
      });
      body.appendChild(btn);
    });
    if (window.lucide) lucide.createIcons();
  }

  function chooseFolder(folder) {
    const target = moveTarget;
    closeFolderModal();
    if (target === "bulk") {
      postBulk("move", { folder: folder });
    } else if (target) {
      window.postForm("/emails/" + encodeURIComponent(target) + "/move", {
        folder: folder,
      }).then(function (resp) {
        if (resp.ok) window.location.href = "/emails";
      });
    }
  }

  function init() {
    const container = tableContainer();
    if (!container) return;

    container.addEventListener("change", function (e) {
      const el = e.target;
      if (el.hasAttribute("data-bulk-row")) {
        toggleRow(el.value, el.checked);
      } else if (el.hasAttribute("data-bulk-select-all")) {
        toggleAll(el.checked);
      }
    });

    const bar = toolbar();
    if (bar) {
      bar.addEventListener("click", function (e) {
        const btn = e.target.closest("[data-bulk-action]");
        if (!btn) return;
        if (selected.size === 0) {
          if (window.showToast) window.showToast("No emails selected", "warning");
          return;
        }
        confirmAndPost(btn);
      });
    }

    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-move-email]");
      if (!btn) return;
      openFolderModal(btn.getAttribute("data-move-email"));
    });
    container.addEventListener("click", function (e) {
      const btn = e.target.closest(".star-toggle");
      if (!btn) return;
    });

    document.body.addEventListener("htmx:afterSwap", function (e) {
      const target = e.detail && e.detail.target;
      if (target && target.id === "email-table") {
        refresh();
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      const modal = document.getElementById("folder-modal");
      if (modal && !modal.hasAttribute("hidden")) {
        closeFolderModal();
      }
    });

    refresh();
  }

  window.openFolderModal = openFolderModal;
  window.closeFolderModal = closeFolderModal;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

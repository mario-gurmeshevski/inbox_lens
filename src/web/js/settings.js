(function () {
  var combobox = document.getElementById("timezone-combobox");
  if (!combobox) return;

  var btn = document.getElementById("timezone-btn");
  var panel = document.getElementById("timezone-panel");
  var search = document.getElementById("timezone-search");
  var optionsEl = document.getElementById("timezone-options");
  var hidden = document.getElementById("timezone-hidden");
  var label = combobox.querySelector(".combobox-label");
  var groups = Array.prototype.slice.call(optionsEl.querySelectorAll(".group"));
  var options = Array.prototype.slice.call(optionsEl.querySelectorAll(".option"));
  var activeIndex = -1;

  var BOTTOM_GAP = 16;
  var MAX_CAP = 320;

  function constrainPanelHeight() {
    var top = panel.getBoundingClientRect().top;
    var available = window.innerHeight - top - BOTTOM_GAP;
    var effective = Math.min(MAX_CAP, available);
    panel.style.setProperty("--panel-max-height", effective + "px");
  }

  function isOpen() {
    return panel.classList.contains("is-open");
  }

  function open() {
    panel.classList.add("is-open");
    constrainPanelHeight();
    btn.setAttribute("aria-expanded", "true");
    setTimeout(function () {
      search.focus();
      var sel = optionsEl.querySelector('.option[aria-selected="true"]');
      if (sel) sel.scrollIntoView({ block: "center" });
    }, 0);
  }

  function close() {
    panel.classList.remove("is-open");
    btn.setAttribute("aria-expanded", "false");
    if (search.value) {
      search.value = "";
      filter("");
    }
    clearActive();
  }

  btn.addEventListener("click", function () {
    if (isOpen()) close();
    else open();
  });

  document.addEventListener("click", function (e) {
    if (isOpen() && !combobox.contains(e.target)) close();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && isOpen()) {
      close();
      btn.focus();
    }
  });

  function handleViewportChange() {
    if (isOpen()) constrainPanelHeight();
  }
  window.addEventListener("resize", handleViewportChange);
  window.addEventListener("scroll", handleViewportChange, true);

  function visibleOptions() {
    return options.filter(function (o) {
      return !o.hasAttribute("hidden");
    });
  }

  function clearActive() {
    options.forEach(function (o) {
      o.classList.remove("is-active");
    });
    activeIndex = -1;
  }

  function setActive(idx) {
    var visible = visibleOptions();
    if (!visible.length) return;
    clearActive();
    activeIndex = (idx + visible.length) % visible.length;
    var el = visible[activeIndex];
    el.classList.add("is-active");
    el.scrollIntoView({ block: "nearest" });
  }

  function filter(q) {
    q = q.trim().toLowerCase();
    var count = 0;
    options.forEach(function (opt) {
      var hay = opt.getAttribute("data-label") || "";
      var match = !q || hay.indexOf(q) !== -1;
      if (match) {
        opt.removeAttribute("hidden");
        count++;
      } else {
        opt.setAttribute("hidden", "");
      }
    });
    groups.forEach(function (g) {
      var any = g.querySelectorAll('.option:not([hidden])').length > 0;
      if (any) g.removeAttribute("hidden");
      else g.setAttribute("hidden", "");
    });
    var existing = optionsEl.querySelector(".empty");
    if (count === 0) {
      var msg = 'No timezones match "' + q + '"';
      if (existing) {
        existing.textContent = msg;
      } else {
        var empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = msg;
        optionsEl.appendChild(empty);
      }
    } else if (existing) {
      existing.remove();
    }
    clearActive();
  }

  search.addEventListener("input", function () {
    filter(search.value);
  });

  search.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(activeIndex + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(activeIndex - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      var visible = visibleOptions();
      if (activeIndex >= 0 && activeIndex < visible.length) {
        selectOption(visible[activeIndex]);
      } else if (visible.length) {
        selectOption(visible[0]);
      }
    }
  });

  function selectOption(opt) {
    if (!opt) return;
    hidden.value = opt.getAttribute("data-value");
    label.textContent = opt.textContent;
    options.forEach(function (o) {
      if (o === opt) o.setAttribute("aria-selected", "true");
      else o.removeAttribute("aria-selected");
    });
    close();
  }

  optionsEl.addEventListener("click", function (e) {
    var opt = e.target.closest(".option");
    if (opt) selectOption(opt);
  });
})();

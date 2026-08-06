(function () {
  const tabs = document.querySelector(".settings-tabs");
  if (!tabs) return;

  const navLinks = document.querySelector(".nav-links");
  if (!navLinks) return;

  const clone = tabs.cloneNode(true);
  clone.classList.remove("settings-tabs");
  clone.classList.add("nav-tabs-clone");
  clone.removeAttribute("role");
  clone.setAttribute("aria-hidden", "true");
  const spacer = navLinks.querySelector("a.nav-spacer");
  navLinks.insertBefore(clone, spacer);

  const pageTabs = [].slice.call(tabs.querySelectorAll(".settings-tab"));
  const cloneTabs = [].slice.call(clone.querySelectorAll(".settings-tab"));
  function syncActive() {
    const active = pageTabs.find((t) => t.classList.contains("is-active"));
    const name = active ? active.getAttribute("data-tab") : null;
    cloneTabs.forEach((t) =>
      t.classList.toggle("is-active", t.getAttribute("data-tab") === name)
    );
  }
  pageTabs.forEach((t) => t.addEventListener("click", syncActive));
  cloneTabs.forEach((t) =>
    t.addEventListener("click", function () {
      const name = t.getAttribute("data-tab");
      const target = pageTabs.find((p) => p.getAttribute("data-tab") === name);
      if (target) target.click();
    })
  );

  const mq = window.matchMedia("(max-width: 768px)");
  function desktop() {
    return !mq.matches;
  }

  const sentinel = document.createElement("div");
  sentinel.className = "settings-nav-sentinel";
  sentinel.setAttribute("aria-hidden", "true");
  tabs.parentNode.insertBefore(sentinel, tabs);

  let pinned = false;
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries[0].isIntersecting;
        if (!visible && !pinned && desktop()) {
          pinned = true;
          syncActive();
          document.body.classList.add("nav-tabs");
        } else if (visible && pinned) {
          pinned = false;
          document.body.classList.remove("nav-tabs");
        }
      },
      { rootMargin: "0px 0px -40px 0px", threshold: 0 }
    );
    observer.observe(sentinel);

    mq.addEventListener("change", (e) => {
      if (e.matches) {
        pinned = false;
        document.body.classList.remove("nav-tabs");
      }
    });
  }
})();

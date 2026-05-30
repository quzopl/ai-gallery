/* ===========================================================================
   theme.js — animated theme + view switchers
   Themes: data-theme = "oled" | "light" | "warm"  on <html>.
   To use in your real app: keep the two .seg controls from index.html
   (or build your own) and include this file. Persists to localStorage.
   =========================================================================== */
(function () {
  "use strict";
  const root = document.documentElement;

  /* sweep overlay for a delightful theme transition */
  const sweep = document.createElement("div");
  sweep.id = "theme-sweep";
  document.body.appendChild(sweep);
  function playSweep() {
    sweep.classList.remove("run");
    void sweep.offsetWidth; // reflow to restart animation
    sweep.classList.add("run");
  }

  /* generic animated segmented control: moves .seg-thumb under the active btn */
  function wireSeg(el, { storeKey, apply, def }) {
    if (!el) return;
    const thumb = el.querySelector(".seg-thumb");
    const btns = [...el.querySelectorAll("button")];

    function moveThumb(btn) {
      if (!thumb || !btn) return;
      thumb.style.width = btn.offsetWidth + "px";
      thumb.style.transform = `translateX(${btn.offsetLeft - el.querySelector("button").offsetLeft}px)`;
    }
    function setActive(val, animate) {
      const btn = btns.find(b => b.dataset[el === themeEl ? "themeVal" : "viewVal"] === val) || btns[0];
      btns.forEach(b => b.classList.toggle("active", b === btn));
      moveThumb(btn);
      apply(val);
      if (storeKey) localStorage.setItem(storeKey, val);
    }
    btns.forEach(b => b.addEventListener("click", () => {
      const val = b.dataset[el === themeEl ? "themeVal" : "viewVal"];
      if (el === themeEl) playSweep();
      setActive(val, true);
    }));
    const saved = (storeKey && localStorage.getItem(storeKey)) || def;
    // set without sweep on load
    requestAnimationFrame(() => setActive(saved, false));
    window.addEventListener("resize", () => {
      const active = btns.find(b => b.classList.contains("active"));
      moveThumb(active);
    });
    return setActive;
  }

  const themeEl = document.getElementById("theme-switch");
  const viewEl = document.getElementById("view-switch");

  wireSeg(themeEl, {
    storeKey: "galleryTheme",
    def: "oled",
    apply: (val) => { root.dataset.theme = val; },
  });

  wireSeg(viewEl, {
    storeKey: "galleryView",
    def: "grid",
    apply: (val) => {
      const g = document.getElementById("gallery");
      if (g) g.classList.toggle("masonry", val === "masonry");
    },
  });
})();

/* ===========================================================================
   mock.js — PROTOTYPE ONLY
   Stands in for the real backend so the gallery is fully interactive in a
   static preview. Installs a fetch() shim for /api/*, a WebSocket stub, a
   safe clipboard fallback, and procedurally generated thumbnails.
   DELETE this file (and its <script> tag) when wiring to the live API.
   =========================================================================== */
(function () {
  "use strict";

  /* ---------- seeded RNG ------------------------------------------------- */
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------- procedural "AI art" thumbnails ----------------------------- */
  const PALETTES = [
    ["#1b2a4a", "#5b8def", "#b06bff"],
    ["#2a1133", "#ff5d8f", "#ffb347"],
    ["#06262b", "#11999e", "#a8e6cf"],
    ["#311b0b", "#ff7a18", "#ffd86b"],
    ["#14233b", "#0f4c81", "#7fd1ff"],
    ["#1a1030", "#7b2ff7", "#f107a3"],
    ["#0d1f1a", "#2dd4a8", "#d9f99d"],
    ["#2b0a16", "#e0245e", "#ff9a8b"],
    ["#101820", "#3a6ea5", "#c9d6df"],
    ["#241b00", "#caa83a", "#fff3c4"],
    ["#0c1d2e", "#1f6f8b", "#99e1d9"],
    ["#1e0f2e", "#9d4edd", "#ff99c8"],
  ];
  const ASPECTS = [[1, 1], [3, 4], [4, 3], [2, 3], [16, 9], [4, 5]];

  const cache = new Map();
  function thumbFor(id) {
    if (cache.has(id)) return cache.get(id);
    const rnd = mulberry32(id * 2654435761 >>> 0);
    const [aw, ah] = ASPECTS[Math.floor(rnd() * ASPECTS.length)];
    const base = 480;
    const w = aw >= ah ? base : Math.round(base * aw / ah);
    const h = ah >= aw ? base : Math.round(base * ah / aw);
    const pal = PALETTES[Math.floor(rnd() * PALETTES.length)];

    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const x = c.getContext("2d");

    // base gradient
    const ang = rnd() * Math.PI * 2;
    const g = x.createLinearGradient(
      w / 2 - Math.cos(ang) * w, h / 2 - Math.sin(ang) * h,
      w / 2 + Math.cos(ang) * w, h / 2 + Math.sin(ang) * h
    );
    g.addColorStop(0, pal[0]);
    g.addColorStop(0.55, pal[1]);
    g.addColorStop(1, pal[2]);
    x.fillStyle = g; x.fillRect(0, 0, w, h);

    // glowing blobs
    x.globalCompositeOperation = "screen";
    const blobs = 4 + Math.floor(rnd() * 4);
    for (let i = 0; i < blobs; i++) {
      const cx = rnd() * w, cy = rnd() * h, r = (0.15 + rnd() * 0.4) * Math.max(w, h);
      const col = pal[Math.floor(rnd() * pal.length)];
      const rg = x.createRadialGradient(cx, cy, 0, cx, cy, r);
      rg.addColorStop(0, col + "cc");
      rg.addColorStop(1, col + "00");
      x.fillStyle = rg;
      x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fill();
    }

    // a few crisp geometric accents
    x.globalCompositeOperation = "overlay";
    for (let i = 0; i < 3; i++) {
      x.strokeStyle = "rgba(255,255,255," + (0.05 + rnd() * 0.12).toFixed(3) + ")";
      x.lineWidth = 1 + rnd() * 3;
      x.beginPath();
      x.arc(rnd() * w, rnd() * h, (0.1 + rnd() * 0.35) * w, 0, Math.PI * 2);
      x.stroke();
    }

    // grain
    x.globalCompositeOperation = "source-over";
    const grain = 900;
    for (let i = 0; i < grain; i++) {
      x.fillStyle = "rgba(255,255,255," + (rnd() * 0.04).toFixed(3) + ")";
      x.fillRect(rnd() * w, rnd() * h, 1, 1);
    }
    // vignette
    const vg = x.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.max(w, h) * 0.75);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,0.35)");
    x.fillStyle = vg; x.fillRect(0, 0, w, h);

    const url = c.toDataURL("image/jpeg", 0.82);
    cache.set(id, url);
    return url;
  }
  // image resolver used by the (lightly patched) app.js
  window.__mockImg = thumbFor;

  /* ---------- sample data ------------------------------------------------ */
  const MODELS = [
    "juggernautXL_v9.safetensors", "sdxl_base_1.0.safetensors", "flux1-dev.safetensors",
    "dreamshaper_8.ckpt", "realvisxl_v4.safetensors", "ponyDiffusionV6.safetensors",
  ];
  const LORAS = [
    "add_detail.safetensors", "filmgrain_v2.safetensors", "cyberpunk_edge.safetensors",
    "watercolor_style.safetensors", "epi_noiseoffset2.safetensors", "softlight.safetensors",
  ];
  const TAGS = ["portrait", "landscape", "cyberpunk", "watercolor", "wallpaper",
    "character", "concept", "moody", "neon", "nature", "retro", "pick"];
  const SAMPLERS = ["DPM++ 2M Karras", "Euler a", "DPM++ SDE Karras", "UniPC", "DDIM"];

  const SUBJECTS = [
    "a lone astronaut on a crystalline shore", "neon-lit alley in a rain-soaked metropolis",
    "ancient forest temple wrapped in mist", "portrait of a cyber-shaman, intricate jewelry",
    "floating islands above a sea of clouds", "retro-futuristic diner at golden hour",
    "bioluminescent jellyfish drifting through a nebula", "a fox spirit in an autumn shrine",
    "brutalist cathedral overgrown with vines", "desert nomad beneath twin moons",
    "underwater city of glass and coral", "a clockwork hummingbird, macro shot",
    "samurai silhouette against a blood-red sky", "cozy bookshop on a snowy evening",
    "abstract liquid metal sculpture", "lighthouse on a stormy cliff at dawn",
    "a greenhouse on Mars, lush and humid", "vaporwave shrine with pink columns",
  ];
  const STYLES = [
    "cinematic lighting, 35mm film, shallow depth of field",
    "highly detailed, octane render, volumetric fog",
    "watercolor illustration, soft washes, paper texture",
    "studio portrait, rim light, ultra sharp, 85mm",
    "matte painting, epic scale, golden hour",
    "synthwave palette, chromatic aberration, glow",
  ];
  const LIBS = [
    { id: 1, name: "ComfyUI Output", path: "/home/user/ComfyUI/output" },
    { id: 2, name: "SD WebUI", path: "/home/user/stable-diffusion-webui/outputs/txt2img" },
    { id: 3, name: "Curated", path: "/mnt/art/curated" },
  ];

  function pick(rnd, arr) { return arr[Math.floor(rnd() * arr.length)]; }

  let IMAGES = [];
  (function build() {
    const N = 44;
    for (let i = 1; i <= N; i++) {
      const rnd = mulberry32(i * 99991);
      const lib = LIBS[Math.floor(rnd() * LIBS.length)];
      const [aw, ah] = ASPECTS[Math.floor(mulberry32(i * 2654435761 >>> 0)() * ASPECTS.length)];
      const subject = SUBJECTS[(i - 1) % SUBJECTS.length];
      const style = pick(rnd, STYLES);
      const tags = [];
      const tagN = Math.floor(rnd() * 3);
      while (tags.length < tagN) { const t = pick(rnd, TAGS); if (!tags.includes(t)) tags.push(t); }
      const loras = [];
      if (rnd() > 0.45) loras.push({ name: pick(rnd, LORAS), strength: +(0.4 + rnd() * 0.6).toFixed(2) });
      if (rnd() > 0.8) loras.push({ name: pick(rnd, LORAS), strength: +(0.3 + rnd() * 0.5).toFixed(2) });
      const month = ["2024-11", "2024-12", "2025-01", "2025-02"][Math.floor(rnd() * 4)];
      IMAGES.push({
        id: i,
        library_id: lib.id,
        rel_path: `${month}/render_${String(10000 + i)}_.png`,
        width: aw >= ah ? 1024 : Math.round(1024 * aw / ah),
        height: ah >= aw ? 1024 : Math.round(1024 * ah / aw),
        source_kind: lib.id === 2 ? "A1111" : (lib.id === 1 ? "ComfyUI" : "import"),
        model_name: pick(rnd, MODELS),
        sampler: pick(rnd, SAMPLERS),
        steps: [20, 24, 28, 30, 35, 40][Math.floor(rnd() * 6)],
        cfg: +(3 + rnd() * 6).toFixed(1),
        seed: Math.floor(rnd() * 4294967295),
        loras,
        prompt: `${subject}, ${style}, masterpiece, best quality`,
        negative: "lowres, blurry, watermark, text, deformed, extra fingers",
        is_favorite: rnd() > 0.78 ? 1 : 0,
        tags,
      });
    }
  })();

  function libCount(id) { return IMAGES.filter(x => x.library_id === id).length; }
  function countBy(field) {
    const m = new Map();
    for (const im of IMAGES) {
      const v = im[field];
      if (!v) continue;
      m.set(v, (m.get(v) || 0) + 1);
    }
    return [...m.entries()].map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count);
  }
  function tagCounts() {
    const m = new Map();
    for (const im of IMAGES) for (const t of im.tags) m.set(t, (m.get(t) || 0) + 1);
    return [...m.entries()].map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }

  function filterImages(p) {
    let r = IMAGES.slice();
    const lib = p.get("library_id");
    if (lib) r = r.filter(x => String(x.library_id) === lib);
    const model = p.get("model"); if (model) r = r.filter(x => x.model_name === model);
    const lora = p.get("lora"); if (lora) r = r.filter(x => x.loras.some(L => L.name === lora));
    if (p.get("favorite") === "true") r = r.filter(x => x.is_favorite);
    const q = (p.get("q") || "").toLowerCase();
    if (q) r = r.filter(x =>
      x.prompt.toLowerCase().includes(q) ||
      x.rel_path.toLowerCase().includes(q) ||
      x.tags.some(t => t.includes(q)));
    const tags = p.getAll("tag");
    if (tags.length) r = r.filter(x => tags.every(t => x.tags.includes(t)));
    return r;
  }

  /* ---------- fake filesystem for the folder picker ---------------------- */
  function browse(path) {
    const tree = {
      "/home/user": ["ComfyUI", "stable-diffusion-webui", "Pictures", "Downloads"],
      "/home/user/ComfyUI": ["output", "models", "input"],
      "/home/user/Pictures": ["AI", "Screenshots"],
      "/mnt/art": ["curated", "raw", "archive"],
    };
    const p = path || "/home/user";
    const dirs = (tree[p] || []).map(name => ({ name, readable: name !== "archive" }));
    const parent = p === "/" ? null : p.split("/").slice(0, -1).join("/") || "/";
    return { path: p, parent, dirs };
  }

  /* ---------- response helper ------------------------------------------- */
  function ok(data) {
    return Promise.resolve({
      ok: true, status: 200,
      json: async () => data,
      text: async () => JSON.stringify(data),
    });
  }
  function notFound() {
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}), text: async () => "not found" });
  }

  /* ---------- the fetch shim -------------------------------------------- */
  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init = {}) {
    const url = typeof input === "string" ? input : input.url;
    if (!url || !url.includes("/api/")) return realFetch(input, init);

    const u = new URL(url, location.origin);
    const path = u.pathname;
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : {};

    // libraries
    if (path === "/api/libraries" && method === "GET")
      return ok(LIBS.map(L => ({ ...L, image_count: libCount(L.id) })));
    if (path === "/api/libraries" && method === "POST") {
      const id = LIBS.length + 1;
      LIBS.push({ id, name: body.name || body.path.split("/").pop(), path: body.path });
      return ok({ id });
    }
    let m;
    if ((m = path.match(/^\/api\/libraries\/(\d+)\/rescan$/)) && method === "POST") return ok({ ok: true });
    if ((m = path.match(/^\/api\/libraries\/(\d+)$/)) && method === "DELETE") {
      const id = Number(m[1]);
      const idx = LIBS.findIndex(L => L.id === id);
      if (idx >= 0) LIBS.splice(idx, 1);
      return ok({ status: "deleted" });
    }

    // facets / tags
    if (path === "/api/facets") return ok({ models: countBy("model_name"), loras: loraCounts() });
    if (path === "/api/tags") return ok(tagCounts());

    // browse
    if (path === "/api/browse") return ok(browse(u.searchParams.get("path")));

    // images list
    if (path === "/api/images" && method === "GET") {
      const items = filterImages(u.searchParams);
      return ok({ items, next_cursor: null });
    }
    // single image ops
    if ((m = path.match(/^\/api\/images\/(\d+)$/))) {
      const id = +m[1];
      const idx = IMAGES.findIndex(x => x.id === id);
      if (idx < 0) return notFound();
      if (method === "GET") return ok(IMAGES[idx]);
      if (method === "DELETE") { IMAGES.splice(idx, 1); return ok({ ok: true }); }
    }
    if ((m = path.match(/^\/api\/images\/(\d+)\/favorite$/)) && method === "POST") {
      const im = IMAGES.find(x => x.id === +m[1]); if (im) im.is_favorite = body.value ? 1 : 0;
      return ok({ ok: true });
    }
    if ((m = path.match(/^\/api\/images\/(\d+)\/tags$/)) && method === "POST") {
      const im = IMAGES.find(x => x.id === +m[1]); if (im) im.tags = body.tags || [];
      return ok({ ok: true });
    }
    if ((m = path.match(/^\/api\/images\/(\d+)\/rename$/)) && method === "POST") {
      const im = IMAGES.find(x => x.id === +m[1]);
      if (im) im.rel_path = im.rel_path.split("/").slice(0, -1).concat(body.new_name).join("/");
      return ok({ ok: true });
    }
    if ((m = path.match(/^\/api\/images\/(\d+)\/export$/)) && method === "POST") {
      const im = IMAGES.find(x => x.id === +m[1]);
      const name = (im?.rel_path || "image.png").split("/").pop();
      return ok({ status: "exported", path: `${body.to_dir}/${name}` });
    }
    // thumb / file are loaded as <img src> (handled by __mockImg), not fetch
    return notFound();
  };

  function loraCounts() {
    const m = new Map();
    for (const im of IMAGES) for (const L of im.loras) m.set(L.name, (m.get(L.name) || 0) + 1);
    return [...m.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
  }

  /* ---------- WebSocket stub (prevents reconnect spam) ------------------- */
  const RealWS = window.WebSocket;
  window.WebSocket = function () {
    const self = { readyState: 1, send() {}, close() {}, onopen: null, onmessage: null, onclose: null, onerror: null };
    setTimeout(() => { if (self.onopen) self.onopen({}); }, 30);
    return self;
  };
  window.WebSocket.OPEN = 1;
  if (RealWS) { window.WebSocket.prototype = RealWS.prototype; }

  /* ---------- thumbnail filler (prototype only) -------------------------
     The real app lazy-loads thumbs via IntersectionObserver. That observer is
     unreliable inside static sandbox previews, so here we eagerly resolve any
     tile image that has a data-id but no src. Harmless next to the real flow. */
  function fillThumbs(rootNode) {
    const imgs = (rootNode || document).querySelectorAll
      ? (rootNode || document).querySelectorAll("img[data-id]")
      : [];
    imgs.forEach(img => {
      if (img.src && img.src.length > 50) return;
      const id = Number(img.dataset.id);
      if (!id) return;
      img.src = thumbFor(id);
      if (img.complete) img.classList.add("loaded");
      else img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
    });
  }
  function watchGallery() {
    const g = document.getElementById("gallery");
    if (!g) { setTimeout(watchGallery, 50); return; }
    fillThumbs(g);
    new MutationObserver(muts => {
      for (const mu of muts) for (const n of mu.addedNodes) {
        if (n.nodeType === 1) fillThumbs(n.matches && n.matches("img[data-id]") ? n.parentNode : n);
      }
    }).observe(g, { childList: true, subtree: true });
    // a couple of sweeps to catch async first paint
    setTimeout(() => fillThumbs(g), 200);
    setTimeout(() => fillThumbs(g), 700);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchGallery);
  else watchGallery();

  /* ---------- clipboard fallback (sandbox-safe) -------------------------- */
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => {} }, configurable: true });
  } else {
    const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
    navigator.clipboard.writeText = async (t) => { try { await orig(t); } catch (_) {} };
  }
})();

/**
 * Local-only top toggle: Local working copy vs What Render users see.
 * Injected only when /api/config reports localSurfaces (POP_MODE=edit).
 */
(function () {
  const STORAGE_LOCAL = "popLocalPath";
  const STORAGE_RENDER = "popRenderPath";

  function path() {
    return location.pathname || "/";
  }

  function isRenderTestPath(p) {
    return p === "/preview" || p.startsWith("/preview/")
      || p === "/render-admin" || p.startsWith("/render-admin/");
  }

  function isLocalPath(p) {
    return p === "/edit" || p.startsWith("/edit/")
      || p === "/admin" || (p.startsWith("/admin") && !p.startsWith("/render-admin"));
  }

  function remember() {
    const p = path();
    try {
      if (isRenderTestPath(p)) sessionStorage.setItem(STORAGE_RENDER, p);
      else if (isLocalPath(p)) sessionStorage.setItem(STORAGE_LOCAL, p);
    } catch (e) {}
  }

  function lastLocal() {
    try {
      const p = sessionStorage.getItem(STORAGE_LOCAL);
      if (p && isLocalPath(p)) return p;
    } catch (e) {}
    return "/edit";
  }

  function lastRender() {
    try {
      const p = sessionStorage.getItem(STORAGE_RENDER);
      if (p && isRenderTestPath(p)) return p;
    } catch (e) {}
    return "/preview";
  }

  function injectStyles() {
    if (document.getElementById("pop-local-switcher-style")) return;
    const style = document.createElement("style");
    style.id = "pop-local-switcher-style";
    style.textContent = `
      #pop-local-switcher {
        position: sticky; top: 0; z-index: 10000;
        display: flex; flex-direction: column; align-items: center; gap: 0.35rem;
        padding: 0.5rem 0.75rem 0.55rem;
        background: #0c0a09;
        border-bottom: 1px solid #292524;
        font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      }
      #pop-local-switcher .pop-toggle {
        display: inline-flex; border: 1px solid #44403c; border-radius: 999px;
        overflow: hidden; background: #1c1917;
      }
      #pop-local-switcher .pop-toggle a {
        text-decoration: none; color: #a8a29e; font-size: 0.78rem; font-weight: 600;
        padding: 0.4rem 0.95rem; background: transparent;
      }
      #pop-local-switcher .pop-toggle a:hover { color: #e7e5e4; }
      #pop-local-switcher .pop-toggle a.is-active {
        background: #fff1e6; color: #1e3a8a;
      }
      #pop-local-switcher .pop-toggle a.is-active[data-mode="render"] {
        background: #1e3a5f; color: #dbeafe;
      }
      #pop-local-switcher .pop-sub {
        display: flex; flex-wrap: wrap; gap: 0.35rem; justify-content: center;
      }
      #pop-local-switcher .pop-sub a {
        text-decoration: none; color: #d6d3d1; font-size: 0.72rem; font-weight: 600;
        padding: 0.2rem 0.55rem; border-radius: 5px; border: 1px solid transparent;
      }
      #pop-local-switcher .pop-sub a:hover { border-color: #44403c; color: #fff; }
      #pop-local-switcher .pop-sub a.is-active {
        border-color: #93c5fd; color: #bfdbfe;
      }
      #pop-local-switcher .pop-caption {
        margin: 0; color: #78716c; font-size: 0.65rem; letter-spacing: 0.02em;
      }
      /* Full-screen PDF/Reader must not sit under the sticky local chrome */
      body.viewer-open #pop-local-switcher,
      body.reader-open #pop-local-switcher {
        display: none !important;
      }
    `;
    document.head.appendChild(style);
  }

  function mount(cfg) {
    remember();
    injectStyles();

    const p = path();
    const onRender = isRenderTestPath(p);
    const mode = onRender ? "render" : "local";

    const existing = document.getElementById("pop-local-switcher");
    if (existing) existing.remove();

    const bar = document.createElement("div");
    bar.id = "pop-local-switcher";
    bar.setAttribute("role", "navigation");
    bar.setAttribute("aria-label", "Local vs Render view");

    const localHref = lastLocal();
    const renderHref = lastRender();

    let sub = "";
    if (mode === "local") {
      const onEdit = p === "/edit" || p.startsWith("/edit/");
      const onAdmin = p.startsWith("/admin") && !p.startsWith("/render-admin");
      sub = `
        <div class="pop-sub">
          <a href="/edit" class="${onEdit ? "is-active" : ""}">Edit &amp; Append</a>
          <a href="/admin" class="${onAdmin ? "is-active" : ""}">Admin</a>
        </div>`;
    } else {
      const onPreview = p === "/preview" || p.startsWith("/preview/");
      const onHosted = p === "/render-admin" || p.startsWith("/render-admin/");
      sub = `
        <div class="pop-sub">
          <a href="/preview" class="${onPreview ? "is-active" : ""}">Public site (/)</a>
          <a href="/render-admin" class="${onHosted ? "is-active" : ""}">Hosted admin (/admin)</a>
        </div>`;
    }

    bar.innerHTML = `
      <p class="pop-caption">Local test switcher (not shown on Render)</p>
      <div class="pop-toggle">
        <a href="${localHref}" data-mode="local" class="${mode === "local" ? "is-active" : ""}">Local working copy</a>
        <a href="${renderHref}" data-mode="render" class="${mode === "render" ? "is-active" : ""}">What Render users see</a>
      </div>
      ${sub}
    `;

    document.body.insertBefore(bar, document.body.firstChild);

    // Hide legacy local-nav if present
    const legacy = document.getElementById("local-nav");
    if (legacy) legacy.style.display = "none";
    const escape = document.querySelector(".local-escape");
    if (escape) escape.style.display = "none";
  }

  async function init() {
    try {
      const cfg = await (await fetch("/api/config")).json();
      if (!(cfg.localSurfaces || (cfg.editable && !cfg.isProduction))) return;
      mount(cfg);
    } catch (e) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

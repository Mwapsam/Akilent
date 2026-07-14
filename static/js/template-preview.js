/*
 * Shared live-preview module for the template builder split-screen layout.
 * Device-width switching and full-screen toggling are handled declaratively
 * by Alpine (see template_edit.html, #preview-pane's x-data) — this module
 * owns the draft-render fetch against template_preview's POST branch, the
 * split-pane resize gutter, and a debounce() helper reused by
 * raw-editor.js/email-builder.js so both modes share one implementation.
 *
 * Placed after the editor markup in the page, so DOM lookups below run
 * synchronously against an already-parsed document — no DOMContentLoaded
 * wrapper needed, which keeps window.TemplatePreview available as soon as
 * this <script> tag executes (raw-editor.js/email-builder.js load after it
 * and depend on that).
 */
(function () {
  "use strict";

  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments;
      var ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(ctx, args);
      }, wait);
    };
  }

  function initResizer() {
    var container = document.getElementById("editor-split");
    var gutter = document.getElementById("split-gutter");
    if (!container || !gutter) return;

    var STORAGE_KEY = "email-builder-split-ratio";
    var dragging = false;
    var desktopQuery = window.matchMedia("(min-width: 1024px)");

    function applyRatio(ratio) {
      if (desktopQuery.matches) {
        container.style.gridTemplateColumns = ratio * 100 + "% 10px " + (1 - ratio) * 100 + "%";
      } else {
        container.style.gridTemplateColumns = "";
      }
    }

    var stored = parseFloat(localStorage.getItem(STORAGE_KEY) || "");
    if (stored && stored > 0.2 && stored < 0.8) applyRatio(stored);

    gutter.addEventListener("mousedown", function (e) {
      dragging = true;
      gutter.classList.add("is-dragging");
      e.preventDefault();
    });

    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      var rect = container.getBoundingClientRect();
      var ratio = (e.clientX - rect.left) / rect.width;
      ratio = Math.min(0.75, Math.max(0.25, ratio));
      applyRatio(ratio);
      localStorage.setItem(STORAGE_KEY, String(ratio));
    });

    document.addEventListener("mouseup", function () {
      if (!dragging) return;
      dragging = false;
      gutter.classList.remove("is-dragging");
    });

    // Keyboard equivalent of the drag gesture for non-mouse users.
    gutter.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      var current = parseFloat(localStorage.getItem(STORAGE_KEY) || "") || 0.5;
      var next = current + (e.key === "ArrowRight" ? 0.05 : -0.05);
      next = Math.min(0.75, Math.max(0.25, next));
      applyRatio(next);
      localStorage.setItem(STORAGE_KEY, String(next));
    });

    desktopQuery.addEventListener("change", function () {
      applyRatio(parseFloat(localStorage.getItem(STORAGE_KEY) || "") || 0.5);
    });
  }

  var previewFrame = document.getElementById("email-builder-preview-frame");
  var previewWarnings = document.getElementById("email-builder-preview-warnings");
  var requestId = 0;

  function refresh(previewUrl, csrfToken, draft) {
    if (!previewFrame || !previewUrl) return;
    var thisRequest = ++requestId;
    previewFrame.classList.add("opacity-50", "pointer-events-none");

    fetch(previewUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(draft),
    })
      .then(function (res) {
        if (!res.ok) {
          var err = new Error("Preview request failed (" + res.status + ")");
          err.status = res.status;
          throw err;
        }
        return res.json();
      })
      .then(function (data) {
        if (thisRequest !== requestId) return; // a newer request already landed
        var doc = previewFrame.contentDocument || previewFrame.contentWindow.document;
        doc.open();
        doc.write(data.html || "");
        doc.close();
        if (previewWarnings) {
          if (data.missing_variables && data.missing_variables.length) {
            previewWarnings.textContent = "Missing variables: " + data.missing_variables.join(", ");
            previewWarnings.classList.remove("hidden");
          } else {
            previewWarnings.textContent = "";
            previewWarnings.classList.add("hidden");
          }
        }
      })
      .catch(function (err) {
        // Leave the last-good preview on screen (don't blank it), but tell
        // the user it's stale instead of failing silently.
        if (thisRequest !== requestId) return;
        if (previewWarnings) {
          previewWarnings.textContent =
            "Preview couldn't be refreshed" + (err && err.status ? " (" + err.status + ")" : "") + " — showing the last successful preview.";
          previewWarnings.classList.remove("hidden");
        }
      })
      .finally(function () {
        if (thisRequest === requestId) {
          previewFrame.classList.remove("opacity-50", "pointer-events-none");
        }
      });
  }

  initResizer();

  window.TemplatePreview = { refresh: refresh, debounce: debounce };
})();

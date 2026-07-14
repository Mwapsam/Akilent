/*
 * "Send Test" button: POSTs the current draft (from whichever mode is
 * active — window.RawEditor.buildDraft() or window.EmailBuilderEditor's
 * getDraft(), both return the same {subject, text_body, html_body,
 * variables} shape) to the send-test endpoint and reports the result via
 * the app's toast system.
 *
 * Loads after email-builder.js/raw-editor.js (see template_edit.html script
 * order), so window.RawEditor / window.EmailBuilderEditor are already set
 * by the time this file's DOMContentLoaded listener runs.
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("template-send-test");
    var configEl = document.getElementById("template-editor-config");
    if (!btn || !configEl) return;
    var config = JSON.parse(configEl.getAttribute("data-config") || "{}");

    function currentDraft() {
      if (window.EmailBuilderEditor && window.EmailBuilderEditor.getDraft) {
        return window.EmailBuilderEditor.getDraft();
      }
      if (window.RawEditor) {
        return window.RawEditor.buildDraft();
      }
      return null;
    }

    btn.addEventListener("click", function () {
      var draft = currentDraft();
      if (!draft || !config.sendTestUrl) return;

      btn.disabled = true;
      var originalText = btn.textContent;
      btn.textContent = "Sending…";

      fetch(config.sendTestUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": config.csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(draft),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (result.ok) {
            if (window.toast) window.toast("success", "Test email queued — check your inbox shortly.");
          } else if (window.toast) {
            window.toast("danger", (result.data && result.data.error) || "Couldn't send test email.");
          }
        })
        .catch(function () {
          if (window.toast) window.toast("danger", "Couldn't send test email — check your connection.");
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = originalText;
        });
    });
  });
})();

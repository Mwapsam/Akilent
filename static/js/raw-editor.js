/*
 * Raw-HTML mode wiring: reads the shared settings fields (#tpl-name,
 * #tpl-subject, #tpl-sample-variables) plus #tpl-text-body/#tpl-html-body,
 * and feeds a debounced draft to the shared TemplatePreview module so the
 * right-hand pane stays live as the user types.
 *
 * Explicit "Save changes" stays a native form submit (see #email-builder-save
 * in template_edit.html, which targets #raw-editor-form via its `form`
 * attribute) — no JS is required for that to work.
 */
(function () {
  "use strict";

  function init() {
    var form = document.getElementById("raw-editor-form");
    if (!form) return;
    var config = JSON.parse(form.getAttribute("data-config") || "{}");

    var fields = {
      subject: document.getElementById("tpl-subject"),
      sampleVariables: document.getElementById("tpl-sample-variables"),
      textBody: document.getElementById("tpl-text-body"),
      htmlBody: document.getElementById("tpl-html-body"),
    };

    function buildDraft() {
      var variables = config.sampleVariables || {};
      try {
        variables = JSON.parse((fields.sampleVariables && fields.sampleVariables.value) || "{}");
      } catch (e) {
        // Keep the last-valid variables rather than breaking the preview on
        // a mid-edit invalid JSON keystroke.
      }
      return {
        subject: fields.subject ? fields.subject.value : "",
        text_body: fields.textBody ? fields.textBody.value : "",
        html_body: fields.htmlBody ? fields.htmlBody.value : "",
        variables: variables,
      };
    }

    var refreshPreview = window.TemplatePreview.debounce(function () {
      window.TemplatePreview.refresh(config.previewUrl, config.csrfToken, buildDraft());
    }, 400);

    [fields.subject, fields.sampleVariables, fields.textBody, fields.htmlBody].forEach(function (el) {
      if (el) el.addEventListener("input", refreshPreview);
    });

    // Initial paint so the pane isn't blank before the first edit.
    refreshPreview();

    window.RawEditor = { form: form, fields: fields, buildDraft: buildDraft, config: config };
  }

  document.addEventListener("DOMContentLoaded", init);
})();

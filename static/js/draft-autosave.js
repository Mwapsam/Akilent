/*
 * Autosave, localStorage draft recovery, undo/redo button wiring, and
 * centrally-bound keyboard shortcuts for the template builder.
 *
 * Loads after email-builder.js/raw-editor.js (see template_edit.html script
 * order) and waits for DOMContentLoaded so window.EmailBuilderEditor /
 * window.RawEditor — both assigned inside those files' own DOMContentLoaded
 * listeners, registered earlier — are guaranteed to already be set by the
 * time this listener runs (listeners fire in registration order).
 *
 * Autosave intentionally skips server-side version snapshots (autosave=1 on
 * the existing template_edit endpoint) — only the explicit "Save changes"
 * button/Ctrl+S creates a version, so history stays meaningful instead of
 * being spammed on every debounce tick.
 */
(function () {
  "use strict";

  function stripTags(html) {
    var div = document.createElement("div");
    div.innerHTML = html;
    return (div.textContent || div.innerText || "").replace(/\s+\n/g, "\n").trim();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var configEl = document.getElementById("template-editor-config");
    if (!configEl || !window.TemplatePreview) return;
    var config = JSON.parse(configEl.getAttribute("data-config") || "{}");
    var mode = config.mode || "raw";
    var draftKey = "email-draft:" + (config.saveUrl || "") + ":" + mode;

    var statusEl = document.getElementById("email-builder-status");
    var undoBtn = document.getElementById("editor-undo");
    var redoBtn = document.getElementById("editor-redo");

    function setStatus(text) {
      if (statusEl) statusEl.textContent = text;
    }

    function currentDraft() {
      var nameEl = document.getElementById("tpl-name");
      var sampleVarsEl = document.getElementById("tpl-sample-variables");
      var base = {
        name: nameEl ? nameEl.value : config.name || "",
        sample_variables: sampleVarsEl ? sampleVarsEl.value : JSON.stringify(config.sampleVariables || {}),
      };
      if (mode === "blocks" && window.EmailBuilderEditor) {
        var editor = window.EmailBuilderEditor;
        var css = editor.getCss();
        var html = editor.getHtml();
        var subjectEl = document.getElementById("tpl-subject");
        base.subject = subjectEl ? subjectEl.value : config.subject || "";
        base.html_body = css ? "<style>" + css + "</style>" + html : html;
        base.text_body = stripTags(base.html_body);
        base.content_blocks = JSON.stringify(editor.getProjectData());
        return base;
      }
      if (window.RawEditor) {
        var d = window.RawEditor.buildDraft();
        base.subject = d.subject;
        base.text_body = d.text_body;
        base.html_body = d.html_body;
        return base;
      }
      return null;
    }

    function applyDraft(fields) {
      var nameEl = document.getElementById("tpl-name");
      var subjectEl = document.getElementById("tpl-subject");
      var sampleVarsEl = document.getElementById("tpl-sample-variables");
      var textEl = document.getElementById("tpl-text-body");
      var htmlEl = document.getElementById("tpl-html-body");
      if (nameEl && fields.name != null) nameEl.value = fields.name;
      if (subjectEl && fields.subject != null) subjectEl.value = fields.subject;
      if (sampleVarsEl && fields.sample_variables != null) sampleVarsEl.value = fields.sample_variables;
      if (textEl && fields.text_body != null) textEl.value = fields.text_body;
      if (htmlEl && fields.html_body != null) htmlEl.value = fields.html_body;
      if (mode === "blocks" && window.EmailBuilderEditor && fields.content_blocks) {
        try {
          window.EmailBuilderEditor.loadProjectData(JSON.parse(fields.content_blocks));
        } catch (e) {
          /* corrupt snapshot — leave canvas as-is */
        }
      }
      [textEl, htmlEl, subjectEl].forEach(function (el) {
        if (el) el.dispatchEvent(new Event("input"));
      });
    }

    // --- Autosave ------------------------------------------------------------
    function clearDraft() {
      try {
        localStorage.removeItem(draftKey);
      } catch (e) {
        /* storage unavailable — non-fatal */
      }
    }

    function postAutosave() {
      var draft = currentDraft();
      if (!draft) return;
      var form = new FormData();
      form.append("csrfmiddlewaretoken", config.csrfToken);
      form.append("autosave", "1");
      form.append("name", draft.name || "");
      form.append("subject", draft.subject || "");
      form.append("text_body", draft.text_body || "");
      form.append("html_body", draft.html_body || "");
      form.append("sample_variables", draft.sample_variables || "{}");
      if (draft.content_blocks) form.append("content_blocks", draft.content_blocks);
      form.append("builder_mode", mode);

      setStatus("Saving…");
      fetch(config.saveUrl, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: form,
      })
        .then(function (res) {
          return res.ok ? res.json() : Promise.reject();
        })
        .then(function (data) {
          setStatus("All changes saved");
          config.updatedAt = data.updated_at;
          clearDraft();
        })
        .catch(function () {
          setStatus("Autosave failed — changes kept locally");
        });
    }

    var autosave = window.TemplatePreview.debounce(postAutosave, 1500);

    // --- localStorage draft backup --------------------------------------------
    function saveLocalDraft() {
      var draft = currentDraft();
      if (!draft) return;
      try {
        localStorage.setItem(
          draftKey,
          JSON.stringify({
            fields: draft,
            capturedAt: new Date().toISOString(),
          })
        );
      } catch (e) {
        /* storage full/unavailable — non-fatal */
      }
    }
    var backupDraft = window.TemplatePreview.debounce(saveLocalDraft, 400);

    function onEdit() {
      autosave();
      backupDraft();
    }

    ["tpl-name", "tpl-subject", "tpl-sample-variables", "tpl-text-body", "tpl-html-body"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("input", onEdit);
    });
    if (mode === "blocks" && window.EmailBuilderEditor) {
      window.EmailBuilderEditor.on("update", onEdit);
    }

    // --- Draft-recovery banner -------------------------------------------------
    function showRestoreBanner(stored) {
      var anchor = document.getElementById("template-settings-card");
      if (!anchor || !anchor.parentNode) return;
      var bar = document.createElement("div");
      bar.className =
        "card card-pad mb-4 border-amber-200 bg-amber-50/60 flex flex-wrap items-center justify-between gap-3";
      bar.setAttribute("role", "status");
      bar.setAttribute("aria-live", "polite");
      var when = new Date(stored.capturedAt).toLocaleString();
      var msg = document.createElement("p");
      msg.className = "text-sm text-amber-800";
      msg.textContent = "Unsaved changes from " + when + " were found.";
      var actions = document.createElement("div");
      actions.className = "flex gap-2";
      var restoreBtn = document.createElement("button");
      restoreBtn.type = "button";
      restoreBtn.className = "btn btn-primary btn-sm";
      restoreBtn.textContent = "Restore";
      var discardBtn = document.createElement("button");
      discardBtn.type = "button";
      discardBtn.className = "btn btn-secondary btn-sm";
      discardBtn.textContent = "Discard";
      actions.appendChild(restoreBtn);
      actions.appendChild(discardBtn);
      bar.appendChild(msg);
      bar.appendChild(actions);
      anchor.parentNode.insertBefore(bar, anchor);

      restoreBtn.addEventListener("click", function () {
        applyDraft(stored.fields);
        bar.remove();
        clearDraft();
      });
      discardBtn.addEventListener("click", function () {
        bar.remove();
        clearDraft();
      });
    }

    function checkForDraft() {
      var raw;
      try {
        raw = localStorage.getItem(draftKey);
      } catch (e) {
        return;
      }
      if (!raw) return;
      var stored;
      try {
        stored = JSON.parse(raw);
      } catch (e) {
        clearDraft();
        return;
      }
      // Compare against what's actually on the page right now (server-rendered
      // at load) rather than an updated_at timestamp: if nothing has been
      // saved since the draft was captured, updated_at is unchanged too, which
      // would otherwise make a genuinely-unsaved draft look "already synced".
      var current = currentDraft();
      if (current && JSON.stringify(current) === JSON.stringify(stored.fields)) {
        clearDraft(); // identical to current state — nothing to recover
        return;
      }
      showRestoreBanner(stored);
    }
    checkForDraft();

    // --- Undo/redo -------------------------------------------------------------
    var rawStack = { entries: [], index: -1 };
    var MAX_ENTRIES = 50;

    function refreshUndoRedoState() {
      if (mode === "blocks" && window.EmailBuilderEditor) {
        var um = window.EmailBuilderEditor.UndoManager;
        if (undoBtn) undoBtn.disabled = !um.hasUndo();
        if (redoBtn) redoBtn.disabled = !um.hasRedo();
      } else {
        if (undoBtn) undoBtn.disabled = rawStack.index <= 0;
        if (redoBtn) redoBtn.disabled = rawStack.index >= rawStack.entries.length - 1;
      }
    }

    function pushRawSnapshot() {
      var draft = currentDraft();
      if (!draft) return;
      var snapshot = JSON.stringify(draft);
      if (rawStack.entries[rawStack.index] === snapshot) return; // no-op edit
      rawStack.entries = rawStack.entries.slice(0, rawStack.index + 1);
      rawStack.entries.push(snapshot);
      if (rawStack.entries.length > MAX_ENTRIES) rawStack.entries.shift();
      rawStack.index = rawStack.entries.length - 1;
      refreshUndoRedoState();
    }

    function applyRawSnapshot(index) {
      var snapshot = rawStack.entries[index];
      if (!snapshot) return;
      rawStack.index = index;
      applyDraft(JSON.parse(snapshot));
      refreshUndoRedoState();
    }

    if (mode === "blocks") {
      if (window.EmailBuilderEditor) {
        window.EmailBuilderEditor.on("update", refreshUndoRedoState);
      }
    } else {
      pushRawSnapshot(); // seed with initial state
      var snapshotOnEdit = window.TemplatePreview.debounce(pushRawSnapshot, 400);
      ["tpl-subject", "tpl-text-body", "tpl-html-body"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener("input", snapshotOnEdit);
      });
    }
    refreshUndoRedoState();

    if (undoBtn) {
      undoBtn.addEventListener("click", function () {
        if (mode === "blocks" && window.EmailBuilderEditor) {
          window.EmailBuilderEditor.UndoManager.undo();
        } else if (rawStack.index > 0) {
          applyRawSnapshot(rawStack.index - 1);
        }
      });
    }
    if (redoBtn) {
      redoBtn.addEventListener("click", function () {
        if (mode === "blocks" && window.EmailBuilderEditor) {
          window.EmailBuilderEditor.UndoManager.redo();
        } else if (rawStack.index < rawStack.entries.length - 1) {
          applyRawSnapshot(rawStack.index + 1);
        }
      });
    }

    // --- Keyboard shortcuts ------------------------------------------------------
    document.addEventListener("keydown", function (e) {
      var mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      var key = e.key.toLowerCase();
      if (key === "s") {
        e.preventDefault();
        var saveBtn = document.getElementById("email-builder-save");
        if (saveBtn) saveBtn.click();
        return;
      }
      if (key !== "z") return;
      // Leave native per-field undo alone when focus is inside a raw-mode
      // textarea; only take over for blocks mode or focus outside any field.
      var tag = document.activeElement && document.activeElement.tagName;
      if (mode !== "blocks" && tag === "TEXTAREA") return;
      e.preventDefault();
      if (e.shiftKey) {
        if (redoBtn) redoBtn.click();
      } else {
        if (undoBtn) undoBtn.click();
      }
    });
  });
})();

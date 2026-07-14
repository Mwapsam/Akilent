/*
 * Lightweight `{{ tag }}` typeahead for plain <textarea>/<input> fields.
 * No dependencies. Attaches to any element with a `data-merge-tags`
 * attribute holding a JSON array of dotted variable paths, e.g.
 * '["first_name", "contact.phone"]'.
 *
 * Trigger: typing "{{" (optionally followed by a partial name) opens a
 * dropdown filtered by what's typed after "{{". Enter/click selects,
 * inserting "{{ path }}" in place of the partial text. Escape closes.
 */
(function () {
  "use strict";

  function getMergeTags(el) {
    try {
      return JSON.parse(el.getAttribute("data-merge-tags") || "[]");
    } catch (e) {
      return [];
    }
  }

  function currentTagQuery(el) {
    var pos = el.selectionStart;
    var value = el.value.slice(0, pos);
    var openIdx = value.lastIndexOf("{{");
    if (openIdx === -1) return null;
    var since = value.slice(openIdx + 2);
    // If a "}}" already closes this tag before the cursor, we're not inside one.
    if (since.indexOf("}}") !== -1) return null;
    // Don't trigger across newlines/other "{{" — keep it to the immediate tag.
    if (/[\n{}]/.test(since)) return null;
    return { start: openIdx, query: since.trim() };
  }

  function buildDropdown() {
    var el = document.createElement("div");
    el.className = "merge-tag-dropdown";
    el.style.cssText =
      "position:absolute;z-index:50;background:#fff;border:1px solid #e5e7eb;" +
      "border-radius:0.5rem;box-shadow:0 4px 12px rgba(0,0,0,0.08);" +
      "max-height:180px;overflow-y:auto;min-width:160px;font-size:0.8rem;";
    el.style.display = "none";
    document.body.appendChild(el);
    return el;
  }

  function positionDropdown(dropdown, target) {
    var rect = target.getBoundingClientRect();
    dropdown.style.left = window.scrollX + rect.left + "px";
    dropdown.style.top = window.scrollY + rect.bottom + 4 + "px";
  }

  function attach(target) {
    var tags = getMergeTags(target);
    if (!tags.length) return;

    var dropdown = buildDropdown();
    var activeIndex = -1;
    var currentMatches = [];
    var currentStart = -1;

    function close() {
      dropdown.style.display = "none";
      activeIndex = -1;
      currentMatches = [];
    }

    function render(matches) {
      dropdown.innerHTML = "";
      matches.forEach(function (tag, i) {
        var item = document.createElement("div");
        item.textContent = tag;
        item.className = "merge-tag-dropdown-item";
        item.style.cssText =
          "padding:0.35rem 0.6rem;cursor:pointer;" +
          (i === activeIndex ? "background:#f3f4f6;" : "");
        item.addEventListener("mousedown", function (e) {
          e.preventDefault();
          select(tag);
        });
        dropdown.appendChild(item);
      });
    }

    function select(tag) {
      var value = target.value;
      var pos = target.selectionStart;
      var closeIdx = value.indexOf("}}", currentStart);
      var afterOpen = currentStart + 2;
      var end = closeIdx !== -1 && closeIdx < afterOpen + 40 ? closeIdx : pos;
      var replacement = " " + tag + " }}";
      target.value = value.slice(0, afterOpen) + replacement + value.slice(end);
      var newPos = afterOpen + replacement.length;
      target.setSelectionRange(newPos, newPos);
      target.focus();
      close();
    }

    function update() {
      var ctx = currentTagQuery(target);
      if (!ctx) {
        close();
        return;
      }
      currentStart = ctx.start;
      var q = ctx.query.toLowerCase();
      currentMatches = tags.filter(function (t) {
        return t.toLowerCase().indexOf(q) !== -1;
      });
      if (!currentMatches.length) {
        close();
        return;
      }
      activeIndex = 0;
      positionDropdown(dropdown, target);
      dropdown.style.display = "block";
      render(currentMatches);
    }

    target.addEventListener("input", update);
    target.addEventListener("click", update);
    target.addEventListener("blur", function () {
      // Delay so a mousedown selection on the dropdown still fires first.
      setTimeout(close, 150);
    });
    target.addEventListener("keydown", function (e) {
      if (dropdown.style.display === "none") return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, currentMatches.length - 1);
        render(currentMatches);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        render(currentMatches);
      } else if (e.key === "Enter" && activeIndex >= 0) {
        e.preventDefault();
        select(currentMatches[activeIndex]);
      } else if (e.key === "Escape") {
        close();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-merge-tags]").forEach(attach);
  });
})();

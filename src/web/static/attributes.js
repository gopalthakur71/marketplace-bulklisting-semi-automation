// Preview & edit screen: SKU selection for the filtered download, and the live
// character counter on capped free-text fields.
//
// Vendored like everything else in static/ — no CDN, no build step. Everything
// here is an enhancement: with JS off the panels still save and the full
// download still works; only the selection bar and the counter go quiet.
(function () {
  "use strict";

  var bar = document.getElementById("sku-select-bar");

  function picks() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".sku-pick"));
  }

  function checked() {
    return picks().filter(function (b) { return b.checked; });
  }

  function refresh() {
    if (!bar) { return; }
    var all = picks();
    var on = checked();
    var countEl = document.getElementById("sku-pick-count");
    var btn = document.getElementById("download-selected");
    var master = document.getElementById("sku-pick-all");

    if (countEl) { countEl.textContent = String(on.length); }
    // Hidden rather than disabled at zero: there is nothing to explain about a
    // button for an empty selection.
    if (btn) { btn.hidden = on.length === 0; }
    if (master) {
      master.checked = all.length > 0 && on.length === all.length;
      // Partial selection reads as indeterminate, not unchecked — otherwise
      // one more click looks like it will select nothing.
      master.indeterminate = on.length > 0 && on.length < all.length;
    }
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t) { return; }

    if (t.classList && t.classList.contains("sku-pick")) {
      // The checkbox sits inside <summary>, whose default action toggles the
      // card. Ticking a box must not expand it.
      e.stopPropagation();
      refresh();
      return;
    }

    if (t.id === "sku-pick-all") {
      e.stopPropagation();
      var on = t.checked;
      picks().forEach(function (b) { b.checked = on; });
      refresh();
      return;
    }

    if (t.id === "download-selected") {
      var sel = checked();
      if (!sel.length) { return; }
      var params = sel.map(function (b) {
        return "sku=" + encodeURIComponent(b.value);
      }).join("&");
      // A real navigation, not fetch(): the browser has to see the response's
      // Content-Disposition for the file to be saved under its own name.
      window.location = "/generate/download/" + bar.dataset.job +
        "/selected?" + params;
    }
  });

  // Selection survives an htmx swap (a per-SKU save re-renders parts of a
  // panel), so recount rather than assuming the boxes are untouched.
  document.body.addEventListener("htmx:afterSwap", refresh);

  function countChars(input) {
    var limit = parseInt(input.dataset.limit, 10);
    if (!limit) { return; }
    // The saved value is trimmed, so the counter measures the trimmed length —
    // otherwise trailing spaces would show a name as over when it exports fine.
    var n = input.value.trim().length;
    var label = input.parentNode.querySelector(".char-count");
    if (label) {
      label.textContent = n + " / " + limit;
      // maxlength keeps n <= limit from the keyboard; a longer value can only
      // reach here in an uploaded workbook, where the server-rendered error is
      // already showing.
      label.classList.toggle("over-limit", n > limit);
    }
    input.classList.toggle("over-limit", n > limit);
  }

  document.addEventListener("input", function (e) {
    if (e.target && e.target.dataset && e.target.dataset.limit) {
      countChars(e.target);
    }
  });

  refresh();
})();

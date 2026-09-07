// Click any table header to sort. Works on server-rendered and JS-rendered tables.
(function () {
  function cellValue(row, i) {
    const el = row.cells[i];
    return el ? el.textContent.trim() : "";
  }

  function numeric(v) {
    const m = v.replace(/[,\s]/g, "").match(/-?\d+\.?\d*/);
    return m ? parseFloat(m[0]) : null;
  }

  function sortBy(table, i, dir) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = [...tbody.rows].filter((r) => r.cells.length > i);
    if (rows.length < 2) return;
    const allNum = rows.every((r) => numeric(cellValue(r, i)) !== null);
    rows.sort((a, b) => {
      let av, bv;
      if (allNum) {
        av = numeric(cellValue(a, i));
        bv = numeric(cellValue(b, i));
      } else {
        av = cellValue(a, i).toLowerCase();
        bv = cellValue(b, i).toLowerCase();
      }
      if (av < bv) return dir === "asc" ? -1 : 1;
      if (av > bv) return dir === "asc" ? 1 : -1;
      return 0;
    });
    const frag = document.createDocumentFragment();
    rows.forEach((r) => frag.appendChild(r));
    tbody.appendChild(frag);
  }

  function wire(table) {
    const head = table.tHead;
    if (!head || !head.rows.length) return;
    const ths = [...head.rows[head.rows.length - 1].cells];
    let observer = null;

    function applyActive() {
      const active = ths.find(
        (t) => t.classList.contains("sort-asc") || t.classList.contains("sort-desc")
      );
      if (!active) return;
      if (observer) observer.disconnect();
      sortBy(table, +active.dataset.col, active.classList.contains("sort-asc") ? "asc" : "desc");
      if (observer && table.tBodies[0]) observer.observe(table.tBodies[0], { childList: true });
    }

    ths.forEach((th, i) => {
      if (!th.textContent.trim()) return;
      th.classList.add("sort");
      th.addEventListener("click", () => {
        const asc = !th.classList.contains("sort-asc");
        ths.forEach((t) => t.classList.remove("sort-asc", "sort-desc"));
        th.classList.add(asc ? "sort-asc" : "sort-desc");
        th.dataset.col = i;
        applyActive();
      });
    });

    // re-apply the active sort when rows are re-rendered (paginated/filtered tables)
    if (table.tBodies[0] && "MutationObserver" in window) {
      observer = new MutationObserver(() => applyActive());
      observer.observe(table.tBodies[0], { childList: true });
    }
  }

  addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("table.info").forEach((t) => {
      if (t.querySelector("td.label")) return; // skip key/value tables
      wire(t);
    });
  });
})();

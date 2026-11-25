let posActiveRowId = null;
let posPendingEnterQuery = null;

function posSetActiveRow(rowEl) {
  document.querySelectorAll("#pos-cart-body tr").forEach((tr) => {
    tr.classList.remove("bg-sky-50");
  });
  if (rowEl) {
    rowEl.classList.add("bg-sky-50");
    posActiveRowId = rowEl.id;
  } else {
    posActiveRowId = null;
  }
}

function posGetActiveRow() {
  if (posActiveRowId) {
    const el = document.getElementById(posActiveRowId);
    if (el) return el;
  }
  const rows = document.querySelectorAll("#pos-cart-body tr");
  return rows.length ? rows[rows.length - 1] : null;
}

function posAddItem(variantId, sku, label, stock, wholesalePrice, retailPrice) {
  const body = document.getElementById("pos-cart-body");
  const existing = document.getElementById(`pos-row-${variantId}`);
  if (existing) {
    const qtyInput = existing.querySelector('input[name="qty"]');
    qtyInput.value = parseInt(qtyInput.value || "1", 10) + 1;
    posSetActiveRow(existing);
    posRecalc();
    return;
  }
  const priceList = document.getElementById("pos-price-list").value;
  const unitPrice = priceList === "WHOLESALE" ? wholesalePrice : retailPrice;
  const canEditPrice = document.getElementById("pos-form")?.dataset.canEditPrice === "1";
  const stockLabel = document.getElementById("pos-form")?.dataset.labelStock || "Stock";
  const readonlyAttr = canEditPrice ? "" : "readonly";
  const tr = document.createElement("tr");
  tr.id = `pos-row-${variantId}`;
  tr.dataset.wholesale = wholesalePrice;
  tr.dataset.retail = retailPrice;
  tr.className = "border-b border-slate-100 cursor-pointer";
  tr.addEventListener("click", () => posSetActiveRow(tr));
  tr.innerHTML = `
    <td class="p-2 text-xs">
      <input type="hidden" name="variant_id" value="${variantId}" />
      <div class="font-semibold text-slate-900">${label}</div>
      <div class="font-mono text-slate-400">${sku} &middot; ${stockLabel}: ${stock}</div>
    </td>
    <td class="p-2 w-20"><input type="number" name="qty" min="1" value="1" onfocus="posSetActiveRow(this.closest('tr'))" oninput="posRecalc()" /></td>
    <td class="p-2 w-28"><input type="number" step="0.001" min="0" name="unit_price" value="${unitPrice}" ${readonlyAttr} onfocus="posSetActiveRow(this.closest('tr'))" oninput="posRecalc()" /></td>
    <td class="p-2 w-20"><input type="number" step="0.01" min="0" max="100" name="discount_percent" value="0" onfocus="posSetActiveRow(this.closest('tr'))" oninput="posRecalc()" /></td>
    <td class="p-2 text-right font-semibold pos-line-total">${unitPrice}</td>
    <td class="p-2 text-right"><button type="button" onclick="event.stopPropagation(); posRemoveRow(this.closest('tr'));" class="text-rose-600 font-bold text-xs">✕</button></td>
  `;
  body.appendChild(tr);
  posSetActiveRow(tr);
  posRecalc();
}

function posRemoveRow(tr) {
  if (!tr) return;
  const wasActive = tr.id === posActiveRowId;
  tr.remove();
  if (wasActive) {
    posSetActiveRow(posGetActiveRow());
  }
  posRecalc();
}

function posAdjustActiveQty(delta) {
  const row = posGetActiveRow();
  if (!row) return;
  const qtyInput = row.querySelector('input[name="qty"]');
  if (!qtyInput) return;
  const nextVal = Math.max(1, parseInt(qtyInput.value || "1", 10) + delta);
  qtyInput.value = nextVal;
  posSetActiveRow(row);
  posRecalc();
}

function posResetBill() {
  const body = document.getElementById("pos-cart-body");
  if (body) body.innerHTML = "";
  posActiveRowId = null;
  const disc = document.getElementById("pos-inv-disc");
  if (disc) disc.value = "0.000";
  const paid = document.querySelector('#pos-form input[name="amount_paid"]');
  if (paid) paid.value = "0.000";
  const ref = document.querySelector('#pos-form input[name="payment_reference"]');
  if (ref) ref.value = "";
  const search = document.getElementById("pos-search-input");
  if (search) {
    search.value = "";
    search.dispatchEvent(new Event("search", { bubbles: true }));
    search.focus();
  }
  posRecalc();
}

function posTryQuickAddMatch(query) {
  const results = Array.from(document.querySelectorAll("#pos-search-results .pos-result-item"));
  if (!results.length) return false;
  const qLower = (query || "").trim().toLowerCase();
  let match = null;
  if (qLower) {
    match = results.find(
      (btn) =>
        (btn.dataset.barcode || "").trim().toLowerCase() === qLower ||
        (btn.dataset.sku || "").trim().toLowerCase() === qLower
    );
  }
  if (!match && results.length === 1) {
    match = results[0];
  }
  if (!match && qLower) {
    match = results[0];
  }
  if (!match) return false;
  match.click();
  const search = document.getElementById("pos-search-input");
  if (search) {
    search.value = "";
    search.focus();
    if (window.htmx) {
      const url = search.getAttribute("hx-get");
      if (url) window.htmx.ajax("GET", url, { target: "#pos-search-results" });
    }
  }
  return true;
}

function posSwitchPriceList() {
  const priceList = document.getElementById("pos-price-list").value;
  document.querySelectorAll("#pos-cart-body tr").forEach((tr) => {
    const priceInput = tr.querySelector('input[name="unit_price"]');
    if (priceInput) {
      priceInput.value = priceList === "WHOLESALE" ? tr.dataset.wholesale : tr.dataset.retail;
    }
  });
  posRecalc();
}

function posSyncCustomerType(selectEl) {
  const opt = selectEl.options[selectEl.selectedIndex];
  if (!opt) return;
  const ctype = opt.dataset.type;
  const pl = document.getElementById("pos-price-list");
  if (pl && (ctype === "WHOLESALE" || ctype === "RETAIL")) {
    pl.value = ctype;
    posSwitchPriceList();
  }
}

function posRecalc() {
  let total = 0;
  document.querySelectorAll("#pos-cart-body tr").forEach((tr) => {
    const qty = parseFloat(tr.querySelector('input[name="qty"]')?.value || "0");
    const price = parseFloat(tr.querySelector('input[name="unit_price"]')?.value || "0");
    const discPct = parseFloat(tr.querySelector('input[name="discount_percent"]')?.value || "0");
    const net = qty * price * (1 - discPct / 100);
    tr.querySelector(".pos-line-total").textContent = net.toFixed(3);
    total += net;
  });
  const invDisc = parseFloat(document.getElementById("pos-inv-disc")?.value || "0");
  const finalNet = Math.max(total - invDisc, 0);
  const el = document.getElementById("pos-est-total");
  if (el) el.textContent = finalNet.toFixed(3);
}

function posHandleSubmit(form) {
  const rows = document.querySelectorAll("#pos-cart-body tr");
  if (rows.length === 0) {
    alert(form.dataset.msgEmpty || "Please add at least one shoe variant to the bill.");
    return false;
  }
  const btn = document.getElementById("pos-submit-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = form.dataset.msgProcessing || "Processing...";
  }
  return true;
}

document.addEventListener("DOMContentLoaded", () => {
  const customerSelect = document.querySelector('#pos-form select[name="customer"]');
  if (customerSelect) posSyncCustomerType(customerSelect);

  const searchInput = document.getElementById("pos-search-input");
  const resultsEl = document.getElementById("pos-search-results");
  if (searchInput && resultsEl) {
    searchInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const q = searchInput.value.trim();
      if (!q) return;
      // If results already match barcode/SKU, add immediately; otherwise trigger immediate HTMX search and add on swap.
      const exactInDom = Array.from(resultsEl.querySelectorAll(".pos-result-item")).find(
        (btn) =>
          (btn.dataset.barcode || "").trim().toLowerCase() === q.toLowerCase() ||
          (btn.dataset.sku || "").trim().toLowerCase() === q.toLowerCase()
      );
      if (exactInDom) {
        posTryQuickAddMatch(q);
      } else if (window.htmx) {
        posPendingEnterQuery = q;
        const url = searchInput.getAttribute("hx-get");
        if (url) {
          window.htmx.ajax("GET", `${url}?q=${encodeURIComponent(q)}`, { target: "#pos-search-results" });
        }
      } else {
        posTryQuickAddMatch(q);
      }
    });

    document.body.addEventListener("htmx:afterSwap", (e) => {
      if (e.target && e.target.id === "pos-search-results" && posPendingEnterQuery) {
        const q = posPendingEnterQuery;
        posPendingEnterQuery = null;
        posTryQuickAddMatch(q);
      }
    });
  }

  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : "";
    const inInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      const form = document.getElementById("pos-form");
      if (form) {
        e.preventDefault();
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else if (posHandleSubmit(form)) form.submit();
      }
      return;
    }

    if (e.altKey && (e.key === "n" || e.key === "N")) {
      e.preventDefault();
      posResetBill();
      return;
    }

    if (inInput) return;

    if (e.key === "/") {
      const si = document.getElementById("pos-search-input");
      if (si) {
        e.preventDefault();
        si.focus();
        si.select();
      }
    } else if (e.key === "+" || e.key === "=") {
      e.preventDefault();
      posAdjustActiveQty(1);
    } else if (e.key === "-" || e.key === "_") {
      e.preventDefault();
      posAdjustActiveQty(-1);
    } else if (e.key === "Delete" || e.key === "Backspace") {
      const row = posGetActiveRow();
      if (row) {
        e.preventDefault();
        posRemoveRow(row);
      }
    }
  });
});

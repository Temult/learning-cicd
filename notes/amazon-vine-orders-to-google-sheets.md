# Amazon Vine Orders → Google Sheets + PDF Snapshots

This is a **Tampermonkey userscript (v1.6)** for the Amazon Vine Orders page. It adds controls to scan the visible orders table, check which orders are already recorded, and send order metadata and product-page HTML to a companion Google Apps Script web app. That web app is responsible for updating your Google Sheet and creating a PDF snapshot in Drive.

**Setup and limitations:**

1. Install Tampermonkey and create a new script.
2. Expand the code below and copy it into Tampermonkey.
3. Replace `YOUR_SCRIPT_ID_HERE` in `WEBAPP_URL` with **your own** deployed Google Apps Script web-app URL. **Do not publish a personal deployment URL, token, or order history.**
4. The matching pages are Amazon.com Vine order-history pages. `Scan Now` inspects the **currently visible** order rows; `Add All` works through those rows with a delay between requests.
5. The companion Google Apps Script backend is **not included** here. This file alone cannot create a Sheet or PDFs. Test with one order before using bulk actions.

<details>
<summary><strong>Show full userscript (v1.6)</strong></summary>

```javascript
// ==UserScript==
// @name         Vine Orders → Amazon Vine Order History (PDF snapshots, v1.6 no-append APIs)
// @namespace    vine2sheet
// @version      1.6
// @description  Log Vine orders to your Google Sheet with coupon-aware price + PDF snapshots in Drive.
// @match        https://www.amazon.com/vine/orders*
// @match        https://amazon.com/vine/orders*
// @match        https://smile.amazon.com/vine/orders*
// @inject-into  content
// @noframes
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      script.google.com
// @connect      googleusercontent.com
// @connect      www.amazon.com
// ==/UserScript==

(function () {
  'use strict';
  console.log('[vine2sheet] v1.6 loaded on', location.href);

  // REDACTED: Replace with your Google Apps Script deployment URL before sharing/using
  const WEBAPP_URL  = 'https://script.google.com/macros/s/YOUR_SCRIPT_ID_HERE/exec';
  const THROTTLE_MS_DEFAULT = 3500;

  const $  = (sel) => document.querySelector(sel); const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // ================= UI: Top bar via insertAdjacentHTML only =================
  function ensureBar(){
    if ($('#v2s-bar')) return;

    const markup = `
<div id="v2s-bar" style="position:sticky;top:0;z-index:999999;background:#fff;border:1px solid #ddd;padding:8px;display:flex;gap:8px;align-items:center">
  <button id="v2s-scan"   style="border:1px solid #888;padding:6px 10px;cursor:pointer;border-radius:6px;background:#f7f7f7">Scan Now</button>
  <button id="v2s-addall" style="border:1px solid #888;padding:6px 10px;cursor:pointer;border-radius:6px;background:#f7f7f7">Add All (throttled)</button>
  <button id="v2s-exists" style="border:1px solid #888;padding:6px 10px;cursor:pointer;border-radius:6px;background:#f7f7f7">Mark Existing</button>
  <label>Throttle(ms)
    <input id="v2s-throttle" type="number" min="1000" step="250" style="margin-left:6px;width:90px">
  </label>
  <span id="v2s-status" style="font-size:12px;padding:2px 6px;border-radius:9999px;border:1px solid #ccc">loaded</span>
</div>`.trim();

    const anchor = document.querySelector('.vvp-tab-content') || document.body;
    try {
      anchor.insertAdjacentHTML('afterbegin', markup);
      console.log('[vine2sheet] bar inserted');
    } catch (e) {
      console.warn('[vine2sheet] bar insert failed', e);
      return;
    }

    // Wire events after markup exists
    const input = $('#v2s-throttle');
    if (input) {
      input.value = String(GM_getValue('v2s_throttle', THROTTLE_MS_DEFAULT));
      input.addEventListener('change', e => GM_setValue('v2s_throttle', +e.target.value || THROTTLE_MS_DEFAULT));
    }
    const btnScan = $('#v2s-scan');
    const btnExists = $('#v2s-exists');
    const btnAll = $('#v2s-addall');
    btnScan && btnScan.addEventListener('click', scanNow);
    btnExists && btnExists.addEventListener('click', () => markExisting(parseRows()));
    btnAll && btnAll.addEventListener('click', () => addAll(parseRows()));

    // Health ping
    fetch(WEBAPP_URL).then(r=>r.text()).then(t=>{
      setStatus('webapp ok', true); console.log('[vine2sheet] webapp ok:', t);
    }).catch(err=>{
      setStatus('webapp unreachable', false); console.warn('[vine2sheet] webapp unreachable', err);
    });
  }

  function setStatus(msg, ok){
    const s = $('#v2s-status'); if (!s) return; s.textContent = msg; s.style.borderColor = ok ? '#0a0' : '#a60'; s.style.color = ok ? '#0a0' : '#a60'; }

  // ================= Parse orders table =================
  function parseRows() {
    const rows = $$('tr.vvp-orders-table--row').map(tr => {
      const tds = tr.querySelectorAll('td');
      const a = tr.querySelector('td.vvp-orders-table--text-col a.a-link-normal');
      const dpLink = a?.href || '';
      const title  = a?.textContent.trim() || '';
      const asin   = (dpLink.match(/\/dp\/([A-Z0-9]{10})/) || [])[1] || '';
      const tsEl   = tr.querySelector('td[data-order-timestamp]');
      const orderTs = tsEl?.getAttribute('data-order-timestamp') || '';
      const orderDate = orderTs ? new Date(+orderTs).toISOString().slice(0,10) : '';
      const etv = (tds[3]?.textContent || '').trim();
      const orderDetails = tr.querySelector('td.vvp-orders-table--actions-col a')?.href || '';
      const key = `${asin}|${orderDate}`;
      return { tr, asin, title, dpLink, orderDate, etv, orderDetails, key };
    }).filter(r => r.asin);
    console.log('[vine2sheet] parseRows ->', rows.length);
    return rows;
  }

  // ================= Per-row UI via insertAdjacentHTML =================
  function wireButtons(rows){
    rows.forEach(r => {
      if (r.tr.querySelector('.v2s-rowbtn')) return; // already wired
      const cell = r.tr.querySelector('td.vvp-orders-table--actions-col') || r.tr.lastElementChild;
      const id = 'btn_' + Math.random().toString(36).slice(2);
      const chipId = 'chip_' + Math.random().toString(36).slice(2);
      const html = `
<span> </span>
<button class="v2s-rowbtn" id="${id}"
  style="border:1px solid #888;padding:6px 10px;cursor:pointer;border-radius:6px;background:#f7f7f7">
  Archive → Sheet (PDF)
</button>
<span> </span>
<span class="v2s-chip" id="${chipId}" style="font-size:12px;padding:2px 6px;border-radius:9999px;border:1px solid #a60;color:#a60">new?</span>
`.trim();
      try { cell.insertAdjacentHTML('beforeend', html); }
      catch(e){ console.warn('[vine2sheet] row UI insert failed', e); }
      const btn = document.getElementById(id);
      const chip = document.getElementById(chipId);
      btn && btn.addEventListener('click', () => archiveOne(r, chip));
    });
    console.log('[vine2sheet] wired row buttons:', rows.length);
  }

  // ================= Wait helper =================
  function waitForSelector(sel, timeout=20000) {
    return new Promise((resolve, reject) => {
      const el = $(sel);
      if (el) return resolve(el);
      const obs = new MutationObserver(() => { const m=$(sel); if (m) { obs.disconnect(); resolve(m); }});
      obs.observe(document.documentElement, { childList:true, subtree:true });
      setTimeout(() => { obs.disconnect(); reject(new Error('timeout')); }, timeout);
    });
  }

  // ================= Web App calls (GM_xmlhttpRequest) =================
  function apiExists(keys) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: WEBAPP_URL,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ action: 'exists', keys }),
        onload: res => { try { resolve(JSON.parse(res.responseText).result || {}); } catch (e) { reject(e); } },
        onerror: reject
      });
    });
  }
  function apiAdd(payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: WEBAPP_URL,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ action: 'add', ...payload }),
        onload: res => { try { resolve(JSON.parse(res.responseText)); } catch (e) { reject(e); } },
        onerror: reject
      });
    });
  }
  function apiUploadPdf(filename, html) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: WEBAPP_URL,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ action: 'uploadPdf', filename, html }),
        onload: res => { try { resolve(JSON.parse(res.responseText)); } catch (e) { reject(e); } },
        onerror: reject
      });
    });
  }

  // ================= DP fetch & parse =================
  function fetchDP(url) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({ method: 'GET', url, onload: res => resolve(res.responseText), onerror: reject });
    });
  }
  function parseDetail(html) {
    const priceMatch =
      html.match(/id=\"corePrice_desktop\"[\s\S]*?<span[^>]*class=\"a-offscreen\"[^>]*>([^<]+)<\/span>/) ||
      html.match(/<span[^>]*class=\"a-offscreen\"[^>]*>(\$\d+[\.,]?\d*)<\/span>/);
    const price = priceMatch ? priceMatch[1].trim() : '';
    const couponBlock = (html.match(/([\s\S]{0,200}coupon[\s\S]{0,200})/i) || [])[1] || '';
    const pct = (couponBlock.match(/(\d{1,3})\s*%/) || [])[1];
    const amt = (couponBlock.match(/\$(\d+(?:\.\d{2})?)/) || [])[1];
    const couponText = couponBlock ? (pct ? `${pct}% coupon` : (amt ? `$${amt} coupon` : 'coupon')) : '';
    return { price, couponText };
  }

  // ================= Exists / Archive / Add All =================
  async function markExisting(rows) {
    if (!rows.length) return;
    setStatus('checking…', false);
    try {
      const keys = rows.map(r => r.key);
      const map = await apiExists(keys);
      rows.forEach(r => {
        // find/create a chip in the last cell using HTML only
        let chip = r.tr.querySelector('.v2s-chip');
        if (!chip) {
          const cell = r.tr.lastElementChild;
          try {
            cell.insertAdjacentHTML('beforeend',
              `<span class="v2s-chip" style="font-size:12px;padding:2px 6px;border-radius:9999px;border:1px solid #ccc">?</span>`
            );
          } catch(e){ console.warn('[vine2sheet] chip insert failed', e); }
          chip = r.tr.querySelector('.v2s-chip:last-of-type');
        }
        const exists = !!map[r.key];
        if (chip) {
          chip.textContent = exists ? 'in sheet' : 'new';
          chip.style.borderColor = exists ? '#0a0' : '#a60';
          chip.style.color = exists ? '#0a0' : '#a60';
        }
      });
      setStatus('idle', true);
    } catch (e) {
      console.warn('[vine2sheet] exists failed', e);
      setStatus('exists failed', false);
    }
  }

  async function archiveOne(r, chipEl) {
    setStatus('fetching DP…', false);
    const html = await fetchDP(r.dpLink);
    const { price, couponText } = parseDetail(html);
    const payload = {
      ts: new Date().toISOString(),
      asin: r.asin,
      title: r.title,
      etv: r.etv,
      listingPrice: price,
      couponText,
      dpLink: r.dpLink,
      orderDetails: r.orderDetails,
      orderDate: r.orderDate,
      key: r.key
    };
    setStatus('rendering PDF…', false);
    try {
      const up = await apiUploadPdf(`${r.orderDate}_${r.asin}`, html);
      if (up && up.ok) payload.snapshotLink = up.url;
    } catch (e) {
      console.warn('[vine2sheet] PDF render failed (continuing)', e);
    }
    setStatus('adding row…', false);
    await apiAdd(payload);
    setStatus('done', true);
    if (chipEl) {
      chipEl.textContent = 'in sheet';
      chipEl.style.borderColor = '#0a0';
      chipEl.style.color = '#0a0';
    }
  }

  async function addAll(rows) {
    const delay = +( $('#v2s-throttle').value ) || THROTTLE_MS_DEFAULT;
    for (const r of rows) {
      try { await archiveOne(r); } catch (e) { console.warn('[vine2sheet] row failed', r, e); }
      await new Promise(res => setTimeout(res, delay + Math.floor(Math.random() * 500)));
    }
  }

  // ================= Scan / Boot =================
  function scanNow(){
    const rows = parseRows();
    wireButtons(rows);
    markExisting(rows).catch(()=>{});
    setStatus(`found ${rows.length} rows`, true);
  }

  async function boot(){
    ensureBar();
    try {
      await waitForSelector('tr.vvp-orders-table--row', 20000);
      const rows = parseRows();
      wireButtons(rows);
      markExisting(rows).catch(()=>{});
    } catch {
      console.warn('[vine2sheet] orders table not found (yet). Use Scan Now.');
    }
  }

  boot();
  new MutationObserver(() => {
    if (location.pathname.includes('/vine/orders')) boot();
  }).observe(document.documentElement, { childList:true, subtree:true });
})();
```

</details>

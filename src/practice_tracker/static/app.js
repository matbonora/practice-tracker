// Practice tracker PWA. Plain JS, no build step. Every action goes through the REST API.

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUSES = ["want", "learning", "polishing", "repertoire", "retired"];
const LOG_TYPES = ["insight", "problem", "idea", "task"];
const RATINGS = { 1: "Again", 2: "Hard", 3: "Good", 4: "Easy" };

const state = { tab: "timer", ov: null, tick: null, hobbyId: Number(localStorage.getItem("pt_hobby")) || null };

// ------------------------------------------------------------------ API

async function api(path, { method = "GET", body, form } = {}) {
  const headers = { Authorization: `Bearer ${localStorage.getItem("pt_token") || ""}` };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const r = await fetch(path, { method, headers, body: payload });
  if (r.status === 401) { openSettings("Enter your API token to continue."); throw new Error("API token needed"); }
  if (r.status === 204) return null;
  const isJson = (r.headers.get("content-type") || "").includes("json");
  const data = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const detail = data && data.detail;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail || r.statusText));
    err.candidates = data && data.candidates;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

async function guard(fn) {
  try { await fn(); }
  catch (e) { toast(e.message + (e.candidates ? ": " + e.candidates.map((c) => c.name).join(", ") : "")); }
}

// --------------------------------------------------------------- lookups

const hobbies = () => (state.ov ? state.ov.hobbies : []);
const hobby = (id) => hobbies().find((h) => h.id === id);
const currentHobby = () => hobby(state.hobbyId) || hobbies()[0];
const allAreas = () => hobbies().flatMap((h) => h.areas);
const area = (id) => allAreas().find((a) => a.id === id);
const allItems = () => hobbies().flatMap((h) => h.items);
const item = (id) => allItems().find((i) => i.id === id);
const kind = (id) => hobbies().flatMap((h) => h.kinds).find((k) => k.id === id);
const kindFor = (itemId, areaId) => kind((item(itemId) || {}).kind_id || (area(areaId) || {}).default_kind_id);
const itemsIn = (areaId) => allItems().filter((i) => i.area_id === areaId && i.status !== "retired");

const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${Math.round(m % 60)} min` : `${Math.round(m)} min`);
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
const fmtDay = (iso) => (iso ? new Date(iso).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "");
const toLocalInput = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

function options(list, selected, { empty } = {}) {
  const head = empty !== undefined ? `<option value="">${esc(empty)}</option>` : "";
  return head + list.map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(selected ?? "") ? "selected" : ""}>${esc(o.label)}</option>`).join("");
}

function hobbySelect(id = "hobby-select") {
  return `<select id="${id}">${options(hobbies().map((h) => ({ value: h.id, label: h.name })), currentHobby()?.id)}</select>`;
}

function bindHobbySelect(id = "hobby-select") {
  const sel = $("#" + id);
  if (sel) sel.onchange = () => { state.hobbyId = Number(sel.value); localStorage.setItem("pt_hobby", sel.value); render(); };
}

// ------------------------------------------------------------- rendering

async function refresh() {
  state.ov = await api("/api/overview");
  const n = state.ov.inbox_count;
  const badge = $("#inbox-badge");
  badge.hidden = !n;
  badge.textContent = n;
}

async function render() {
  clearInterval(state.tick);
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
  if (!state.ov) return;
  const views = { timer: viewTimer, inbox: viewInbox, items: viewItems, log: viewLog, stats: viewStats };
  await guard(() => views[state.tab]());
}

async function go(tab) {
  state.tab = tab;
  await guard(refresh);
  await render();
}

// ----------------------------------------------------------------- timer

async function viewTimer() {
  const ov = state.ov;
  const t = ov.timer;
  const summary = await api("/api/summary");
  let top;
  if (t.running) {
    const s = t.session;
    const preset = s.preset.item_id ? item(s.preset.item_id)?.title : s.preset.area_id ? area(s.preset.area_id)?.name : "";
    top = `
      <div class="card">
        <div class="spread"><b>${esc(hobby(s.hobby_id)?.name || "Practice")}</b><span class="quiet">${esc(s.source)} · since ${fmtDate(s.started_at)}</span></div>
        ${preset ? `<div class="quiet">${esc(preset)}</div>` : ""}
        <div id="clock" class="clock">--:--</div>
        ${t.forgotten ? `<p class="quiet" style="color:var(--warn)">Running for a long time. Forgot to stop? You can fix the end time after stopping.</p>` : ""}
        <button id="stop" class="primary big">Stop</button>
        <div class="row" style="margin-top:8px">
          <button id="split">Next block${s.splits.length ? ` (${s.splits.length})` : ""}</button>
          <button id="discard" class="danger">Discard</button>
        </div>
      </div>`;
  } else {
    const h = currentHobby();
    top = `
      <div class="card">
        <div class="clock idle">0:00</div>
        <label>Hobby</label>${hobbySelect()}
        <div class="row">
          <div><label>Area (optional)</label><select id="t-area">${options(h.areas.map((a) => ({ value: a.id, label: a.name })), "", { empty: "—" })}</select></div>
          <div><label>Item (optional)</label><select id="t-item"><option value="">—</option></select></div>
        </div>
        <button id="start" class="primary big" style="margin-top:12px">Start</button>
        <button id="log-past" style="margin-top:8px;width:100%">Log a past session</button>
      </div>`;
  }

  const streaks = summary.hobbies.map((h) => `
    <div class="card">
      <div class="spread"><b>${esc(h.name)}</b><span class="quiet">${h.days_this_week} day${h.days_this_week === 1 ? "" : "s"} this week</span></div>
      <div class="row" style="margin-top:6px">
        <div class="stat"><b>${Math.round(h.this_week_min)}</b><span class="quiet">of ${h.weekly_goal_min} min</span></div>
        <div class="stat"><b>${h.current_weeks}</b><span class="quiet">week streak</span></div>
        <div class="stat"><b>${h.longest_weeks}</b><span class="quiet">longest</span></div>
      </div>
    </div>`).join("");

  const reviews = ov.reviews_due.length ? `<h2>Repertoire reviews due</h2><div class="card"><ul class="plain">${ov.reviews_due.map((r) => `
      <li><div class="spread"><span>${esc(r.title)}</span><span class="quiet">due ${fmtDay(r.due)}</span></div>
      <div class="row" style="margin-top:6px">${Object.entries(RATINGS).map(([v, l]) => `<button class="small" data-review="${r.id}" data-rating="${v}">${l}</button>`).join("")}</div></li>`).join("")}</ul></div>` : "";

  const tasks = ov.open_tasks.length ? `<h2>Open tasks</h2><div class="card"><ul class="plain">${ov.open_tasks.map((e) => `
      <li><label class="task"><input type="checkbox" data-done="${e.id}"> <span>${esc(e.text)}${e.item_id ? ` <span class="quiet">· ${esc(item(e.item_id)?.title || "")}</span>` : ""}</span></label></li>`).join("")}</ul></div>` : "";

  $("#view").innerHTML = `${top}
    <div class="row"><div class="card stat"><b>${fmtMin(summary.today_min)}</b><span class="quiet">today</span></div>
    <div class="card stat"><b>${fmtMin(summary.week_min)}</b><span class="quiet">this week</span></div></div>
    ${streaks}${reviews}${tasks}`;

  if (t.running) {
    const start = new Date(t.session.started_at).getTime();
    const tick = () => {
      const sec = Math.max(0, Math.floor((Date.now() - start) / 1000));
      const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
      const el = $("#clock");
      if (el) el.textContent = (hh ? `${hh}:${String(mm).padStart(2, "0")}` : `${mm}`) + `:${String(ss).padStart(2, "0")}`;
    };
    tick();
    state.tick = setInterval(tick, 1000);
    $("#stop").onclick = () => guard(async () => {
      const r = await api("/api/timer/stop", { method: "POST", body: {} });
      await refresh();
      if (r.outcome === "inbox") openAssign(r.session);
      else if (r.session) { toast(`Saved ${fmtMin(r.session.minutes)}`); render(); }
      else { toast(r.reason || r.outcome); render(); }
    });
    $("#split").onclick = () => guard(async () => { await api("/api/timer/split", { method: "POST" }); toast("Block marked"); await go("timer"); });
    $("#discard").onclick = () => guard(async () => { if (confirm("Discard this running session?")) { await api("/api/timer", { method: "DELETE" }); await go("timer"); } });
  } else {
    bindHobbySelect();
    const fillItems = () => {
      const a = Number($("#t-area").value);
      $("#t-item").innerHTML = options(a ? itemsIn(a).map((i) => ({ value: i.id, label: i.title })) : [], "", { empty: "—" });
    };
    $("#t-area").onchange = fillItems;
    $("#start").onclick = () => guard(async () => {
      const body = { hobby: currentHobby().id };
      if ($("#t-area").value) body.area = Number($("#t-area").value);
      if ($("#t-item").value) body.item = Number($("#t-item").value);
      await api("/api/timer/start", { method: "POST", body });
      await go("timer");
    });
    $("#log-past").onclick = () => openAssign(null);
  }
  $$("[data-review]").forEach((b) => (b.onclick = () => guard(async () => {
    await api(`/api/items/${b.dataset.review}/review`, { method: "POST", body: { rating: Number(b.dataset.rating) } });
    toast("Review saved"); await go("timer");
  })));
  $$("[data-done]").forEach((c) => (c.onchange = () => guard(async () => {
    await api(`/api/log/${c.dataset.done}`, { method: "PATCH", body: { done: true } }); await go("timer");
  })));
}

// ------------------------------------------------------- assign / log past

function prefillSegments(s) {
  if (!s) return [{ area: null, item: null, minutes: 0, focus: [], params: {} }];
  if (s.segments && s.segments.length) {
    return s.segments.map((x) => ({ area: x.area_id, item: x.item_id, minutes: x.minutes, focus: x.focus, params: x.params,
      review_rating: x.review_rating, note: x.note, next_focus: x.next_focus }));
  }
  const marks = [s.started_at, ...s.splits, s.ended_at].map((x) => new Date(x).getTime());
  const segs = [];
  for (let i = 1; i < marks.length; i++) {
    segs.push({ area: s.preset.area_id || null, item: s.preset.item_id || null,
      minutes: Math.round((marks[i] - marks[i - 1]) / 600) / 100, focus: [], params: {} });
  }
  return segs;
}

function openAssign(session) {
  const isNew = !session;
  const st = {
    hobbyId: session?.hobby_id || currentHobby().id,
    segments: prefillSegments(session),
    rating: session?.rating || "",
    note: session?.note || "",
    start: toLocalInput(new Date(Date.now() - 30 * 60000)),
  };
  const total = () => (isNew ? st.segments.reduce((a, s) => a + (Number(s.minutes) || 0), 0) : session.minutes);

  const segHtml = (s, i) => {
    const h = hobby(st.hobbyId);
    const k = kindFor(s.item, s.area);
    const it = item(s.item);
    const fields = (k?.segment_fields || []).map((f) => {
      const v = s.params[f.name] ?? "";
      if (f.type === "choice") return `<div><label>${esc(f.name)}</label><select data-param="${esc(f.name)}" data-i="${i}">${options((f.options || []).map((o) => ({ value: o, label: o })), v, { empty: "—" })}</select></div>`;
      return `<div><label>${esc(f.name)}</label><input data-param="${esc(f.name)}" data-type="${f.type}" data-i="${i}" ${f.type === "number" ? 'type="number" step="any" inputmode="decimal"' : ""} value="${esc(v)}"></div>`;
    }).join("");
    const review = it && it.status === "repertoire" && k?.review_enabled ? `
      <label>Repertoire review</label><select data-k="review_rating" data-i="${i}">${options(Object.entries(RATINGS).map(([v, l]) => ({ value: v, label: `${v} ${l}` })), s.review_rating, { empty: "not a review" })}</select>` : "";
    return `<div class="seg">
      <div class="row">
        <div><label>Area</label><select data-k="area" data-i="${i}">${options(h.areas.map((a) => ({ value: a.id, label: a.name })), s.area, { empty: "—" })}</select></div>
        <div><label>Item</label><select data-k="item" data-i="${i}">${options(s.area ? itemsIn(s.area).map((x) => ({ value: x.id, label: x.title })) : [], s.item, { empty: "—" })}</select></div>
        <div style="flex:0 0 90px"><label>Minutes</label><input data-k="minutes" data-i="${i}" type="number" step="any" inputmode="decimal" value="${esc(s.minutes)}"></div>
      </div>
      ${k?.focus_options?.length ? `<label>Focus</label><div class="chips">${k.focus_options.map((f) => `<button type="button" class="chip ${s.focus.includes(f) ? "on" : ""}" data-focus="${esc(f)}" data-i="${i}">${esc(f)}</button>`).join("")}</div>` : ""}
      ${fields ? `<div class="row">${fields}</div>` : ""}
      ${review}
      <label>Next time</label><input data-k="next_focus" data-i="${i}" value="${esc(s.next_focus || "")}" placeholder="what to work on next for this item">
      <label>Note</label><input data-k="note" data-i="${i}" value="${esc(s.note || "")}">
      <button type="button" class="small danger" data-remove="${i}" style="margin-top:6px">Remove block</button>
    </div>`;
  };

  const draw = () => {
    const assigned = st.segments.reduce((a, s) => a + (Number(s.minutes) || 0), 0);
    const rest = isNew ? 0 : Math.round((session.minutes - assigned) * 10) / 10;
    $("#dialog-body").innerHTML = `
      <div class="spread"><h1>${isNew ? "Log a past session" : "Assign session"}</h1><button class="icon" data-close>✕</button></div>
      ${isNew ? `<label>Hobby</label>${hobbySelect("a-hobby")}<label>Started</label><input id="a-start" type="datetime-local" value="${esc(st.start)}">`
        : `<p class="quiet">${fmtDate(session.started_at)} · ${fmtMin(session.minutes)} · ${esc(session.source)}</p>`}
      ${st.segments.map(segHtml).join("")}
      <button type="button" id="add-seg">+ Add block</button>
      <p class="quiet">${isNew ? `Total ${fmtMin(assigned)}` : rest >= 0 ? `${fmtMin(rest)} unassigned` : `<span style="color:var(--bad)">${fmtMin(-rest)} too much</span>`}</p>
      <div class="row">
        <div><label>Session rating</label><select id="a-rating">${options([1, 2, 3, 4, 5].map((v) => ({ value: v, label: v })), st.rating, { empty: "—" })}</select></div>
      </div>
      <label>Session note</label><textarea id="a-note">${esc(st.note)}</textarea>
      <div class="row" style="margin-top:12px">
        ${isNew ? "" : `<button id="a-delete" class="danger">Delete session</button>`}
        <button id="a-save" class="primary">Save</button>
      </div>`;
    bind();
  };

  const bind = () => {
    const body = $("#dialog-body");
    $("[data-close]", body).onclick = () => $("#dialog").close();
    if (isNew) {
      $("#a-hobby").onchange = (e) => { st.hobbyId = Number(e.target.value); st.segments = prefillSegments(null); draw(); };
      $("#a-start").onchange = (e) => (st.start = e.target.value);
    }
    $$("[data-k]", body).forEach((el) => {
      el.onchange = () => {
        const s = st.segments[el.dataset.i];
        const k = el.dataset.k;
        const v = el.value;
        if (k === "area") { s.area = v ? Number(v) : null; s.item = null; s.focus = []; s.params = {}; draw(); return; }
        if (k === "item") { s.item = v ? Number(v) : null; draw(); return; }
        if (k === "minutes") { s.minutes = Number(v); draw(); return; }
        if (k === "review_rating") s.review_rating = v ? Number(v) : null;
        else s[k] = v;
      };
    });
    $$("[data-param]", body).forEach((el) => {
      el.onchange = () => {
        const s = st.segments[el.dataset.i];
        if (el.value === "") delete s.params[el.dataset.param];
        else s.params[el.dataset.param] = el.dataset.type === "number" ? Number(el.value) : el.value;
      };
    });
    $$("[data-focus]", body).forEach((el) => {
      el.onclick = () => {
        const s = st.segments[el.dataset.i];
        const f = el.dataset.focus;
        s.focus = s.focus.includes(f) ? s.focus.filter((x) => x !== f) : [...s.focus, f];
        el.classList.toggle("on");
      };
    });
    $$("[data-remove]", body).forEach((el) => (el.onclick = () => { st.segments.splice(Number(el.dataset.remove), 1); draw(); }));
    $("#add-seg").onclick = () => {
      const assigned = st.segments.reduce((a, s) => a + (Number(s.minutes) || 0), 0);
      const last = st.segments[st.segments.length - 1];
      st.segments.push({ area: last?.area || null, item: null, focus: [], params: {},
        minutes: isNew ? 0 : Math.max(0, Math.round((session.minutes - assigned) * 10) / 10) });
      draw();
    };
    $("#a-rating").onchange = (e) => (st.rating = e.target.value);
    $("#a-note").oninput = (e) => (st.note = e.target.value);
    if (!isNew) $("#a-delete").onclick = () => guard(async () => {
      if (!confirm("Delete this session?")) return;
      await api(`/api/sessions/${session.id}`, { method: "DELETE" });
      $("#dialog").close(); toast("Deleted"); await go(state.tab);
    });
    $("#a-save").onclick = () => guard(async () => {
      const segments = st.segments.filter((s) => s.area || s.item).map((s) => ({
        area: s.area, item: s.item, minutes: Number(s.minutes), focus: s.focus, params: s.params,
        review_rating: s.review_rating || null, note: s.note || "", next_focus: s.next_focus || "" }));
      const rating = st.rating ? Number(st.rating) : null;
      if (isNew) {
        await api("/api/sessions", { method: "POST", body: { hobby: st.hobbyId, started_at: new Date(st.start).toISOString(),
          minutes: total(), segments, rating, note: st.note } });
      } else {
        await api(`/api/sessions/${session.id}/segments`, { method: "PUT", body: { segments, rating, note: st.note } });
      }
      $("#dialog").close(); toast("Saved"); await go(state.tab);
    });
  };

  draw();
  $("#dialog").showModal();
}

// ----------------------------------------------------------------- inbox

async function viewInbox() {
  const sessions = await api("/api/inbox");
  $("#view").innerHTML = `
    <h2>Sessions to assign</h2>
    ${sessions.length ? sessions.map((s) => `
      <div class="card spread"><div><b>${fmtMin(s.minutes)}</b> <span class="quiet">${esc(hobby(s.hobby_id)?.name || "")}</span>
      <div class="quiet">${fmtDate(s.started_at)} · ${esc(s.source)}</div></div>
      <button class="primary" data-assign="${s.id}">Assign</button></div>`).join("") : `<p class="quiet">Nothing to assign.</p>`}
    <button id="log-past" style="width:100%">Log a past session</button>`;
  $$("[data-assign]").forEach((b) => (b.onclick = () => openAssign(sessions.find((s) => s.id === Number(b.dataset.assign)))));
  $("#log-past").onclick = () => openAssign(null);
}

// ----------------------------------------------------------------- items

async function viewItems() {
  const h = currentHobby();
  const showRetired = state.showRetired;
  const list = showRetired ? await api(`/api/items?hobby=${h.id}`) : h.items;
  $("#view").innerHTML = `
    <div class="row"><div>${hobbySelect()}</div>
      <label style="flex:0 0 auto;margin:0"><input type="checkbox" id="show-retired" ${showRetired ? "checked" : ""} style="width:auto"> retired</label></div>
    ${h.areas.map((a) => `
      <div class="card">
        <div class="spread"><b>${esc(a.name)}</b><span class="quiet">target ${Math.round(a.target_share * 100)} % · last 14 d ${Math.round(a.actual_share_14d * 100)} %</span></div>
        <ul class="plain">${list.filter((i) => i.area_id === a.id && (showRetired || i.status !== "retired")).map((i) => `
          <li class="spread" data-item="${i.id}" style="cursor:pointer"><span>${esc(i.title)}</span><span class="status ${i.status}">${i.status}</span></li>`).join("")}</ul>
        <div class="row" style="margin-top:8px"><input placeholder="New item" data-new-in="${a.id}"><button class="small" data-add="${a.id}" style="flex:0 0 auto">Add</button></div>
      </div>`).join("")}`;
  bindHobbySelect();
  $("#show-retired").onchange = (e) => { state.showRetired = e.target.checked; render(); };
  $$("[data-item]").forEach((li) => (li.onclick = () => openItem(Number(li.dataset.item))));
  $$("[data-add]").forEach((b) => (b.onclick = () => guard(async () => {
    const input = $(`[data-new-in="${b.dataset.add}"]`);
    if (!input.value.trim()) return;
    await api("/api/items", { method: "POST", body: { area: Number(b.dataset.add), title: input.value.trim() } });
    await go("items");
  })));
}

async function openAttachment(id) {
  const r = await fetch(`/api/attachments/${id}/file`, { headers: { Authorization: `Bearer ${localStorage.getItem("pt_token")}` } });
  if (!r.ok) return toast("Could not load file");
  window.open(URL.createObjectURL(await r.blob()), "_blank");
}

async function upload(ownerType, ownerId, file, caption = "") {
  const form = new FormData();
  form.append("owner_type", ownerType);
  form.append("owner_id", ownerId);
  form.append("caption", caption);
  form.append("file", file);
  return api("/api/attachments", { method: "POST", form });
}

async function openItem(id) {
  const d = await api(`/api/items/${id}`);
  const a = area(d.area_id);
  const k = kindFor(d.id, d.area_id);
  const attrs = (k?.item_fields || []).map((f) => {
    const v = d.attributes[f.name] ?? "";
    if (f.type === "choice") return `<div><label>${esc(f.name)}</label><select data-attr="${esc(f.name)}">${options((f.options || []).map((o) => ({ value: o, label: o })), v, { empty: "—" })}</select></div>`;
    return `<div><label>${esc(f.name)}</label><input data-attr="${esc(f.name)}" data-type="${f.type}" ${f.type === "number" ? 'type="number" step="any"' : ""} value="${esc(v)}"></div>`;
  }).join("");
  $("#dialog-body").innerHTML = `
    <div class="spread"><h1>${esc(d.title)}</h1><button class="icon" data-close>✕</button></div>
    <p class="quiet">${esc(a?.name || "")}${k ? ` · ${esc(k.name)}` : ""} · ${fmtMin(d.total_minutes)} total${d.last_practised ? ` · last ${fmtDay(d.last_practised)}` : ""}</p>
    ${d.next_focus ? `<div class="card"><b>Next time:</b> ${esc(d.next_focus)}</div>` : ""}
    <div class="row">
      <div><label>Title</label><input id="i-title" value="${esc(d.title)}"></div>
      <div><label>Status</label><select id="i-status">${options(STATUSES.map((s) => ({ value: s, label: s })), d.status)}</select></div>
    </div>
    ${attrs ? `<div class="row">${attrs}</div>` : ""}
    ${d.review_due ? `<p class="quiet">Review due ${fmtDay(d.review_due)} · interval ${d.review_interval_days} d · lapses ${d.review_lapses}</p>` : ""}
    <button id="i-save" class="primary" style="margin-top:8px">Save</button>
    <h2>Milestone</h2>
    <input id="m-title" placeholder="e.g. First clean run at 60 BPM">
    <label>Photo or recording (optional)</label><input id="m-file" type="file" accept="image/*,audio/*,video/*">
    <button id="m-add" style="margin-top:8px">Add milestone</button>
    <h2>Files</h2>
    <ul class="plain">${d.attachments.map((x) => `<li><a href="#" data-file="${x.id}">${esc(x.caption || x.path.split("/").pop())}</a> <span class="quiet">${fmtDay(x.created_at)}</span></li>`).join("") || `<li class="quiet">None</li>`}</ul>
    <input id="i-file" type="file" accept="image/*,audio/*,video/*"><button id="i-upload" class="small" style="margin-top:6px">Upload</button>
    <h2>History</h2>
    <ul class="plain">${d.history.map((h) => `<li><div class="spread"><span>${fmtDate(h.started_at)}</span><b>${fmtMin(h.minutes)}</b></div>
      <div class="quiet">${esc([...h.focus, ...Object.entries(h.params).map(([k2, v]) => `${k2} ${v}`)].join(" · "))}${h.review_rating ? ` · review ${RATINGS[h.review_rating]}` : ""}${h.note ? ` · ${esc(h.note)}` : ""}</div></li>`).join("") || `<li class="quiet">Not practised yet</li>`}</ul>`;
  const body = $("#dialog-body");
  $("[data-close]", body).onclick = () => $("#dialog").close();
  $("#i-save").onclick = () => guard(async () => {
    const attributes = {};
    $$("[data-attr]", body).forEach((el) => {
      if (el.value !== "") attributes[el.dataset.attr] = el.dataset.type === "number" ? Number(el.value) : el.value;
    });
    await api(`/api/items/${id}`, { method: "PATCH", body: { title: $("#i-title").value, status: $("#i-status").value, attributes } });
    toast("Saved"); await refresh(); await openItem(id); render();
  });
  $("#m-add").onclick = () => guard(async () => {
    const title = $("#m-title").value.trim();
    if (!title) return toast("Milestone needs a title");
    const m = await api("/api/milestones", { method: "POST", body: { title, item: id } });
    const file = $("#m-file").files[0];
    if (file) await upload("milestone", m.id, file, title);
    toast("Milestone added"); await openItem(id);
  });
  $("#i-upload").onclick = () => guard(async () => {
    const file = $("#i-file").files[0];
    if (!file) return;
    await upload("item", id, file, file.name);
    await openItem(id);
  });
  $$("[data-file]", body).forEach((el) => (el.onclick = (e) => { e.preventDefault(); guard(() => openAttachment(el.dataset.file)); }));
  if (!$("#dialog").open) $("#dialog").showModal();
}

// ------------------------------------------------------------------- log

async function viewLog() {
  const h = currentHobby();
  const since = new Date(Date.now() - 14 * 86400000).toISOString();
  const [entries, sessions] = await Promise.all([api(`/api/log?hobby=${h.id}&limit=100`), api(`/api/sessions?hobby=${h.id}&start=${since}`)]);
  $("#view").innerHTML = `
    ${hobbySelect()}
    <div class="card">
      <div class="row"><div style="flex:0 0 120px"><select id="l-type">${options(LOG_TYPES.map((t) => ({ value: t, label: t })), "insight")}</select></div>
        <div><select id="l-item">${options(h.items.map((i) => ({ value: i.id, label: i.title })), "", { empty: "no item" })}</select></div></div>
      <textarea id="l-text" placeholder="Rapid log: one line" style="margin-top:8px"></textarea>
      <button id="l-add" class="primary" style="margin-top:8px">Add</button>
    </div>
    <h2>Log book</h2>
    <div class="card"><ul class="plain">${entries.map((e) => `
      <li><div class="spread"><span class="${e.done_at ? "done" : ""}">${e.type === "task" ? `<input type="checkbox" data-toggle="${e.id}" ${e.done_at ? "checked" : ""} style="width:auto"> ` : ""}${esc(e.text)}</span>
      <span class="status">${esc(e.type)}</span></div>
      <div class="quiet">${fmtDate(e.created_at)}${e.item_id ? ` · ${esc(item(e.item_id)?.title || "")}` : ""}${e.source !== "manual" ? ` · ${esc(e.source)}` : ""}</div></li>`).join("") || `<li class="quiet">Empty</li>`}</ul></div>
    <h2>Last 14 days</h2>
    <div class="card"><ul class="plain">${sessions.filter((s) => s.ended_at).map((s) => `
      <li data-session="${s.id}" style="cursor:pointer"><div class="spread"><span>${fmtDate(s.started_at)}</span><b>${fmtMin(s.minutes)}</b></div>
      <div class="quiet">${esc(s.segments.map((x) => item(x.item_id)?.title || area(x.area_id)?.name).join(", ") || "unassigned")}${s.rating ? ` · rated ${s.rating}` : ""}${s.note ? ` · ${esc(s.note)}` : ""}</div></li>`).join("") || `<li class="quiet">No sessions</li>`}</ul></div>`;
  bindHobbySelect();
  $("#l-add").onclick = () => guard(async () => {
    const text = $("#l-text").value.trim();
    if (!text) return;
    const body = { text, type: $("#l-type").value, hobby: h.id };
    if ($("#l-item").value) body.item = Number($("#l-item").value);
    await api("/api/log", { method: "POST", body });
    await go("log");
  });
  $$("[data-toggle]").forEach((c) => (c.onchange = () => guard(async () => {
    await api(`/api/log/${c.dataset.toggle}`, { method: "PATCH", body: { done: c.checked } }); await go("log");
  })));
  $$("[data-session]").forEach((li) => (li.onclick = () => openAssign(sessions.find((s) => s.id === Number(li.dataset.session)))));
}

// ----------------------------------------------------------------- stats

async function viewStats() {
  const h = currentHobby();
  const days = state.splitDays || 14;
  const end = new Date();
  const start = new Date(end.getTime() - 12 * 7 * 86400000);
  const ymd = (d) => d.toISOString().slice(0, 10);
  const [weeks, split, streak, milestones] = await Promise.all([
    api(`/api/stats/minutes?period=week&group=hobby&hobby=${h.id}&start=${ymd(start)}&end=${ymd(new Date(end.getTime() + 86400000))}`),
    api(`/api/stats/split?hobby=${h.id}&days=${days}`),
    api(`/api/stats/streak?hobby=${h.id}`),
    api(`/api/milestones?hobby=${h.id}`),
  ]);
  // fill empty weeks so gaps show
  const byWeek = Object.fromEntries(weeks.map((w) => [w.period, w.minutes]));
  const monday = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
  const series = [];
  for (let w = monday(start); w <= end; w.setDate(w.getDate() + 7)) {
    const key = `${w.getFullYear()}-${String(w.getMonth() + 1).padStart(2, "0")}-${String(w.getDate()).padStart(2, "0")}`;
    series.push({ key, label: `${w.getDate()}.${w.getMonth() + 1}`, minutes: byWeek[key] || 0 });
  }
  const max = Math.max(h.weekly_goal_min, ...series.map((s) => s.minutes), 1);
  const totalSplit = split.reduce((a, s) => a + s.minutes, 0);
  $("#view").innerHTML = `
    ${hobbySelect()}
    <div class="card row">
      <div class="stat"><b>${streak.current_weeks}</b><span class="quiet">week streak</span></div>
      <div class="stat"><b>${streak.longest_weeks}</b><span class="quiet">longest</span></div>
      <div class="stat"><b>${Math.round(streak.this_week_min)}</b><span class="quiet">of ${streak.weekly_goal_min} min</span></div>
      <div class="stat"><b>${streak.days_this_week}</b><span class="quiet">days</span></div>
    </div>
    <h2>Minutes per week</h2>
    <div class="card">
      <div class="bars">${series.map((s) => `<div class="bar ${s.minutes >= h.weekly_goal_min ? "met" : ""}" style="height:${(s.minutes / max) * 100}%" title="${s.label}: ${Math.round(s.minutes)} min"></div>`).join("")}
        <div class="goal" style="bottom:${(h.weekly_goal_min / max) * 100}%"></div></div>
      <div class="bar-labels">${series.map((s, i) => `<span>${i % 2 ? "" : s.label}</span>`).join("")}</div>
      <p class="quiet">Dashed line: weekly goal (${h.weekly_goal_min} min). Green: goal reached.</p>
    </div>
    <h2>Area split, last <select id="split-days" style="width:auto;display:inline">${options([14, 30, 90].map((d) => ({ value: d, label: `${d} days` })), days)}</select></h2>
    <div class="card">${split.map((s) => `
      <div class="split"><div class="spread"><span>${esc(s.name)}</span><span class="quiet">${Math.round(s.actual_share * 100)} % (target ${Math.round(s.target_share * 100)} %) · ${fmtMin(s.minutes)}</span></div>
      <div class="track"><div class="fill" style="width:${s.actual_share * 100}%"></div><div class="target" style="left:${s.target_share * 100}%"></div></div></div>`).join("")}
      <p class="quiet">${totalSplit ? `${fmtMin(totalSplit)} assigned.` : "No assigned practice in this period."} Black mark: target.</p>
    </div>
    <h2>Milestones</h2>
    <div class="card"><ul class="plain">${milestones.map((m) => `<li><div class="spread"><span>${esc(m.title)}</span><span class="quiet">${fmtDay(m.date)}</span></div>
      ${m.attachments.map((x) => `<a href="#" data-file="${x.id}">${esc(x.caption || "file")}</a>`).join(" ")}</li>`).join("") || `<li class="quiet">None yet</li>`}</ul></div>`;
  bindHobbySelect();
  $("#split-days").onchange = (e) => { state.splitDays = Number(e.target.value); render(); };
  $$("[data-file]").forEach((el) => (el.onclick = (e) => { e.preventDefault(); guard(() => openAttachment(el.dataset.file)); }));
}

// -------------------------------------------------------------- settings

function openSettings(message = "") {
  $("#dialog-body").innerHTML = `
    <div class="spread"><h1>Settings</h1><button class="icon" data-close>✕</button></div>
    ${message ? `<p>${esc(message)}</p>` : ""}
    <label>API token</label><input id="s-token" type="password" value="${esc(localStorage.getItem("pt_token") || "")}" autocomplete="off">
    <button id="s-save" class="primary" style="margin-top:8px">Save</button>
    <h2>Export</h2>
    <div class="row"><button data-export="json">JSON</button><button data-export="csv">CSV</button></div>
    <h2>API</h2>
    <p><a href="/api/docs" target="_blank">REST API docs</a> · MCP endpoint: <code>${esc(location.origin)}/mcp</code></p>`;
  const body = $("#dialog-body");
  $("[data-close]", body).onclick = () => $("#dialog").close();
  $("#s-save").onclick = async () => {
    localStorage.setItem("pt_token", $("#s-token").value.trim());
    $("#dialog").close();
    await start();
  };
  $$("[data-export]", body).forEach((b) => (b.onclick = () => guard(async () => {
    const r = await fetch(`/api/export.${b.dataset.export}`, { headers: { Authorization: `Bearer ${localStorage.getItem("pt_token")}` } });
    if (!r.ok) throw new Error("Export failed");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = `practice-export.${b.dataset.export}`;
    a.click();
  })));
  if (!$("#dialog").open) $("#dialog").showModal();
}

// ------------------------------------------------------------------ boot

async function start() {
  if (!localStorage.getItem("pt_token")) return openSettings("Enter your API token to start.");
  await go(state.tab);
}

$$("#tabs button").forEach((b) => (b.onclick = () => go(b.dataset.tab)));
$("#settings-btn").onclick = () => openSettings();
document.addEventListener("visibilitychange", () => { if (!document.hidden && localStorage.getItem("pt_token")) go(state.tab); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
start();

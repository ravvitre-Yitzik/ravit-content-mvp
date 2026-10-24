"use strict";
const $ = (id) => document.getElementById(id);
const state = {template: null, sample: null, fields: new Map(), requestId: null, run: null, busy: false, saved: false, mode: "demo", modes: {}, liveAttempted: false, liveRequestId: null};
const statusLabels = {known: "ערך נמסר", not_provided: "לא נמסר", confirmed_unknown: "ללא נתון — איני יודע/ת", conflicting: "נתונים סותרים — נדרש תיקון"};
const labels = ["א", "ב"];
const numericFields = new Set(["floor", "area_sqm", "rooms", "price", "total_floors", "balcony_area_sqm", "garden_area_sqm"]);
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function option(value, text) {
  const node = el("option", "", text);
  node.value = value;
  return node;
}
function notice(message = "", details = [], error = false) {
  const node = $("notice");
  node.replaceChildren();
  node.classList.toggle("error", error);
  node.setAttribute("role", error ? "alert" : "status");
  if (message) node.append(el("strong", "", message));
  if (details.length) {
    const list = el("ul");
    details.forEach((text) => list.append(el("li", "", String(text))));
    node.append(list);
  }
  if (error) node.focus();
}
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {cache: "no-store"} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
  });
  let result;
  try { result = await response.json(); } catch {
    throw new Error("לא התקבלה תשובה תקינה. אפשר לנסות שוב עם אותם נתונים.");
  }
  if (!response.ok) {
    const error = new Error(result.error || "הפעולה לא הושלמה.");
    error.details = [...(result.completion_requests || []).map((x) => "יש להשלים: " + x), ...(result.problems || [])];
    throw error;
  }
  return result;
}
function remember(key, value) {
  try { sessionStorage.setItem(key, value); } catch { /* Backend enforces the one-run allowance independently. */ }
}
function recalled(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}
function liveMode() { return state.mode === "live"; }
function refreshCompare() {
  $("compare-button").disabled = state.busy || !!state.run ||
    (liveMode() && (!$("live-confirmed").checked || state.liveAttempted));
}
function updateModeText() {
  const live = liveMode();
  $("mode-badge").textContent = live ? "מצב חי — פנייה אמיתית לספקים" : "מצב הדגמה — ללא פנייה לספקים";
  $("mode-badge").classList.toggle("live-badge", live);
  $("live-consent").hidden = !live;
  $("mode-help").textContent = live ? "הדוגמה הבדיונית בלבד. הנתונים נעולים לעריכה." :
    state.modes.live?.available ? "הדגמה היא ברירת המחדל. מעבר לחי דורש אישור נוסף." : "הדגמה בלבד. מצב חי אינו פתוח בשרת המקומי.";
  $("action-title").textContent = live ? "הפקה אמיתית — הדוגמה הבדיונית בלבד" : "יצירת תוכן להדגמה בלבד";
  $("action-help").textContent = live ? "קריאה אחת לכל ספק. אומדן עד $0.50 בסך הכול; אין ניסיונות חוזרים." :
    "שתי חבילות מדומות, ללא קריאה לספקים וללא עלות הפקה.";
  $("waiting-title").textContent = live ? "ממתינים לתשובות הספקים…" : "מכינים את שתי חבילות ההדגמה…";
  $("waiting-help").textContent = live ? "קריאה אחת לכל ספק, ברצף וללא ניסיון חוזר. אין להפעיל השוואה נוספת." :
    "אותם נתוני נכס לשתי החבילות. אין פנייה לספקים.";
  if (!state.run) $("compare-button").textContent = live ? state.liveAttempted ?
    "ניסיון חי כבר החל · יש לבדוק את ההרצה השמורה" : "הפעלת השוואה חיה · עד $0.50" : "הפעלת השוואת הדגמה ←";
  $("property-fields").disabled = state.busy || live;
  $("load-sample").disabled = state.busy || live;
  refreshCompare();
}
function report(error) {
  notice(error instanceof TypeError ? "לא ניתן להתחבר למסך המקומי. ודאו שהשרת פועל ונסו שוב." : error.message,
    error.details || [], true);
}
function newId() {
  return crypto.randomUUID();
}
function setBusy(busy) {
  state.busy = busy;
  $("property-fields").disabled = busy || liveMode();
  $("load-sample").disabled = busy || liveMode();
  $("mode").disabled = busy;
  $("live-confirmed").disabled = busy || state.liveAttempted;
  $("load-saved").disabled = busy;
  $("recover-live").disabled = busy || !state.liveRequestId;
  refreshCompare();
  $("grading-fields").disabled = busy || state.saved;
  $("save-button").disabled = busy || state.saved;
  document.querySelectorAll(".score-select").forEach((node) => {node.disabled = busy || state.saved;});
  $("property-form").setAttribute("aria-busy", String(busy));
  $("grading-form").setAttribute("aria-busy", String(busy));
}
function invalidate() {
  state.requestId = null;
  state.run = null;
  state.saved = false;
  $("results-section").hidden = true;
  $("saved-message").hidden = true;
  updateModeText();
  $("step-input").classList.add("active");
  $("step-results").classList.remove("active");
  $("step-grade").classList.remove("active");
  notice();
}
function syncStatus(key) {
  const field = state.fields.get(key);
  field.input.disabled = !["known", "conflicting"].includes(field.status.value);
  if (key === "safe_room") updateConditional();
}
function updateConditional() {
  const safe = state.fields.get("safe_room");
  const shelter = state.fields.get("building_shelter");
  if (!safe || !shelter) return;
  const required = safe.status.value === "known" && safe.input.value === "1";
  shelter.hint.textContent = required ? "נדרש כי אין ממ״ד" : "נדרש כשידוע שאין ממ״ד";
}
function buildFields(template) {
  for (const [key, spec] of Object.entries(template.fields)) {
    const wrapper = el("div", "field" + (key === "ravit_impression" ? " wide" : ""));
    const label = el("label", "", spec.label);
    label.htmlFor = "value-" + key;
    if (spec.required) label.append(el("span", "required-tag", "חובה"));
    else label.append(el("span", "required-tag", spec.required_if ? "חובה מותנית" : "רשות"));
    if (spec.publishable === false) label.append(el("span", "internal-tag", "פנימי · לא לפרסום"));
    const status = el("select", "status-select");
    status.id = "status-" + key;
    status.setAttribute("aria-label", "מצב המידע: " + spec.label);
    for (const [value, text] of Object.entries(statusLabels)) {
      status.append(option(value, key === "ravit_impression" && value === "confirmed_unknown" ? "אין התרשמות למסור" : text));
    }
    let input;
    if (spec.allowed_values) {
      input = el("select");
      input.append(option("", "בחירת ערך"));
      spec.allowed_values.forEach((value, index) => input.append(option(String(index), value === true ? "כן" : value === false ? "לא" : value)));
    } else if (["ravit_impression", "unique_features", "verified_surroundings"].includes(key)) {
      input = el("textarea");
      input.rows = key === "ravit_impression" ? 3 : 2;
    } else {
      input = el("input");
      input.type = "text";
      if (numericFields.has(key)) input.inputMode = "decimal";
    }
    input.id = "value-" + key;
    input.name = key;
    if (!spec.allowed_values) input.maxLength = 10000;
    if (spec.multiple) input.placeholder = "ערכים מופרדים בפסיק";
    const hint = el("small", spec.required_if ? "conditional-note" : "");
    hint.id = "hint-" + key;
    hint.textContent = spec.required_if ? "נדרש כשידוע שאין ממ״ד" : spec.multiple ? "אפשר להזין כמה ערכים ולהפריד בפסיקים." :
      key === "ravit_impression" ? "יש למסור התרשמות או לבחור במפורש שאין התרשמות. אין להמציא דעה או חוויה." :
      key === "price" ? "בהשכרה — לחודש. במכירה — מחיר כולל." :
      spec.must_be_known ? "נדרש ערך ידוע כדי להפיק טיוטה." : "";
    input.setAttribute("aria-describedby", hint.id);
    wrapper.append(label, status, input, hint);
    (spec.required || spec.required_if ? $("required-fields") : $("optional-fields")).append(wrapper);
    state.fields.set(key, {spec, status, input, hint});
    status.addEventListener("change", () => syncStatus(key));
    if (key === "safe_room") input.addEventListener("change", updateConditional);
  }
}
function loadSample() {
  invalidate();
  for (const [key, control] of state.fields) {
    const data = state.sample.fields[key] || {status: "not_provided", value: null};
    control.status.value = data.status;
    control.input.value = control.spec.allowed_values ?
      (data.status === "known" ? String(control.spec.allowed_values.findIndex((value) => value === data.value)) : "") :
      Array.isArray(data.value) ? data.value.join(", ") : data.value ?? "";
    syncStatus(key);
  }
  updateConditional();
}
function propertyData() {
  const fields = {};
  for (const [key, control] of state.fields) {
    const status = control.status.value;
    let value = null;
    if (["known", "conflicting"].includes(status)) {
      const raw = control.input.value.trim();
      value = control.spec.allowed_values ? control.spec.allowed_values[Number(raw)] : raw;
      if (control.spec.allowed_values && raw === "") value = null;
      if (control.spec.multiple) value = raw.split(",").map((part) => part.trim()).filter(Boolean);
      else if (numericFields.has(key) && /^-?\d+(\.\d+)?$/.test(raw)) value = Number(raw);
    }
    fields[key] = {status, value};
  }
  return {fields};
}
function validResult(result) {
  return result.ok && !(result.structure_errors || []).length && result.parsed;
}
function measuredCost(cost) {
  return cost?.available && cost.total !== null && cost.total !== undefined ?
    String(cost.total) + (cost.currency ? " " + cost.currency : "") : "לא זמינה";
}
function resultMetadata(result, live) {
  if (!live) return "עלות: לא נמדדה — הדגמה · שימוש בטוקנים: לא נמדד";
  const usage = result.usage || {};
  const lines = ["עלות מחושבת: " + measuredCost(result.cost),
    "טוקני קלט: " + (usage.input_tokens ?? "לא זמין") + " · פלט כולל חשיבה: " + (usage.output_tokens ?? "לא זמין")];
  if (!result.cost?.available && result.cost?.reasons?.length) lines.push(...result.cost.reasons);
  if (result.paid_cost_estimate) lines.push("אומדן לפי תעריף בתשלום: " +
    (typeof result.paid_cost_estimate === "object" ? measuredCost(result.paid_cost_estimate) : String(result.paid_cost_estimate)));
  return lines.join("\n");
}
function showRun(run, grading) {
  state.run = run;
  state.saved = false;
  const live = run.mode === "live";
  $("results-badge").textContent = live ? "תשובות מספקים" : "תוצרים מדומים";
  $("results-badge").classList.toggle("live-badge", live);
  $("results-help").textContent = live ?
    "תוצאות ההרצה החיה · עלות כוללת מחושבת: " + measuredCost(run.total_cost) +
    ". הנתונים מבוססים על דיווחי השימוש; אין לראות בהם חשבונית חיוב." :
    "אלה תוצרי הדגמה, ולא תשובות אמיתיות של Google או Anthropic. עלות: לא נמדדה — הדגמה.";
  $("run-reference").textContent = "מזהה ההרצה: " + run.run_id;
  $("run-reference").hidden = false;
  const budget = run.budget_estimate;
  $("budget-summary").hidden = !live || !budget;
  if (live && budget) {
    const lines = ["אומדן לפני ההרצה — אינו חיוב בפועל",
      "אומדן מרבי כולל: " + budget.estimated_max_total + " " + (budget.currency || "USD") +
      " · תקרת אישור: " + budget.approved_max_total + " " + (budget.currency || "USD")];
    for (const estimate of budget.provider_estimates || []) {
      lines.push((estimate.model_id || "ספק") + ": " + measuredCost(estimate) + " · אומדן מרבי");
    }
    lines.push("העלות המחושבת מדיווחי השימוש מופיעה בנפרד בכל חבילה. האומדן אינו מגבלת חיוב אצל הספקים.");
    $("budget-summary").textContent = lines.join("\n");
  }
  if (live) {
    state.liveAttempted = true;
    $("saved-run-id").value = run.run_id;
    remember("ravit-ui-live-run", run.run_id);
  }
  $("packages").replaceChildren();
  $("choice").replaceChildren(option("", "בחירת חבילה"));
  $("note").value = "";
  $("saved-message").hidden = true;
  run.results.forEach((result, index) => {
    const label = labels[index];
    const valid = validResult(result);
    const card = el("article", "package" + (live ? " live-output" : ""));
    const head = el("div", "package-header");
    head.append(el("span", "package-letter", label + "׳"));
    const title = el("div");
    title.append(el("h3", "", "חבילה " + label + "׳"), el("small", "package-model", (result.provider || "ספק") + " · " + (live ? result.model || "מודל לא זמין" : "הדגמה מדומה")));
    head.append(title);
    const body = el("div", "package-body");
    body.append(el("span", "package-status" + (valid ? "" : " failed"), valid ? live ? "מבנה תקין" : "מבנה תקין · תוצר הדגמה" :
      result.ok ? "נדחה בבדיקת המבנה" : "ההפקה נכשלה"));
    if (valid) {
      body.append(el("p", "output-label", "הפוסט"), el("div", "post", result.parsed.post),
        el("p", "output-label", "5 משפטי שיתוף"));
      const hooks = el("ol", "hooks");
      result.parsed.hooks.forEach((text) => hooks.append(el("li", "", text)));
      body.append(hooks);
      $("choice").append(option(label, "חבילה " + label + "׳"));
    } else {
      const errors = result.structure_errors || [];
      body.append(el("p", "package-error", errors.length ? errors.join(" · ") :
        result.error || "לא התקבל תוצר תקין. לא בוצע ניסיון חוזר."));
      body.append(el("p", "muted", "החבילה לא תקבל ציון ואינה ניתנת לבחירה."));
    }
    body.append(el("p", "package-meta", resultMetadata(result, live)));
    const score = el("div", "field score-field");
    const scoreLabel = el("label", "", "הציון שלי לחבילה " + label + "׳");
    scoreLabel.htmlFor = "score-" + label;
    const select = el("select", "score-select");
    select.id = "score-" + label;
    select.dataset.label = label;
    select.setAttribute("form", "grading-form");
    select.append(option("", valid ? "בחירת ציון 1–5" : "ללא ציון"));
    if (valid) {
      select.required = true;
      for (let n = 1; n <= 5; n++) select.append(option(String(n), String(n)));
    }
    select.disabled = !valid;
    select.dataset.gradable = String(!!valid);
    score.append(scoreLabel, select);
    body.append(score);
    card.append(head, body);
    $("packages").append(card);
  });
  $("choice").append(option("אף אחת", "אף אחת"));
  $("results-section").hidden = false;
  $("step-results").classList.add("active");
  $("step-grade").classList.add("active");
  $("compare-button").textContent = live ? "ההרצה החיה נשמרה · אין הפקה נוספת" : "ההשוואה מוכנה · שינוי נתונים יתחיל השוואה חדשה";
  $("save-button").hidden = false;
  if (grading) showSaved(grading);
}
function restoreScoreLocks() {
  document.querySelectorAll(".score-select").forEach((node) => {
    node.disabled = state.busy || state.saved || node.dataset.gradable !== "true";
  });
}
function showSaved(grading) {
  state.saved = true;
  document.querySelectorAll(".score-select").forEach((node) => { node.value = grading.scores?.[node.dataset.label] ?? ""; });
  $("choice").value = grading.choice;
  $("note").value = grading.note || "";
  $("grading-fields").disabled = true;
  $("save-button").disabled = true;
  $("save-button").hidden = true;
  restoreScoreLocks();
  $("saved-message").textContent = "הדירוג והבחירה נשמרו. הדירוג מוצג כעת לקריאה בלבד.\nהבחירה אינה אישור לפרסום.";
  $("saved-message").hidden = false;
}
$("property-form").addEventListener("input", () => { if (state.template && !state.busy) invalidate(); });
$("property-form").addEventListener("change", () => { if (state.template && !state.busy) invalidate(); });
$("load-sample").addEventListener("click", () => {
  if (!state.busy && !liveMode()) { loadSample(); notice("נטענה דוגמה בדיונית. אפשר לעדכן את הנתונים לפני ההפעלה."); }
});
$("property-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy || state.run || !state.template) return;
  if (liveMode() && (!$("live-confirmed").checked || state.liveAttempted)) return;
  state.requestId ||= newId();
  if (liveMode()) {
    state.liveAttempted = true;
    state.liveRequestId = state.requestId;
    remember("ravit-ui-live-attempt", state.requestId);
  }
  notice();
  setBusy(true);
  $("waiting").hidden = false;
  try {
    const payload = {property: liveMode() ? structuredClone(state.sample) : propertyData(),
      request_id: state.requestId, mode: state.mode};
    if (liveMode()) payload.live_confirmed = true;
    const result = await api("/api/compare", payload);
    showRun(result.run, result.grading);
    $("save-button").hidden = state.saved;
    $("results-title").focus();
    $("results-section").scrollIntoView({behavior: "auto", block: "start"});
    notice(liveMode() ? "ההרצה החיה הסתיימה. בדקו את מצב שתי החבילות לפני הדירוג." :
      "שתי חבילות ההדגמה מוכנות. קראו ובדקו את התוכן לפני הדירוג.");
  } catch (error) {
    if (liveMode()) {
      notice("ייתכן שהבקשה כבר נשלחה לספקים. אין להפעיל הרצה חדשה; יש לבדוק את ההרצה השמורה.",
        ["מזהה הפעולה שנשמר: " + state.requestId, error instanceof TypeError ? "החיבור המקומי נותק." : error.message], true);
      updateModeText();
    } else report(error);
  }
  finally { $("waiting").hidden = true; setBusy(false); restoreScoreLocks(); }
});
$("grading-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy || state.saved || !state.run) return;
  const scores = {};
  let missing = false;
  document.querySelectorAll(".score-select").forEach((node) => {
    if (node.dataset.gradable === "true") {
      if (!node.value) missing = true;
      else scores[node.dataset.label] = Number(node.value);
    }
  });
  if (missing || !$("choice").value) {
    notice("יש לבחור ציון לכל חבילה תקינה ובחירה מסכמת.", [], true);
    return;
  }
  setBusy(true);
  notice();
  try {
    const result = await api("/api/grade", {run_id: state.run.run_id, mode: state.run.mode || "demo", scores, choice: $("choice").value, note: $("note").value});
    showSaved(result.grading);
    notice(result.already_saved ? "הדירוג הזה כבר נשמר. לא נוצרה רשומה נוספת." : "הדירוג והבחירה נשמרו בהצלחה.");
  } catch (error) { report(error); }
  finally { setBusy(false); restoreScoreLocks(); }
});
$("mode").addEventListener("change", () => {
  if (state.busy) return;
  state.mode = $("mode").value === "live" && state.modes.live?.available ? "live" : "demo";
  $("mode").value = state.mode;
  $("live-confirmed").checked = false;
  loadSample();
  updateModeText();
  setBusy(false);
});
$("live-confirmed").addEventListener("change", refreshCompare);
async function openSaved(reference, byRequest = false) {
  if (state.busy || !state.modes.live?.available) return;
  const valid = byRequest ? /^[A-Za-z0-9_-]{1,100}$/.test(reference) :
    /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(reference);
  if (!valid) {
    notice("יש להזין מזהה תקין של הרצה חיה שמורה.", [], true);
    return;
  }
  setBusy(true);
  notice();
  try {
    const data = await api("/api/run?mode=live&" + (byRequest ? "request_id=" : "run_id=") + encodeURIComponent(reference));
    if (data.run.mode !== "live") throw new Error("ההרצה אינה הרצה חיה.");
    showRun(data.run, data.grading);
    notice("ההרצה השמורה נפתחה. לא בוצעה קריאה נוספת לספקים.");
    $("results-title").focus();
  } catch (error) { report(error); }
  finally { setBusy(false); restoreScoreLocks(); }
}
$("load-saved").addEventListener("click", () => openSaved($("saved-run-id").value.trim()));
$("recover-live").addEventListener("click", () => {
  if (state.liveRequestId) openSaved(state.liveRequestId, true);
});
async function init() {
  try {
    const data = await api("/api/schema");
    if (data.mode !== "demo") throw new Error("המסך זמין במצב הדגמה בלבד.");
    state.modes = data.modes || {};
    state.liveRequestId = recalled("ravit-ui-live-attempt");
    state.liveAttempted = !!state.liveRequestId;
    $("live-option").disabled = !state.modes.live?.available;
    $("saved-run-id").value = recalled("ravit-ui-live-run") || "";
    state.template = data.template;
    state.sample = data.sample;
    buildFields(data.template);
    loadSample();
    setBusy(false);
    updateModeText();
    const query = new URLSearchParams(location.search);
    if (query.get("mode") === "live" && (query.has("run_id") || query.has("request_id"))) {
      if (!state.modes.live?.available) throw new Error("מצב חי אינו פתוח בשרת המקומי.");
      state.mode = "live";
      $("mode").value = "live";
      updateModeText();
      await openSaved(query.get("run_id") || query.get("request_id"), !query.has("run_id"));
    }
  } catch (error) { report(error); }
}
init();

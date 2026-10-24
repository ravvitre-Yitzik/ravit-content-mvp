"use strict";
const $ = (id) => document.getElementById(id);
const state = {template: null, sample: null, fields: new Map(), requestId: null, run: null, busy: false, saved: false};
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
function report(error) {
  notice(error instanceof TypeError ? "לא ניתן להתחבר למסך המקומי. ודאו שהשרת פועל ונסו שוב." : error.message,
    error.details || [], true);
}
function newId() {
  return crypto.randomUUID();
}
function setBusy(busy) {
  state.busy = busy;
  $("property-fields").disabled = busy;
  $("load-sample").disabled = busy;
  $("compare-button").disabled = busy || !!state.run;
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
  $("compare-button").disabled = false;
  $("compare-button").textContent = "הפעלת השוואת הדגמה ←";
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
function showRun(run, grading) {
  state.run = run;
  state.saved = false;
  $("packages").replaceChildren();
  $("choice").replaceChildren(option("", "בחירת חבילה"));
  $("note").value = "";
  $("saved-message").hidden = true;
  run.results.forEach((result, index) => {
    const label = labels[index];
    const valid = validResult(result);
    const card = el("article", "package");
    const head = el("div", "package-header");
    head.append(el("span", "package-letter", label + "׳"));
    const title = el("div");
    title.append(el("h3", "", "חבילה " + label + "׳"), el("small", "", (result.provider || "ספק") + " · הדגמה מדומה"));
    head.append(title);
    const body = el("div", "package-body");
    body.append(el("span", "package-status" + (valid ? "" : " failed"), valid ? "מבנה תקין · תוצר הדגמה" :
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
      body.append(el("p", "", errors.length ? errors.join(" · ") : "לא התקבל תוצר תקין. לא בוצע ניסיון חוזר."));
      body.append(el("p", "muted", "החבילה לא תקבל ציון ואינה ניתנת לבחירה."));
    }
    body.append(el("p", "package-meta", "עלות: לא נמדדה — הדגמה · שימוש בטוקנים: לא נמדד"));
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
  $("compare-button").textContent = "ההשוואה מוכנה · שינוי נתונים יתחיל השוואה חדשה";
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
  if (!state.busy) { loadSample(); notice("נטענה דוגמה בדיונית. אפשר לעדכן את הנתונים לפני ההפעלה."); }
});
$("property-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy || state.run || !state.template) return;
  state.requestId ||= newId();
  notice();
  setBusy(true);
  $("waiting").hidden = false;
  try {
    const result = await api("/api/compare", {property: propertyData(), request_id: state.requestId});
    showRun(result.run, result.grading);
    $("save-button").hidden = state.saved;
    $("results-title").focus();
    $("results-section").scrollIntoView({behavior: "auto", block: "start"});
    notice("שתי חבילות ההדגמה מוכנות. קראו ובדקו את התוכן לפני הדירוג.");
  } catch (error) { report(error); }
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
    const result = await api("/api/grade", {run_id: state.run.run_id, scores, choice: $("choice").value, note: $("note").value});
    showSaved(result.grading);
    notice(result.already_saved ? "הדירוג הזה כבר נשמר. לא נוצרה רשומה נוספת." : "הדירוג והבחירה נשמרו בהצלחה.");
  } catch (error) { report(error); }
  finally { setBusy(false); restoreScoreLocks(); }
});
async function init() {
  try {
    const data = await api("/api/schema");
    if (data.mode !== "demo") throw new Error("המסך זמין במצב הדגמה בלבד.");
    state.template = data.template;
    state.sample = data.sample;
    buildFields(data.template);
    loadSample();
    setBusy(false);
  } catch (error) { report(error); }
}
init();

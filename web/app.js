"use strict";
const $ = (id) => document.getElementById(id);
const state = {template: null, sample: null, fields: new Map(), modes: {}, mode: "demo", view: "input",
  requestId: null, workRequestId: null, liveRequestId: null, liveAttempted: false,
  reviewProperty: null, reviewBudget: null, reviewMode: null, attempted: false, uncertain: false,
  run: null, saved: false, busy: false, dirty: false, gradeDirty: false, workStarted: false, source: "sample"};
const labels = ["א", "ב"];
const statusLabels = {known: "ערך נמסר", not_provided: "לא נמסר", confirmed_unknown: "ללא נתון — איני יודע/ת", conflicting: "נתונים סותרים — נדרש תיקון"};
const numericFields = new Set(["floor", "area_sqm", "rooms", "price", "total_floors", "balcony_area_sqm", "garden_area_sqm"]);
const featureFields = new Set(["balcony", "garden", "storage", "safe_room", "building_shelter", "elevator", "parking"]);
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function option(value, text) { const node = el("option", "", text); node.value = value; return node; }
function remember(key, value) { try { sessionStorage.setItem(key, value); } catch { /* Server idempotency is independent. */ } }
function recalled(key) { try { return sessionStorage.getItem(key); } catch { return null; } }
function notice(message = "", details = [], error = false) {
  const node = $("notice"); node.replaceChildren(); node.classList.toggle("error", error);
  node.setAttribute("role", error ? "alert" : "status");
  if (message) node.append(el("strong", "", message));
  if (details.length) {
    const list = el("ul");
    for (const detail of details) {
      const item = el("li");
      if (detail && typeof detail === "object" && detail.key) {
        const button = el("button", "field-link", detail.text);
        button.type = "button";
        button.addEventListener("click", () => focusField(detail.key));
        item.append(button);
      } else item.textContent = String(detail);
      list.append(item);
    }
    node.append(list);
  }
  if (error) node.focus();
}
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {cache:"no-store"} : {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)
  });
  let result;
  try { result = await response.json(); } catch { throw new Error("לא התקבלה תשובה תקינה מהשרת המקומי."); }
  if (!response.ok) {
    const error = new Error(result.error || "הפעולה לא הושלמה.");
    error.status = response.status; error.code = result.code;
    error.details = [...(result.completion_requests || []).map((text) => "יש להשלים: " + text), ...(result.problems || [])];
    throw error;
  }
  return result;
}
function report(error) {
  notice(error instanceof TypeError ? "החיבור המקומי אינו זמין. ודאו שהיישום פועל." : error.message, error.details || [], true);
}
function paidMode() { return state.mode === "work" || state.mode === "live"; }
function canReadMode(mode) { return mode === "work" ? !!state.modes.work?.available : mode === "live" && !!(state.modes.live?.read_available || state.modes.live?.available); }
function activateView(view, focus = true) {
  state.view = view;
  const mapping = {input:"input-view",review:"review-view",results:"results-section",history:"history-view"};
  for (const [key,id] of Object.entries(mapping)) $(id).hidden = key !== view;
  for (const key of ["input","results","history"]) {
    const active = key === view || key === "input" && view === "review";
    $("nav-" + key).classList.toggle("active", active);
    if (active) $("nav-" + key).setAttribute("aria-current","page");
    else $("nav-" + key).removeAttribute("aria-current");
  }
  $("nav-results").disabled = !state.run || state.busy;
  if (focus) $(view === "input" ? "input-title" : view === "review" ? "review-title" : view === "results" ? "results-title" : "history-title").focus();
}
function updateModeText() {
  const work = state.mode === "work", legacy = state.mode === "live";
  $("mode").value = state.mode;
  $("mode-badge").textContent = work ? "מצב עבודה · Google ו-Anthropic" : legacy ? "בדיקה חיה קודמת · תוצאה שמורה" : "מצב הדגמה — ללא פנייה לספקים";
  $("mode-badge").classList.toggle("live-badge", work || legacy);
  $("mode-help").textContent = work ? "הנתונים ניתנים לעריכה. שליחה דורשת אישור נפרד בכל השוואה." :
    legacy ? "צפייה בהרצה קיימת בלבד, ללא הפקה נוספת." :
    state.modes.work?.available ? "אפשר לתרגל כאן או לבחור מצב עבודה אמיתי." : "הדגמה היא ברירת המחדל. מצב עבודה אינו פתוח בשרת הזה.";
  $("action-title").textContent = work ? "שתי הצעות כתיבה, באישור שלך" : "מרחב לתרגול, בלי קריאה לספקים";
  $("action-help").textContent = work ? "עד $0.50 לכל השוואה. תחילה בודקים את הנתונים ואת האומדן, ואז מאשרים שליחה." :
    "אותו תהליך עבודה, עם תוצרים מדומים וללא עלות הפקה.";
  $("input-context").textContent = state.source === "sample" ? "נטענה דוגמה בדיונית במפורש. אם עובדים על נכס אמיתי, יש לנקות את הטופס לפני הזנתו." :
    state.source === "saved" ? "הנתונים הועתקו מההרצה שנפתחה. עריכתם תיצור השוואה נפרדת." :
    work ? "מתחילים ללא נתונים. מוסרים רק מידע שיש הרשאה להעביר לספקים; לא ידוע נשאר לא ידוע." :
    "הדגמה משתמשת בספקים מדומים. אפשר לטעון דוגמה בדיונית או להזין פרטים.";
  $("input-context").classList.toggle("sample-context",state.source === "sample");
}
function updateRecovery() {
  const work = canReadMode("work") && !!state.workRequestId;
  const live = canReadMode("live") && !!state.liveRequestId;
  $("recovery-panel").hidden = !work && !live;
  $("recover-work").hidden = !work; $("recover-live").hidden = !live;
  $("recover-work").disabled = state.busy; $("recover-live").disabled = state.busy;
  $("recovery-help").textContent = state.uncertain ?
    "מצב ניסיון העבודה האחרון אינו ודאי. שחזרו את התוצאה לפני כל החלטה על השוואה חדשה. שחזור אינו פונה לספקים." :
    "שחזור פותח את הניסיון השמור בלבד. הוא אינו פונה לספקים.";
}
function setBusy(busy) {
  state.busy = busy;
  const locked = state.mode === "live" || state.mode === "work" && state.uncertain;
  $("property-fields").disabled = busy || locked;
  $("load-sample").disabled = busy || locked; $("clear-input").disabled = busy || locked;
  $("mode").disabled = busy;
  $("compare-button").disabled = busy || locked || !state.template;
  $("send-comparison").disabled = busy || state.attempted || state.mode === "live" ||
    state.mode === "work" && !$("work-consent").checked;
  $("work-consent").disabled = busy || state.attempted;
  $("load-saved").disabled = busy; $("refresh-history").disabled = busy;
  $("new-comparison").disabled = busy; $("back-to-input").disabled = busy;
  $("nav-input").disabled = busy; $("nav-results").disabled = busy || !state.run;
  $("nav-history").disabled = busy || !state.modes.work?.available;
  $("grading-fields").disabled = busy || state.saved;
  $("save-button").disabled = busy || state.saved;
  restoreScoreLocks(); updateRecovery();
  $("property-form").setAttribute("aria-busy", String(busy));
  $("grading-form").setAttribute("aria-busy", String(busy));
}
function invalidateDraft() {
  state.reviewProperty = null; state.reviewBudget = null; state.reviewMode = null;
  $("work-consent").checked = false;
  $("step-review").classList.remove("active");
  if (!state.run) {
    $("step-results").classList.remove("active");
    $("step-grade").classList.remove("active");
    $("step-input").classList.add("active");
  }
  state.dirty = true;
  notice();
}
function normalizeNumberText(value) {
  const text = value.trim();
  if (/^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(text)) return text.replaceAll(",", "");
  if (/^-?\d{1,3}( \d{3})+(\.\d+)?$/.test(text)) return text.replaceAll(" ", "");
  return text;
}
function populateProperty(prop) {
  for (const [key, control] of state.fields) {
    const data = prop?.fields?.[key] || {status:"not_provided",value:null};
    control.status.value = data.status;
    control.input.value = control.spec.allowed_values ?
      data.status === "known" ? String(control.spec.allowed_values.findIndex((value) => value === data.value)) : "" :
      Array.isArray(data.value) ? data.value.join(", ") : data.value ?? "";
    syncStatus(key);
  }
  updateConditional();
}
function focusField(key) {
  activateView("input", false);
  if (!state.template.fields[key].required && !state.template.fields[key].required_if) $("optional-details").open = true;
  const field = state.fields.get(key);
  (field.input.disabled ? field.status : field.input).focus();
}
function clientIssues(prop) {
  const issues = [];
  for (const [key,spec] of Object.entries(state.template.fields)) {
    const field = prop.fields[key], status = field.status, value = field.value;
    const conditional = spec.required_if && Object.entries(spec.required_if).every(([name,expected]) =>
      prop.fields[name].status === "known" && prop.fields[name].value === expected);
    let problem = "";
    if (status === "conflicting") problem = "יש לתקן את הנתונים הסותרים";
    else if (status === "not_provided" && (spec.required || conditional)) problem = "יש למסור ערך או לאשר שאין נתון";
    else if (status === "confirmed_unknown" && spec.must_be_known) problem = "נדרש ערך ידוע";
    else if (status === "known" && (value === null || value === "" || Array.isArray(value) && !value.length)) problem = "סומן ערך ידוע, אך הערך ריק";
    else if (status === "known" && ["price","area_sqm","rooms"].includes(key) &&
      (typeof value !== "number" || !Number.isFinite(value) || value <= 0)) problem = "יש להזין מספר חיובי; אפשר להשתמש בפסיקים להפרדת אלפים";
    state.fields.get(key).input.setAttribute("aria-invalid", String(!!problem));
    state.fields.get(key).status.setAttribute("aria-invalid", String(!!problem));
    if (problem) issues.push({key,text:spec.label + ": " + problem});
  }
  return issues;
}
function fieldText(field) {
  if (!field || field.status === "not_provided") return "לא נמסר";
  if (field.status === "confirmed_unknown") return "ללא נתון";
  if (field.status === "conflicting") return "נתונים סותרים";
  const value = field.value;
  return value === true ? "כן" : value === false ? "לא" : Array.isArray(value) ? value.join(", ") : String(value ?? "");
}
function renderFacts(node, prop) {
  node.replaceChildren();
  for (const [key,spec] of Object.entries(state.template.fields)) {
    const row = el("div", "fact" + (spec.publishable === false ? " internal" : ""));
    row.append(el("dt","",spec.label + (spec.publishable === false ? " · פנימי" : "")),el("dd","",fieldText(prop?.fields?.[key])));
    node.append(row);
  }
}
function renderReview(prop, budget) {
  state.reviewProperty = structuredClone(prop); state.reviewBudget = budget; state.reviewMode = state.mode;
  $("review-overview").replaceChildren();
  const values = [["הנכס",["street","city"].map(key=>fieldText(prop.fields[key])).join(", ")],
    ["סוג העסקה",fieldText(prop.fields.deal_type)],
    [state.mode === "work" ? "אומדן לפני שליחה" : "מצב ההפקה",state.mode === "work" ?
      budget.estimated_max_total + " " + budget.currency + " · עד $0.50 להשוואה" : "שתי חבילות מדומות"]];
  for (const [label,value] of values) { const card=el("div","review-stat");card.append(el("small","",label),el("strong","",value));$("review-overview").append(card); }
  renderFacts($("review-facts"),prop);
  $("work-consent-panel").hidden = state.mode !== "work"; $("demo-review").hidden = state.mode !== "demo";
  $("work-consent").checked = false;
  $("send-comparison").textContent = state.mode === "work" ? "שליחה לשני הספקים · עד $0.50" : "הפקת טיוטות הדגמה";
  $("step-review").classList.add("active");
  activateView("review"); setBusy(false);
}
function confirmReplacement() {
  if (state.gradeDirty && !state.saved) return window.confirm("יש דירוג שטרם נשמר. מעבר להרצה אחרת יחליף אותו. להמשיך?");
  if (state.dirty) return window.confirm("יש נתונים שעריכתך בהם לא נשמרה. פתיחת הרצה אחרת תחליף את הטיוטה. להמשיך?");
  return true;
}
function startNewDraft(keepData = false) {
  if (state.busy) return;
  if (!keepData && state.dirty && !window.confirm("בטופס יש שינויים שטרם נשמרו. לפתוח השוואה חדשה מטופס ריק?")) return;
  if (state.gradeDirty && !state.saved && !window.confirm("הדירוג הנוכחי טרם נשמר ויאבד. להתחיל השוואה חדשה?")) return;
  if (state.uncertain && !window.confirm("מצב הניסיון הקודם אינו ודאי וייתכן שכבר חויב. השוואה חדשה תהיה פעולה נפרדת בתשלום. להמשיך?")) return;
  state.mode = state.mode === "live" ? state.modes.work?.available ? "work" : "demo" : state.mode;
  state.run = null; state.saved = false; state.gradeDirty = false; state.requestId = null; state.attempted = false; state.uncertain = false;
  if (!keepData) {populateProperty({fields:{}});state.source="empty";}
  else state.source="saved";
  invalidateDraft(); state.dirty = false;
  updateModeText(); setBusy(false); activateView("input");
  notice(keepData ? "נתוני ההרצה הועתקו לטיוטה חדשה. ההרצה והדירוג הקודמים לא ישתנו." : "נפתחה השוואה חדשה מטופס ריק. ההרצות הקודמות לא ישתנו.");
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
    const target = key === "ravit_impression" ? "impression-fields" :
      featureFields.has(key) ? "feature-fields" : spec.required || spec.required_if ? "required-fields" : "optional-fields";
    wrapper.dataset.fieldKey = key;
    $(target).append(wrapper);
    state.fields.set(key, {spec, status, input, hint});
    status.addEventListener("change", () => syncStatus(key));
    if (key === "safe_room") input.addEventListener("change", updateConditional);
  }
}
function propertyData() {
  const fields = {};
  for (const [key, control] of state.fields) {
    const status = control.status.value;
    let value = null;
    if (["known", "conflicting"].includes(status)) {
      const raw = numericFields.has(key) ? normalizeNumberText(control.input.value) : control.input.value.trim();
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
  const live = run.mode !== "demo";
  state.mode = run.mode || "demo";
  state.attempted = true;
  state.uncertain = false;
  state.dirty = false;
  state.gradeDirty = false;
  if (run.property && typeof run.property === "object") { populateProperty(run.property); state.source="saved"; }
  renderFacts($("snapshot-facts"), run.property && typeof run.property === "object" ? run.property : state.reviewProperty || propertyData());
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
    $("saved-run-id").value = run.run_id;
    $("saved-mode").value = run.mode;
    if (run.mode === "work") {
      state.workRequestId = run.request_id || state.workRequestId;
      remember("ravit-ui-work-run", run.run_id);
      if (state.workRequestId) remember("ravit-ui-work-completed-request", state.workRequestId);
    } else {
      state.liveAttempted = true;
      remember("ravit-ui-live-run", run.run_id);
    }
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
  $("compare-button").textContent = "בדיקה לפני הפקה ←";
  $("save-button").hidden = false;
  if (grading) showSaved(grading);
  updateModeText();
  updateRecovery();
  activateView("results");
}
function restoreScoreLocks() {
  document.querySelectorAll(".score-select").forEach((node) => {
    node.disabled = state.busy || state.saved || node.dataset.gradable !== "true";
  });
}
function showSaved(grading) {
  state.saved = true;
  state.gradeDirty = false;
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

$("property-form").addEventListener("input", () => { if (!state.busy) invalidateDraft(); });
$("property-form").addEventListener("change", () => { if (!state.busy) invalidateDraft(); });
$("load-sample").addEventListener("click", () => {
  if (state.busy || !confirmReplacement()) return;
  populateProperty(state.sample); state.source="sample"; invalidateDraft(); updateModeText(); notice("נטענה דוגמה בדיונית. בדקו את הפרטים לפני ההפקה.");
});
$("clear-input").addEventListener("click", () => {
  if (state.busy || !confirmReplacement()) return;
  populateProperty({fields:{}}); state.source="empty"; invalidateDraft(); updateModeText(); notice("הטופס נוקה. שדות החובה נותרו גלויים להשלמה.");
});
$("property-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy || state.mode === "live" || state.uncertain && state.mode === "work") return;
  const prop = propertyData(), issues = clientIssues(prop);
  if (issues.length) { notice("יש להשלים או לתקן את הפרטים לפני ההפקה.", issues, true); focusField(issues[0].key); return; }
  notice(); setBusy(true);
  try {
    if (state.mode === "work") {
      const preview = await api("/api/preview",{mode:"work",property:prop});
      populateProperty(preview.property);
      renderReview(preview.property,preview.budget_estimate);
    } else renderReview(prop,null);
  } catch (error) {
    report(error);
    if (error.details?.length) {
      const target = [...state.fields].find(([,control]) => error.details.some(text=>text.includes(control.spec.label)));
      if (target) focusField(target[0]);
    }
  } finally { setBusy(false); }
});
$("back-to-input").addEventListener("click", () => {
  if (state.busy) return;
  invalidateDraft(); activateView("input");
});
$("work-consent").addEventListener("change", () => setBusy(false));
$("send-comparison").addEventListener("click", async () => {
  if (state.busy || state.attempted || !state.reviewProperty || state.reviewMode !== state.mode || state.mode === "live") return;
  if (state.mode === "work" && !$("work-consent").checked) return;
  state.requestId ||= crypto.randomUUID();
  state.attempted = true;
  if (state.mode === "work") {
    state.workRequestId = state.requestId; remember("ravit-ui-work-attempt",state.requestId);
  }
  const payload = {mode:state.mode,property:structuredClone(state.reviewProperty),request_id:state.requestId};
  if (state.mode === "work") payload.work_consent = true;
  notice(); setBusy(true); $("waiting").hidden = false;
  $("waiting-title").textContent = state.mode === "work" ? "ממתינים לתשובות Google ו-Anthropic…" : "מכינים שתי חבילות הדגמה…";
  $("waiting-help").textContent = state.mode === "work" ? "קריאה אחת לכל ספק, ללא ניסיון חוזר. אפשר לשחזר תוצאה שמורה אם החיבור נקטע." : "לא מתבצעת פנייה לספקים.";
  try {
    const data = await api("/api/compare",payload);
    showRun(data.run,data.grading);
    notice("ההשוואה נשמרה. קראו ובדקו את התוכן לפני הדירוג.");
  } catch (error) {
    if (paidMode()) {
      state.uncertain = true;
      notice("ייתכן שהבקשה כבר נשלחה. אין להפיק שוב את אותו ניסיון; השתמשו בשחזור התוצאה.",
        [error.message,"מזהה הפעולה השמור: " + state.requestId],true);
    } else {
      state.attempted = false;
      report(error);
    }
  } finally { $("waiting").hidden = true; setBusy(false); }
});
$("grading-form").addEventListener("input", () => { if (!state.saved) state.gradeDirty = true; });
$("grading-form").addEventListener("change", () => { if (!state.saved) state.gradeDirty = true; });
$("packages").addEventListener("change", () => { if (!state.saved) state.gradeDirty = true; });
$("grading-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (state.busy || state.saved || !state.run) return;
  const scores = {}; let missing = false;
  document.querySelectorAll(".score-select").forEach(node=>{
    if (node.dataset.gradable === "true") { if (!node.value) missing=true;else scores[node.dataset.label]=Number(node.value); }
  });
  if (missing || !$("choice").value) { notice("יש לבחור ציון לכל חבילה תקינה ובחירה מסכמת.",[],true);return; }
  setBusy(true); notice();
  try {
    const data = await api("/api/grade",{mode:state.run.mode || "demo",run_id:state.run.run_id,scores,choice:$("choice").value,note:$("note").value});
    showSaved(data.grading); notice(data.already_saved ? "הדירוג כבר נשמר. לא נוצרה רשומה נוספת." : "הדירוג והבחירה נשמרו בהצלחה.");
  } catch(error) { report(error); } finally { setBusy(false); }
});
$("new-comparison").addEventListener("click",()=>startNewDraft(true));
$("nav-input").addEventListener("click",()=>{
  if (state.run || state.attempted || state.uncertain) startNewDraft();
  else { activateView("input");notice(); }
});
$("nav-results").addEventListener("click",()=>{if(state.run && !state.busy){activateView("results");notice();}});
$("mode").addEventListener("change",()=>{
  if (state.busy) return;
  const nextMode = $("mode").value === "work" && state.modes.work?.available ? "work" : "demo";
  if (nextMode === "work" && !state.workStarted) {
    if (state.dirty && !confirmReplacement()) {$("mode").value=state.mode;return;}
    populateProperty({fields:{}});state.source="empty";state.workStarted=true;
  }
  state.mode = nextMode;
  invalidateDraft(); updateModeText(); setBusy(false);
});
async function openSaved(reference, byRequest = false, mode = state.mode) {
  if (state.busy || !canReadMode(mode)) return;
  const valid = byRequest ? /^[A-Za-z0-9_-]{1,100}$/.test(reference) : /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(reference);
  if (!valid) {notice("יש להזין מזהה תקין של הרצה שמורה.",[],true);return;}
  if (!confirmReplacement()) return;
  setBusy(true);notice();
  try {
    const data = await api("/api/run?mode=" + mode + "&" + (byRequest ? "request_id=" : "run_id=") + encodeURIComponent(reference));
    if (data.run.mode !== mode) throw new Error("מצב ההרצה אינו תואם לבקשה.");
    showRun(data.run,data.grading); notice("התוצאה השמורה נפתחה עם נתוני הקלט המקוריים. לא בוצעה קריאה נוספת לספקים.");
  } catch(error) {report(error);} finally {setBusy(false);}
}
$("recover-work").addEventListener("click",()=>openSaved(state.workRequestId,true,"work"));
$("recover-live").addEventListener("click",()=>openSaved(state.liveRequestId,true,"live"));
$("load-saved").addEventListener("click",()=>openSaved($("saved-run-id").value.trim(),false,$("saved-mode").value));
function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "תאריך לא זמין" : new Intl.DateTimeFormat("he-IL",{dateStyle:"medium",timeStyle:"short"}).format(date);
}
async function loadHistory() {
  if (state.busy || !state.modes.work?.available) return;
  activateView("history"); notice(); setBusy(true);
  try {
    const data = await api("/api/history?mode=work");
    $("history-list").replaceChildren();
    if (!data.runs.length) {
      const empty=el("div","empty-state");empty.append(el("h3","","עוד אין השוואות עבודה שמורות"),el("p","","אחרי ההפקה הראשונה, התוצאות והדירוגים יופיעו כאן."));
      $("history-list").append(empty);
    }
    const names={completed:"שתי חבילות תקינות",partial_failure:"הפקה חלקית",failed:"ההפקה נכשלה",uncertain:"תוצאה לא ודאית · לשחזור"};
    for(const run of data.runs){
      const row=el("article","history-row"), identity=el("div");
      const shortId = String(run.run_id || run.request_id || "").slice(0,8);
      identity.append(el("strong","",dateLabel(run.date)),el("small","","מזהה: " + shortId + " · " + (run.graded ? "דירוג נשמר · לקריאה בלבד" : "טרם נשמר דירוג")));
      const status=el("span","history-status" + (run.status==="uncertain"?" uncertain":""),names[run.status] || "הרצה שמורה");
      const cost=el("div");cost.append(el("strong","","עלות: "+measuredCost(run.total_cost)),el("small","",run.choice ? "בחירה: "+run.choice : "ללא בחירה שמורה"));
      const button=el("button","button secondary",run.status==="uncertain"?"בדיקת הניסיון":"פתיחת ההרצה");button.type="button";
      button.setAttribute("aria-label",button.textContent + " · " + dateLabel(run.date) + " · " + shortId);
      button.addEventListener("click",()=>openSaved(run.status==="uncertain"?run.request_id:run.run_id,run.status==="uncertain","work"));
      row.append(identity,status,cost,button);$("history-list").append(row);
    }
  } catch(error){report(error);}finally{setBusy(false);}
}
$("nav-history").addEventListener("click",loadHistory);
$("refresh-history").addEventListener("click",loadHistory);
async function init() {
  try {
    const data=await api("/api/schema");
    state.template=data.template;state.sample=data.sample;state.modes=data.modes || {};
    state.workRequestId=recalled("ravit-ui-work-attempt");state.liveRequestId=recalled("ravit-ui-live-attempt");
    state.uncertain=!!state.workRequestId && recalled("ravit-ui-work-completed-request")!==state.workRequestId;
    $("work-option").disabled=!state.modes.work?.available;
    $("live-option").disabled=true;
    buildFields(data.template);populateProperty(data.sample);
    updateModeText();setBusy(false);activateView("input",false);
    const query=new URLSearchParams(location.search),mode=query.get("mode");
    if ((mode==="live" || mode==="work") && (query.has("run_id") || query.has("request_id"))) {
      await openSaved(query.get("run_id") || query.get("request_id"),!query.has("run_id"),mode);
    }
  } catch(error){report(error);}
}
init();

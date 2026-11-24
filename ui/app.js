import { enhanceSelect } from "./dropdown.js";

const $ = (id) => document.getElementById(id);
const dialog = $("settings-dialog");
const form = $("settings-form");
const updateDropdown = Object.fromEntries(
  [...document.querySelectorAll("select")].map((select) => [select.id, enhanceSelect(select)]),
);
let showAllDeadlines = false;
const deadlineDateFormatter = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
const labels = {
  success: "À jour", partial: "À vérifier", expired: "Connexion requise",
  running: "En cours", login: "Connexion en cours", error: "À vérifier",
};
const selectedId = new URLSearchParams(window.location.search).get("course") || "";
const catalogueView = window.location.pathname === "/matieres" || window.location.hash === "#catalogue";
const accordionView = catalogueView && !selectedId;
const courseContent = $("course-content");
let expandedCourse = "";
let stateRequest;
let contentFilter = "all";
let model;
let timer;
let refreshTimer;
const dashboardView = !selectedId && !catalogueView;
const courseHistoryKey = "hipposync.course-opens";
const cardSortKey = "hipposync.card-sort";

function browserPreference(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

function saveBrowserPreference(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage may be disabled in private environments.
  }
}

function recordCourseOpen(identifier) {
  if (identifier) saveBrowserPreference(courseHistoryKey, {
    ...browserPreference(courseHistoryKey, {}), [identifier]: Date.now(),
  });
}

if (selectedId) recordCourseOpen(selectedId);
$("card-sort").value = browserPreference(cardSortKey, "modified") === "opened" ? "opened" : "modified";
updateDropdown["card-sort"]();

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function notify(message) {
  $("notification").textContent = message;
  $("notification").hidden = false;
  clearTimeout(timer);
  timer = setTimeout(() => { $("notification").hidden = true; }, 5000);
}

async function post(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Hippocampus-Token": model.token },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === "string" ? data.detail : "La demande a échoué.");
  }
}

async function openFolder(path, course = model.selected?.id ?? null) {
  try {
    await post("/api/open", { path, course });
  } catch (error) {
    notify(error.message);
  }
}

function openButton(text, path) {
  const button = element("button", "link-button", text);
  button.type = "button";
  button.addEventListener("click", () => openFolder(path));
  return button;
}

function fileLink(file, className) {
  const course = model.selected.id;
  const name = file.path.at(-1);
  const link = element("a", className, name);
  const query = new URLSearchParams({ course });
  for (const part of file.path) query.append("path", part);
  link.href = "/api/file?" + query;
  link.target = "_blank";
  link.rel = "noopener";
  if (file.native) {
    link.addEventListener("click", async (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      try {
        await post("/api/open-file", { path: file.path, course });
      } catch (error) {
        notify(error.message);
      }
    });
  }
  return link;
}

function dateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  const time = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" }).format(date);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return `Aujourd’hui, ${time}`;
  today.setDate(today.getDate() + 1);
  if (date.toDateString() === today.toDateString()) return `Demain, ${time}`;
  return `${new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" }).format(date)}, ${time}`;
}

function fileSize(bytes) {
  const fmt = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
  if (bytes >= 1e6) return `${fmt.format(bytes / 1e6)} Mo`;
  if (bytes >= 1e3) return `${fmt.format(bytes / 1e3)} Ko`;
  return `${bytes} o`;
}

function renderDocuments(files) {
  const parent = $("sections");
  const recent = $("sort").value === "recent";
  const documents = recent ? [...files].sort((a, b) => new Date(b.updated) - new Date(a.updated)) : files;
  const grouped = new Map();
  for (const file of documents) {
    const section = recent ? "Dernières mises à jour" : file.path.length > 1 ? file.path[0] : "Documents";
    if (!grouped.has(section)) grouped.set(section, []);
    grouped.get(section).push(file);
  }
  const groups = [];
  for (const [section, files] of grouped) {
    const group = element("section", "document-group");
    const header = element("div", "group-header");
    const title = element("h3", "", section);
    title.append(element("span", "group-count", `(${files.length})`));
    header.append(title);
    if (!recent) header.append(openButton("Ouvrir le dossier ↗", files[0].path.length > 1 ? [section] : []));
    const list = element("ul", "file-list");
    for (const file of files) {
      const name = file.path.at(-1);
      const suffix = name.includes(".") ? name.split(".").at(-1).slice(0, 6).toUpperCase() : "FICH.";
      const row = element("li", "file-row");
      const ext = element("span", "file-type", suffix);
      ext.setAttribute("aria-hidden", "true");
      const info = element("div");
      const filename = fileLink(file, "file-name");
      filename.setAttribute("aria-label", `Ouvrir ${name}`);
      filename.title = `Synchronisé le ${dateLabel(file.updated)}`;
      info.append(filename);
      const location = file.path.slice(recent ? 0 : 1, -1).join(" / ");
      const metadata = recent ? [location, dateLabel(file.updated)].filter(Boolean).join(" · ") : location;
      if (metadata) info.append(element("span", "file-path", metadata));
      const actions = element("div", "file-actions");
      actions.append(element("span", "file-size", fileSize(file.size)));
      if (recent || file.path.length > 2) {
        actions.append(openButton("Dossier ↗", file.path.slice(0, -1)));
      }
      row.append(ext, info, actions);
      list.append(row);
    }
    group.append(header, list);
    groups.push(group);
  }
  parent.replaceChildren(...groups);
}

function deadlineTime(deadline) {
  const months = ["janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
  const match = deadline?.toLowerCase().match(/(\d{1,2})\s+([\p{L}]+)\s+(\d{4})\s*,?\s*(\d{1,2}):(\d{2})/u);
  if (!match) return NaN;
  const [, day, month, year, hour, minute] = match;
  const monthIndex = months.indexOf(month);
  if (monthIndex < 0) return NaN;
  return new Date(+year, monthIndex, +day, +hour, +minute).getTime();
}

function remainingDays(deadline) {
  const difference = deadlineTime(deadline) - Date.now();
  if (!Number.isFinite(difference)) return "";
  const days = difference / 86_400_000;
  if (days <= 0) {
    const overdue = Math.floor(-days);
    return overdue === 0 ? "Échéance dépassée depuis moins d’un jour" : `Échéance dépassée depuis ${overdue} j`;
  }
  return days < 1 ? "Moins d’un jour restant" : `${Math.ceil(days)} jours restants`;
}

function renderLinks(links) {
  const groups = new Map();
  for (const item of links) {
    const section = item.section.join(" / ");
    if (!groups.has(section)) groups.set(section, []);
    groups.get(section).push(item);
  }
  const sections = [];
  for (const [name, items] of groups) {
    const section = element("section", "document-group");
    const list = element("ul", "file-list");
    for (const item of items) {
      const row = element("li");
      const link = element("a", "link-button", item.title + " ↗");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link);
      list.append(row);
    }
    section.append(element("h3", "group-header", name), list);
    sections.push(section);
  }
  $("links").replaceChildren(...sections);
}

function renderAssignments(items) {
  const target = $("assignments");
  const assignments = [];
  for (const assignment of items) {
    const card = element("article", "assignment");
    const heading = element("h3");
    const assignmentLink = element("a", "assignment-title", assignment.title);
    assignmentLink.href = assignment.url;
    assignmentLink.target = "_blank";
    assignmentLink.rel = "noopener";
    heading.append(assignmentLink);
    card.append(heading);
    const status = assignment.status || "À vérifier sur Hippocampus";
    const submitted = assignment.submission_state === "submitted";
    const statusClass = submitted ? "success" : "";
    card.append(element("span", `badge ${statusClass}`, status));
    const details = element("dl", "assignment-info");
    const fields = [["Échéance", assignment.due], [submitted ? "Remise" : "Jours avant remise", submitted
      ? assignment.remaining : remainingDays(assignment.due) || assignment.remaining], ["Modification", assignment.modified], ["Évaluation", assignment.grading]];
    for (const [label, value] of fields) {
      if (!value || /^[-—–]+$/.test(value.trim())) continue;
      const pair = element("div");
      pair.append(element("dt", "", label), element("dd", "", value));
      details.append(pair);
    }
    if (details.children.length) card.append(details);
    if (assignment.error) card.append(element("p", "empty", assignment.error));
    const files = element("ul", "assignment-files");
    for (const file of assignment.files) {
      const item = element("li");
      item.append(element("span", "muted", file.category), fileLink(file, "file-name"),
        openButton("Ouvrir le dossier ↗", file.path.slice(0, -1)));
      files.append(item);
    }
    if (files.children.length) card.append(files);
    if (!assignment.files.length && !assignment.error) card.append(element("p", "empty", "Aucun fichier remis ni retour disponible."));
    const link = element("a", "link-button", "Ouvrir le devoir ↗");
    link.href = assignment.url;
    link.target = "_blank";
    link.rel = "noopener";
    card.append(link);
    assignments.push(card);
  }
  target.replaceChildren(...assignments);
}

function courseUrl(id) {
  return id ? "/matieres?course=" + encodeURIComponent(id) : "/matieres";
}

function searchText(value) {
  return value.normalize("NFD").replace(/\p{M}+/gu, "").toLocaleLowerCase("fr-FR").trim();
}

function matching(query) {
  const words = searchText(query).split(/\s+/);
  return (...fields) => {
    const value = searchText(fields.flat(Infinity).join(" "));
    return words.every((word) => value.includes(word));
  };
}

const contentSections = {
  files: { panel: "documents", render: renderDocuments },
  assignments: { panel: "rendus", render: renderAssignments },
  links: { panel: "course-links", render: renderLinks },
};

function renderContent() {
  const { selected } = model;
  if (!selected) return;
  const query = $("filter").value.trim();
  const matches = matching(query);
  const results = {
    files: selected.files.filter((file) => matches(file.path)),
    assignments: selected.assignments.filter((assignment) => matches(
      assignment.title, assignment.section, assignment.due, assignment.status,
      assignment.files.map((file) => [file.path, file.category]),
    )),
    links: (selected.links || []).filter((item) => matches(item.title, item.section, item.url)),
  };
  let count = 0;
  for (const [key, section] of Object.entries(contentSections)) {
    const items = results[key];
    const visible = (contentFilter === "all" || contentFilter === key) && items.length > 0;
    $(section.panel).hidden = !visible;
    $(key + "-count").textContent = items.length;
    if (visible) {
      section.render(items);
      count += items.length;
    }
  }
  $("content-results").textContent = count + (count === 1 ? " élément" : " éléments");
  $("content-empty").hidden = count > 0;
  $("content-empty").textContent = query ? "Aucun contenu ne correspond à ta recherche." : "Aucun contenu disponible dans cette vue.";
  $("course-panels").hidden = count === 0;
}

function renderCourses() {
  const input = $("course-filter").value.trim();
  const matches = matching(input);
  const focusedCourse = document.activeElement?.dataset.courseToggle;
  if (accordionView && courseContent.parentElement !== $("apercu")) $("apercu").append(courseContent);
  const nodes = [];
  for (const course of model.courses) {
    if (!matches(course.title)) continue;
    const row = element("li", "course-list-row");
    const card = element("button", "course-tile");
    card.type = "button";
    card.dataset.courseToggle = course.id;
    card.setAttribute("aria-expanded", "false");
    card.setAttribute("aria-controls", `course-expand-${course.id}`);
    card.addEventListener("click", () => setExpandedCourse(expandedCourse === course.id ? "" : course.id));
    const heading = element("span", "course-name", course.title);
    const indicator = element("span", "course-arrow");
    indicator.setAttribute("aria-hidden", "true");
    const info = element("span", "muted course-summary");
    if (course.status) {
      const documents = course.documents + (course.documents === 1 ? " document" : " documents");
      const assignments = course.assignments + (course.assignments === 1 ? " devoir" : " devoirs");
      const links = course.links_count + (course.links_count === 1 ? " lien" : " liens");
      info.textContent = [documents, assignments, links].join(" · ");
    } else {
      info.textContent = "À synchroniser";
    }
    card.append(heading, indicator, info);
    if (["partial", "error"].includes(course.status)) card.append(element("span", "badge partial", "À vérifier"));
    const contents = element("div", "course-expansion");
    contents.id = `course-expand-${course.id}`;
    contents.hidden = true;
    const loading = element("p", "empty course-loading", "Chargement de la matière…");
    loading.setAttribute("role", "status");
    contents.append(loading);
    row.append(card, contents);
    nodes.push(row);
  }
  $("course-results").textContent = nodes.length;
  if (!nodes.length) nodes.push(element("li", "empty", input
    ? "Aucune matière ne correspond."
    : "Lance la synchronisation pour afficher tes matières Hippocampus."));
  $("course-grid").replaceChildren(...nodes);
  updateCourseExpansion();
  if (focusedCourse) {
    const trigger = [...document.querySelectorAll("[data-course-toggle]")].find((button) => button.dataset.courseToggle === focusedCourse);
    trigger?.focus({ preventScroll: true });
  }
}

function updateCourseExpansion() {
  if (!accordionView) return;
  const ready = Boolean(expandedCourse) && model?.selected?.id === expandedCourse;
  let container = null;
  for (const button of document.querySelectorAll("[data-course-toggle]")) {
    const open = button.dataset.courseToggle === expandedCourse;
    button.setAttribute("aria-expanded", String(open));
    const region = $(button.getAttribute("aria-controls"));
    region.hidden = !open;
    if (open) container = region;
  }

  courseContent.hidden = !(container && ready);
  if (container && ready) {
    container.querySelector(".course-loading").hidden = true;
    if (courseContent.parentElement !== container) container.append(courseContent);
  } else {
    if (container) container.querySelector(".course-loading").hidden = false;
    if (courseContent.parentElement !== $("apercu")) $("apercu").append(courseContent);
  }
}

function setExpandedCourse(identifier) {
  if (identifier === expandedCourse) return;
  expandedCourse = identifier;
  recordCourseOpen(identifier);
  $("filter").value = "";
  contentFilter = "all";
  for (const button of document.querySelectorAll("[data-content-filter]")) {
    button.setAttribute("aria-pressed", String(button.dataset.contentFilter === contentFilter));
  }
  $("course-picker").value = identifier;
  updateDropdown["course-picker"]();
  render.signature = null;
  updateCourseExpansion();
  refresh();
}

function renderDashboardCourses() {
  const sort = $("card-sort").value;
  const opened = browserPreference(courseHistoryKey, {});
  const recentTime = (course) => sort === "opened" ? opened[course.id] || 0 : Date.parse(course.modified) || 0;
  const ordered = [...model.courses].sort((a, b) => recentTime(b) - recentTime(a) ||
    a.title.localeCompare(b.title, "fr"));
  const signature = JSON.stringify([sort, ordered, opened]);
  if (renderDashboardCourses.signature === signature) return;
  renderDashboardCourses.signature = signature;
  $("dashboard-course-count").textContent = ordered.length;
  const cards = ordered.map((course) => {
    const row = element("li");
    const link = element("a", "course-tile dashboard-course-card");
    link.href = courseUrl(course.id);
    const arrow = element("span", "course-arrow", "↗");
    arrow.setAttribute("aria-hidden", "true");
    const counts = element("p", "muted", `${course.documents} document(s) · ${course.assignments} rendu(s) · ${course.links_count} lien(s)`);
    const time = recentTime(course);
    const activity = time ? dateLabel(time) : sort === "opened" ? "Jamais ouverte ici" : "Aucun fichier local";
    link.append(element("h3", "", course.title), arrow, counts, element("p", "course-activity", activity));
    row.append(link);
    return row;
  });
  $("dashboard-course-grid").replaceChildren(...(cards.length ? cards : [element("li", "empty", "Lance une synchronisation pour afficher tes matières.")]));
}

function renderAgenda() {
  const now = Date.now();
  const filter = $("deadline-filter").value;
  const statusLabels = { upcoming: "À venir", late: "En retard", submitted: "Remis",
    dated: "À vérifier", offline: "Sans dépôt en ligne" };
  const deadlines = (model.deadlines || []).map((item) => {
    const time = deadlineTime(item.due);
    let status = item.submission_state;
    if (status === "pending" || status === "draft") {
      status = Number.isFinite(time) ? time >= now ? "upcoming" : "late" : "dated";
    } else if (status !== "submitted" && status !== "offline") {
      status = "dated";
    }
    return { ...item, time, status };
  });
  $("upcoming-count").textContent = deadlines.filter((item) => item.status === "upcoming").length;
  const statusOrder = { upcoming: 0, late: 1, submitted: 2, dated: 3, offline: 4 };
  const filtered = deadlines.filter((item) => filter === "all" || item.status === filter);
  filtered.sort((a, b) => statusOrder[a.status] - statusOrder[b.status] ||
    (a.status === "upcoming" ? a.time - b.time : b.time - a.time));
  const maximum = 5;
  const visible = showAllDeadlines ? filtered : filtered.slice(0, maximum);
  const list = $("deadline-list");
  const signature = JSON.stringify([filter, visible, Math.floor(now / 60000)]);
  if (list.dataset.signature !== signature) {
    const rows = visible.map((item) => {
      const row = element("article", "deadline-row");
      const date = Number.isFinite(item.time) ? deadlineDateFormatter.format(item.time) : "Sans date";
      const tile = element("time", "deadline-date", date);
      if (Number.isFinite(item.time)) tile.dateTime = new Date(item.time).toISOString();
      tile.title = item.due;
      const info = element("div", "deadline-info");
      const title = element("a", "deadline-title", item.title);
      title.href = item.url;
      title.target = "_blank";
      title.rel = "noopener";
      const course = element("a", "deadline-course", item.course_title);
      course.href = courseUrl(item.course_id);
      info.append(title, course);
      const status = element("span", `badge ${item.status}`, statusLabels[item.status]);
      status.title = item.status === "upcoming" || item.status === "late"
        ? remainingDays(item.due) || item.due : item.due;
      row.append(tile, info, status);
      return row;
    });
    const empty = filter === "upcoming" ? "Aucune échéance à venir." : "Aucune échéance dans cette vue.";
    list.replaceChildren(...(rows.length ? rows : [element("p", "empty agenda-empty", empty)]));
    list.dataset.signature = signature;
  }
  $("deadline-footer").hidden = filtered.length <= maximum;
  $("deadline-more").setAttribute("aria-expanded", String(showAllDeadlines));
  $("deadline-more").textContent = showAllDeadlines ? "Réduire la liste" : `Voir les ${filtered.length} échéances`;
}

function render() {
  const { execution, config } = model;
  const selected = accordionView ? null : model.selected;
  const catalogueSignature = JSON.stringify(model.courses);
  if (render.catalogueSignature !== catalogueSignature) {
    const options = [new Option("Toutes les matières", "")];
    for (const course of model.courses) options.push(new Option(course.title, course.id));
    $("course-picker").replaceChildren(...options);
    $("course-picker").value = expandedCourse || selected?.id || "";
    updateDropdown["course-picker"]();
    renderCourses();
    render.catalogueSignature = catalogueSignature;
  }
  const showCatalogue = !selected && (catalogueView || Boolean(selectedId));
  $("course-title").textContent = selected?.title || (showCatalogue ? "Mes matières" : "Tableau de bord");
  document.title = selected ? `${selected.title} · HippoSync` : showCatalogue ? "Mes matières · HippoSync" : "HippoSync";
  $("course-link").href = selected?.url || model.courses_url;
  $("back-courses").hidden = !selected;
  $("browse-courses").hidden = showCatalogue || Boolean(selected);
  $("catalogue").hidden = !showCatalogue;
  $("dashboard-courses").hidden = !dashboardView;
  if (dashboardView) renderDashboardCourses();
  $("overview-metrics").hidden = Boolean(selected) || showCatalogue;
  $("dashboard-grid").hidden = showCatalogue;
  if (accordionView) updateCourseExpansion();
  else courseContent.hidden = !selected;

  const badge = $("sync-badge");
  badge.className = `badge ${execution.status || ""}`;
  badge.textContent = labels[execution.status] || "Prêt";
  const message = execution.progress
    ? `${execution.progress.done} / ${execution.progress.total} matières · ${execution.message}`
    : execution.message;
  $("sync-message").textContent = selected?.warning || message || "Lance une synchronisation pour récupérer tes matières.";
  $("last-sync").textContent = dateLabel(execution.success);
  $("next-sync").textContent = config.interval === 0 ? "En pause" : model.scheduled ? dateLabel(model.next) : "À configurer";
  $("course-count").textContent = model.courses.length;
  const frequency = [...$("interval").options].find((item) => Number(item.value) === config.interval);
  $("sync-frequency").textContent = frequency?.label || "—";
  $("dashboard-grid").classList.toggle("course-view", Boolean(selected));
  $("agenda").hidden = Boolean(selected);
  $("dashboard-grid").classList.toggle("is-syncing", model.active);
  renderAgenda();
  $("open-root").disabled = selected ? !selected.folder_exists : !model.root_exists;
  $("sync").disabled = model.active;
  $("login").hidden = !["login", "expired", "error"].includes(execution.status);
  $("login").disabled = model.active;
  const content = accordionView && model.selected?.id !== expandedCourse ? null : model.selected;
  const signature = JSON.stringify(content) + Math.floor(Date.now() / 60000);
  if (content && render.signature !== signature) {
    renderContent();
    render.signature = signature;
  }
}

async function refresh() {
  clearTimeout(refreshTimer);
  stateRequest?.abort();
  const request = new AbortController();
  stateRequest = request;
  try {
    const params = new URLSearchParams();
    const identifier = expandedCourse || selectedId;
    if (identifier) params.set("course", identifier);
    if (catalogueView) params.set("view", "matieres");
    if (dashboardView) params.set("view", "dashboard");
    const response = await fetch("/api/state?" + params, { cache: "no-store", signal: request.signal });
    if (!response.ok) throw new Error();
    const nextModel = await response.json();
    if (request.signal.aborted) return;
    model = nextModel;
    render();
  } catch {
    if (request.signal.aborted) return;
    const loading = expandedCourse && $(`course-expand-${expandedCourse}`)?.querySelector(".course-loading");
    if (loading) loading.textContent = "Chargement indisponible. Nouvelle tentative en cours…";
    $("sync-badge").textContent = "Interface déconnectée";
    $("sync-badge").className = "badge error";
    $("sync-message").textContent = "Relance HippoSync si la connexion locale ne revient pas.";
    $("sync").disabled = true;
  } finally {
    if (!request.signal.aborted) refreshTimer = setTimeout(refresh, model?.active ? 1800 : 9500);
  }
}

async function start(mode) {
  $("sync").disabled = true;
  $("login").disabled = true;
  try {
    await post(`/api/work/${mode}`);
    if (mode === "login") notify("Connecte-toi dans la fenêtre Centrale Nantes.");
  } catch (error) {
    notify(error.message);
  } finally {
    await refresh();
  }
}

$("card-sort").addEventListener("change", (event) => {
  saveBrowserPreference(cardSortKey, event.target.value);
  if (model && dashboardView) renderDashboardCourses();
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted && model && dashboardView) renderDashboardCourses();
});

$("deadline-filter").addEventListener("change", () => {
  showAllDeadlines = false;
  if (model) renderAgenda();
});
$("deadline-more").addEventListener("click", () => {
  showAllDeadlines = !showAllDeadlines;
  if (model) renderAgenda();
});
for (const card of document.querySelectorAll(".metric")) {
  card.addEventListener("pointermove", (event) => {
    const bounds = card.getBoundingClientRect();
    card.style.setProperty("--spot-x", `${event.clientX - bounds.left}px`);
    card.style.setProperty("--spot-y", `${event.clientY - bounds.top}px`);
  });
}

$("sync").addEventListener("click", () => start("sync"));
$("login").addEventListener("click", () => start("login"));
$("open-root").addEventListener("click", () => openFolder([], accordionView ? null : model.selected?.id));
$("filter").addEventListener("input", () => { if (model?.selected) renderContent(); });
$("sort").addEventListener("change", () => { if (model?.selected) renderContent(); });
for (const filter of document.querySelectorAll("[data-content-filter]")) {
  filter.addEventListener("click", () => {
    document.querySelector("[data-content-filter][aria-pressed='true']")?.setAttribute("aria-pressed", "false");
    filter.setAttribute("aria-pressed", "true");
    contentFilter = filter.dataset.contentFilter;
    if (model?.selected) renderContent();
  });
}
$("course-filter").addEventListener("input", () => { if (model) renderCourses(); });
$("course-picker").addEventListener("change", (event) => {
  if (!accordionView) {
    window.location.assign(courseUrl(event.target.value));
    return;
  }
  if ($("course-filter").value) {
    $("course-filter").value = "";
    renderCourses();
  }
  setExpandedCourse(event.target.value);
  $(`course-expand-${expandedCourse}`)?.parentElement.scrollIntoView({ block: "nearest" });
});
$("close-settings").addEventListener("click", () => dialog.close());
for (const button of document.querySelectorAll("[data-settings]")) {
  button.addEventListener("click", () => {
    if (!model) return;
    form.elements.root.value = model.config.root;
    form.elements.interval.value = model.config.interval;
    updateDropdown.interval();
    $("settings-error").hidden = true;
    $("schedule-help").textContent = model.scheduled ? "Le planificateur applique l’intervalle choisi." : "Configure un déclencheur système toutes les minutes pour activer la planification.";
    dialog.showModal();
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  $("settings-error").hidden = true;
  const data = Object.fromEntries(new FormData(form));
  data.interval = Number(data.interval);
  try {
    await post("/api/settings", data);
    dialog.close();
    notify("Réglages enregistrés.");
    await refresh();
  } catch (error) {
    $("settings-error").textContent = error.message;
    $("settings-error").hidden = false;
  } finally {
    button.disabled = false;
  }
});

const activeNavigation = catalogueView || selectedId ? "nav-courses" : "nav-dashboard";
$("nav-dashboard").removeAttribute("aria-current");
$(activeNavigation).setAttribute("aria-current", "page");

refresh();

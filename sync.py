import argparse
from collections import Counter
from email.message import Message
from datetime import datetime, timedelta
from hashlib import file_digest, sha256
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import sys
import unicodedata
from tempfile import NamedTemporaryFile
from subprocess import run
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from filelock import FileLock, Timeout
from platformdirs import user_state_path

BASE = "https://hippocampus.ec-nantes.fr"
SSO = "https://sso.ec-nantes.fr"
DEFAULT_ID = "2824"
COURSE_ID = DEFAULT_ID
COURSE_URL = f"{BASE}/course/view.php?id={COURSE_ID}"
DESTINATION = Path.home() / "Documents/Cours/CPP"
STATE = (Path.home() / ".local/state/hippocampus-sync" if sys.platform.startswith("linux")
         else user_state_path("HippocampusSync", appauthor=False))
COOKIES = STATE / "session.json"
INVENTORY = STATE / "fichiers.json"
SETTINGS = STATE / "reglages.json"
CATALOG = STATE / "catalogue.json"
BATCH_STATE = STATE / "synchronisation.json"
MY_COURSES = f"{BASE}/my/courses.php"
INTERVALS = (0, 5, 10, 15, 20, 30, 45, 60, 120, 240, 360, 720, 1440)
ASSIGNMENT_INSTRUCTIONS = {("mod_assign", area) for area in ("intro", "introattachment", "activityattachment")}
SUBMISSION_AREAS = {
    ("assignsubmission_file", "submission_files"): "Déposé",
    ("assignfeedback_file", "feedback_files"): "Retours",
    ("assignfeedback_editpdf", "download"): "Retours",
}
SECTION_LAYOUT = "#region-main [data-for='section'][id], #region-main .course-content li.section[id]"


class SyncError(Exception):
    pass


class LoginRequired(SyncError):
    pass


def save_json(path, data):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.close()
            temporary.replace(path)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)


def clean_name(value):
    value = re.sub(r'[\x00-\x1f\x7f/\\<>:"|?*]', " - ", value)
    value = " ".join(value.split()).strip(". ")
    value = value.encode("utf-8")[:210].decode("utf-8", errors="ignore").rstrip(". ")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", value.split(".")[0], re.IGNORECASE):
        value = "_" + value
    return value or "Sans titre"


def local_path(parts):
    path = DESTINATION.joinpath(*parts)
    root = DESTINATION.parent.resolve() / DESTINATION.name
    if not path.resolve().is_relative_to(root):
        raise SyncError("Un lien local sort du dossier de synchronisation.")
    return path


def is_origin(url, origin):
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" == origin


def set_query(url, **values):
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in values]
    return urlunsplit(parsed._replace(query=urlencode(query + list(values.items())), fragment=""))


def file_parts(url):
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if path.startswith("/pluginfile.php/"):
        path = path.removeprefix("/pluginfile.php")
    elif path == "/pluginfile.php":
        path = parse_qs(parsed.query).get("file", [""])[0]
    else:
        return ()
    return tuple(part for part in path.split("/") if part)


def get(session, url, previous=None):
    for _ in range(10):
        if not any(is_origin(url, origin) for origin in (BASE, SSO)):
            raise SyncError("Lien de téléchargement en dehors d’Hippocampus.")
        headers = {}
        if previous and url == previous.get("url"):
            if previous.get("etag"):
                headers["If-None-Match"] = previous["etag"]
            elif previous.get("modified"):
                headers["If-Modified-Since"] = previous["modified"]
        response = session.get(url, headers=headers, allow_redirects=False, stream=True, timeout=(10, 55))
        if response.is_redirect:
            url = urljoin(response.url, response.headers["Location"])
            response.close()
            continue
        path = urlsplit(response.url).path
        if not is_origin(response.url, BASE) or path.startswith("/login/") or response.status_code == 401:
            response.close()
            raise LoginRequired("Reconnecte-toi à Centrale Nantes pour reprendre la synchronisation.")
        if response.status_code not in (200, 304):
            status = response.status_code
            response.close()
            raise SyncError(f"Hippocampus a répondu HTTP {status}.")
        return response
    raise LoginRequired("La connexion Centrale Nantes n’a pas abouti. Relance hippocampus-sync --login.")


def html(response):
    return BeautifulSoup(response.content, "html.parser")


def linked_files(node, page_url):
    links = {}
    for link in node.select("a[href], object[data], iframe[src], embed[src]"):
        url = urljoin(page_url, link.get("href") or link.get("data") or link.get("src") or "")
        if is_origin(url, BASE) and file_parts(url):
            links[file_parts(url)] = set_query(url, forcedownload=1)
    return links


def section_page(url):
    if not is_origin(url, BASE):
        return False
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    return (parsed.path == "/course/section.php" and query.get("id", [""])[0].isdigit()
            or parsed.path == "/course/view.php" and query.get("id") == [COURSE_ID] and "section" in query)


def section_name(section, soup):
    title = section.select_one(".sectionname, [data-for='section_title']")
    if title is None and not section.has_attr("data-sectionname"):
        title = soup.select_one("#region-main h2, #page-header h1")
    return clean_name(section.get("data-sectionname") or
                      (title.get_text(" ", strip=True) if title else "Général"))


def activity_name(label, fallback):
    if label is None:
        return fallback
    for hidden in label.select(".accesshide, .sr-only, .visually-hidden"):
        hidden.extract()
    return clean_name(label.get_text(" ", strip=True))


def discover(session, course=None, warnings=None):
    seen = set()

    def collect_links(anchors, page_url, directory):
        if course is None:
            return
        for anchor in anchors:
            target = urlsplit(urljoin(page_url, anchor["href"]))
            if target.scheme not in ("http", "https") or not target.hostname or target.username is not None:
                continue
            if is_origin(target.geturl(), BASE):
                activity = re.fullmatch(r"/mod/([a-z][a-z0-9_]*)/view\.php", target.path)
                if not activity or activity[1] in {"assign", "folder", "resource", "subsection"}:
                    continue
                identifier = parse_qs(target.query).get("id", [""])[0]
                if not identifier.isdecimal():
                    continue
                address = f"{BASE}{target.path}?id={identifier}"
            else:
                address = target.geturl()
            title = activity_name(anchor.select_one(".instancename") or anchor, target.hostname)
            course["links"].setdefault(address, {"url": address, "title": title, "section": list(directory)})

    def scan_section(url, directory):
        try:
            yield from scan(url, directory)
        except LoginRequired:
            raise
        except (SyncError, requests.RequestException) as error:
            if warnings is None:
                raise
            warnings.append(directory[-1])
            logging.warning("Rubrique %s : %s", directory[-1],
                            str(error) if isinstance(error, SyncError) else type(error).__name__)

    def scan(url, prefix=()):
        url = url.split("#", 1)[0]
        if url in seen:
            return
        seen.add(url)
        with get(session, url) as response:
            soup = html(response)
            page_url = response.url
        body = soup.body
        if not body or f"course-{COURSE_ID}" not in body.get("class", []):
            raise SyncError(f"La page reçue ne correspond pas au cours {COURSE_ID}.")
        if "notloggedin" in body.get("class", []):
            raise LoginRequired("Reconnecte-toi à Centrale Nantes pour reprendre la synchronisation.")
        if body.select_one("#region-main .alert-danger, #notice"):
            raise SyncError("Moodle signale une erreur d’accès au cours.")

        if course is not None and url == COURSE_URL:
            page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
            course["title"] = re.sub(r"^(Cours|Course)\s*:\s*", "", page_title.split("|", 1)[0]).strip()

        sections = soup.select(SECTION_LAYOUT)
        if not sections:
            raise SyncError("Aucune rubrique Moodle reconnue sur cette page.")
        names = [section_name(section, soup) for section in sections]
        duplicates = Counter(names)
        for section, name in zip(sections, names):
            # The overview already supplied the path for sections on a dedicated page.
            if prefix and len(sections) == 1:
                directory = prefix
            else:
                if duplicates[name] > 1:
                    name += f" ({section.get('id', 'section')})"
                directory = prefix + (name,)
            scoped_links = section.select("a[href]")
            collect_links(scoped_links, page_url, directory)
            activities = section.select("[data-for='cmitem'], li.activity")
            for activity in activities:
                links = activity.select("a[href]")
                by_path = {}
                for link in links:
                    linked = urljoin(page_url, link["href"])
                    if is_origin(linked, BASE):
                        by_path.setdefault(urlsplit(linked).path, (linked, link))
                folder = by_path.get("/mod/folder/view.php")
                resource = by_path.get("/mod/resource/view.php")
                subsection = by_path.get("/mod/subsection/view.php")
                assignment = by_path.get("/mod/assign/view.php")
                if course is not None and assignment:
                    assignment_url, link = assignment
                    activity_id = parse_qs(urlsplit(assignment_url).query).get("id", [""])[0]
                    if activity_id.isdecimal():
                        title = activity_name(link.select_one(".instancename") or link, "Devoir")
                        course["assignments"].setdefault(activity_id, {"title": title, "url": f"{BASE}/mod/assign/view.php?id={activity_id}",
                             "section": list(directory)})
                embedded = linked_files(activity, page_url)
                if assignment:
                    embedded = {parts: source for parts, source in embedded.items()
                                if len(parts) > 2 and parts[1:3] in ASSIGNMENT_INSTRUCTIONS}
                classes = activity.get("class", [])
                if folder or "modtype_folder" in classes or "folder" in classes:
                    label = activity.select_one(".instancename, .activityname, .activitytitle")
                    if not label and folder:
                        label = folder[1]
                    title = activity_name(label, "Dossier")
                    suffix = activity.get("data-id") or activity.get("id") or title
                    files = dict(embedded)
                    if folder:
                        try:
                            with get(session, folder[0]) as response:
                                folder_page = html(response)
                                if folder_page.select_one("#region-main .alert-danger, #notice"):
                                    raise SyncError(f"Moodle signale une erreur dans le dossier {title}.")
                                files.update(linked_files(folder_page.select_one("#region-main") or folder_page, response.url))
                        except LoginRequired:
                            raise
                        except (SyncError, requests.RequestException) as error:
                            if warnings is None:
                                raise
                            warnings.append(title)
                            logging.warning("Dossier %s : %s", title,
                                            str(error) if isinstance(error, SyncError) else type(error).__name__)
                    for parts, source in files.items():
                        if len(parts) >= 5 and parts[1:3] == ("mod_folder", "content"):
                            yield directory + (clean_name(title),) + tuple(map(clean_name, parts[4:-1])), source, str(suffix)
                elif resource:
                    label = resource[1].select_one(".instancename") or resource[1]
                    title = activity_name(label, str(activity.get("data-id") or ""))
                    yield directory, set_query(resource[0], redirect=1), title
                elif subsection:
                    label = subsection[1].select_one(".instancename") or subsection[1]
                    yield from scan_section(subsection[0], directory + (activity_name(label, "Sous-section"),))
                else:
                    for source in embedded.values():
                        yield directory, source, str(activity.get("data-id") or "")
            for link in scoped_links:
                if link.find_parent(attrs={"data-for": "cmitem"}) or link.find_parent("li", class_="activity"):
                    continue
                source = urljoin(page_url, link["href"]).split("#", 1)[0]
                if section_page(source) and source != url:
                    yield from scan_section(source, directory)
                elif is_origin(source, BASE) and file_parts(source):
                    yield directory, set_query(source, forcedownload=1), ""

    yield from scan(COURSE_URL)


def filename(response):
    disposition = Message()
    if header := response.headers.get("Content-Disposition"):
        disposition["Content-Disposition"] = header
    parts = file_parts(response.url)
    return clean_name(disposition.get_filename() or (parts[-1] if parts else "Sans titre"))


def file_key(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def reference_key(directory, source):
    return json.dumps([directory, source], ensure_ascii=False)


def sync_documents(session, inventory, dry_run, references, warnings=None):
    seen = set()
    for directory, source, title in references:
        key = reference_key(directory, source)
        if key in seen:
            continue
        try:
            sync_file(session, inventory, dry_run, directory, source, title, key)
            seen.add(key)
        except LoginRequired:
            raise
        except (SyncError, requests.RequestException) as error:
            if warnings is None:
                raise
            warnings.append(title or "Document")
            logging.warning("Document %s : %s", title or "Moodle",
                            str(error) if isinstance(error, SyncError) else type(error).__name__)
    return len(seen)


def sync_file(session, inventory, dry_run, directory, source, title, key):
    previous = inventory.get(key)
    if previous:
        old = local_path(previous["path"])
        if not old.is_file() or old.is_symlink() or file_key(old) != previous["stat"]:
            previous = None

    with get(session, source, previous) as response:
        if response.status_code == 304 and previous:
            if dry_run:
                print("/".join(previous["path"]))
            return
        if (not file_parts(response.url) and "attachment" not in response.headers.get("Content-Disposition", "").lower()):
            raise SyncError("Une ressource Moodle n’a pas fourni de document.")
        name = filename(response)
        if dry_run:
            print("/".join(directory + (name,)))
            return
        if (response.headers.get("Content-Type", "").split(";", 1)[0] == "text/html"
                and not name.lower().endswith((".htm", ".html"))
                and "attachment" not in response.headers.get("Content-Disposition", "").lower()):
            raise SyncError("Hippocampus a renvoyé une page HTML à la place d’un document.")

        path = local_path(directory + (name,))
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix=".hippocampus-", delete=False) as output:
            temporary = Path(output.name)
            try:
                digest = sha256()
                with output:
                    for chunk in response.iter_content(128 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                checksum = digest.hexdigest()
                replace = bool(previous and previous["name"] == name and previous.get("owned"))
                owned = True
                if replace:
                    path = old
                    changed = previous["sha256"] != checksum
                else:
                    suffix = title.encode("utf-8")[:32].decode("utf-8", errors="ignore").rstrip()
                    suffix = suffix or sha256(source.encode()).hexdigest()[:10]
                    stem, extension = path.stem, path.suffix
                    number = 0
                    while path.exists() or path.is_symlink():
                        if path.is_file() and not path.is_symlink():
                            with path.open("rb") as local:
                                if file_digest(local, "sha256").hexdigest() == checksum:
                                    owned = False
                                    break
                        number += 1
                        extra = f" - {suffix}" if number == 1 else f" - {suffix} ({number})"
                        path = local_path(directory + (stem + extra + extension,))
                    changed = owned

                if changed:
                    temporary.replace(path)
                    logging.info("Téléchargé : %s", path.relative_to(DESTINATION))
                inventory[key] = {"path": path.relative_to(DESTINATION).parts, "name": name,
                                  "owned": owned, "stat": file_key(path), "sha256": checksum,
                                  "url": response.url, "etag": response.headers.get("ETag"),
                                  "modified": response.headers.get("Last-Modified"),
                                  "synced_at": timestamp() if changed else (previous or {}).get("synced_at")}
                save_json(INVENTORY, inventory)
            finally:
                temporary.unlink(missing_ok=True)


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def restore_session():
    cookies = load_json(COOKIES, [])
    if not cookies:
        raise LoginRequired("Connecte-toi à Centrale Nantes pour démarrer la synchronisation.")
    client = requests.Session()
    client.headers["Accept-Language"] = "fr-FR,fr;q=0.9"
    for cookie in cookies:
        expires = cookie["expires"]
        client.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"],
                           path=cookie["path"], secure=cookie["secure"],
                           expires=int(expires) if expires and expires >= 0 else None)
    return client


def persist_session(client):
    save_json(COOKIES, [
        {"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
         "path": cookie.path, "secure": cookie.secure, "expires": cookie.expires}
        for cookie in client.cookies
    ])


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_settings():
    return {"root": str(Path.home() / "Documents/Cours"), "interval": 60, "interval_unit": "minutes", "cpp_folder": "CPP"}


def settings():
    saved = load_json(SETTINGS, {})
    if "root" not in saved and saved.get("destination"):
        previous = Path(saved["destination"])
        saved = {"root": str(previous.parent), "cpp_folder": previous.name, "interval": saved.get("interval", 1)}
    if saved and saved.get("interval_unit") != "minutes":
        saved["interval"] = saved.get("interval", 1) * 60
    return default_settings() | saved


def course_storage(identifier):
    return STATE if str(identifier) == DEFAULT_ID else STATE / "cours" / str(identifier)


def course_destination(config, course):
    folder = config["cpp_folder"] if course["id"] == DEFAULT_ID else course["folder"]
    return Path(config["root"]) / folder


def configure(config, course):
    global COURSE_ID, COURSE_URL, DESTINATION, INVENTORY
    COURSE_ID, COURSE_URL = course["id"], course["url"]
    DESTINATION = course_destination(config, course)
    storage = course_storage(COURSE_ID)
    storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    INVENTORY = storage / "fichiers.json"
    return storage


def read_catalog():
    return load_json(CATALOG, [])


def find_courses(client, config):
    with get(client, MY_COURSES) as response:
        match = re.search(r"""["']sesskey["']\s*:\s*["']([A-Za-z0-9]+)["']""", response.text)
    if not match:
        raise LoginRequired("Reconnecte-toi à Centrale Nantes pour afficher tes matières.")
    method = "core_course_get_enrolled_courses_by_timeline_classification"
    offset = 0
    enrolled = {}
    while True:
        call = {"index": 0, "methodname": method, "args": {
            "classification": "allincludinghidden", "limit": 100, "offset": offset,
            "requiredfields": ["id", "fullname", "shortname", "startdate", "enddate", "visible"],
        }}
        with client.post(f"{BASE}/lib/ajax/service.php", params={"sesskey": match[1], "info": method},
                         json=[call], timeout=(10, 30), allow_redirects=False) as response:
            if response.is_redirect or response.status_code in (401, 403):
                raise LoginRequired("Reconnecte-toi à Centrale Nantes pour afficher tes matières.")
            try:
                if response.status_code != 200:
                    raise ValueError()
                result, = response.json()
                if result.get("error"):
                    if result.get("exception", {}).get("errorcode") in ("invalidsesskey", "requireloginerror"):
                        raise LoginRequired("Reconnecte-toi à Centrale Nantes pour afficher tes matières.")
                    raise ValueError()
                data = result["data"]
                rows, next_offset = data["courses"], int(data["nextoffset"])
                for row in rows:
                    identifier = str(row["id"])
                    if re.fullmatch(r"[1-9][0-9]{0,11}", identifier):
                        title = BeautifulSoup(row["fullname"], "html.parser").get_text(" ", strip=True)
                        enrolled[identifier] = title or f"Cours {identifier}"
            except (KeyError, ValueError, TypeError, AttributeError):
                raise SyncError("Impossible de récupérer la liste de tes matières sur Hippocampus.") from None
        if next_offset <= offset:
            break
        offset = next_offset

    previous = {course["id"]: course["folder"] for course in read_catalog()}
    used = {folder.casefold() for folder in previous.values()} | {config["cpp_folder"].casefold()}
    catalog = []
    for identifier, title in sorted(enrolled.items(), key=lambda item: label_key(item[1])):
        folder = config["cpp_folder"] if identifier == DEFAULT_ID else previous.get(identifier)
        if not folder:
            folder = clean_name(title)
            if folder.casefold() in used:
                folder = f"{folder} ({identifier})"
        used.add(folder.casefold())
        catalog.append({"id": identifier, "title": title, "folder": folder,
                        "url": f"{BASE}/course/view.php?id={identifier}"})
    return catalog


def sync_lock(timeout=0):
    return FileLock(str(STATE / "sync.lock"), timeout=timeout, mode=0o600)


def next_scheduled(now, interval):
    if not interval:
        return None
    minute_of_day = now.hour * 60 + now.minute
    return now.replace(second=0, microsecond=0) + timedelta(minutes=interval - minute_of_day % interval)


def label_key(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(character if character.isascii() else " "
                         for character in decomposed if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


SUBMISSION_LABELS = ("statut des travaux remis", "statut de la remise", "submission status")
SUBMISSION_CLASSES = {
    "submissionstatussubmitted": "submitted",
    "submissionstatusdraft": "draft",
    "submissionstatusreopened": "draft",
    "submissionstatusnew": "pending",
}


def submission_state(status, classes=()):
    for marker, state in SUBMISSION_CLASSES.items():
        if marker in classes:
            return state
    label = label_key(status)
    if label.startswith(("brouillon", "draft", "rouvert", "reopened")):
        return "draft"
    if label.startswith(("aucun devoir", "aucun travail", "rien n a ete depose",
                         "nothing submitted", "no submission", "not submitted")):
        return "pending"
    if label.startswith(("remis pour evaluation", "remis pour notation",
                         "envoye pour evaluation", "submitted for grading")) or label in {"remis", "submitted"}:
        return "submitted"
    if "remise en ligne" in label or "remise de travaux en ligne" in label or label == "no online submission required":
        return "offline"
    return "unknown"


def assignment_details(soup, url):
    region = soup.select_one("#region-main")
    if region is None or region.select_one("#notice, .errorbox"):
        raise SyncError("Le devoir est inaccessible.")
    fields = {}
    submission_classes = ()
    table = region.select_one(".submissionstatustable table")
    if table:
        for row in table.select("tr"):
            label = row.select_one("th")
            value = row.select_one("td")
            if label and value:
                key = label_key(label.get_text(" ", strip=True))
                fields[key] = value.get_text(" ", strip=True)
                if key in SUBMISSION_LABELS:
                    submission_classes = value.get("class", [])
    status = next((fields[key] for key in SUBMISSION_LABELS if fields.get(key)), "Voir sur Hippocampus")
    due = next((fields[name] for name in (
        "date de remise prolongee", "date limite prolongee", "date de prolongation",
        "date limite de prolongation", "extension due date", "date de remise",
        "date limite", "date d echeance", "due date",
    ) if fields.get(name)), "")
    for date in region.select(".activity-dates div"):
        label = date.find("strong", recursive=False)
        if not due and label and label_key(label.get_text()) in ("a rendre", "a rendre le", "date de remise", "due", "due date"):
            due = date.get_text(" ", strip=True).removeprefix(label.get_text(" ", strip=True)).strip(" :")
    files = {}
    for parts, source in linked_files(region, url).items():
        if len(parts) >= 5 and parts[1:3] in SUBMISSION_AREAS:
            files[parts] = (source, SUBMISSION_AREAS[parts[1:3]])
        elif len(parts) >= 4 and parts[1:3] in ASSIGNMENT_INSTRUCTIONS:
            files[parts] = (source, "Consignes")
    return {
        "status": status,
        "submission_state": submission_state(status, submission_classes),
        "grading": fields.get("statut de l evaluation") or fields.get("grading status") or "",
        "modified": fields.get("derniere modification") or fields.get("last modified") or "",
        "due": due,
        "remaining": fields.get("temps restant") or fields.get("time remaining") or "",
    }, files


def sync_assignments(client, course, inventory, warnings=None):
    assignments = course["assignments"]
    counts = Counter(item["title"] for item in assignments.values())
    result = []
    for identifier, assignment in assignments.items():
        title = assignment["title"]
        directory = title if counts[title] == 1 else f"{title} ({identifier})"
        item = assignment | {"id": identifier, "files": []}
        try:
            with get(client, item["url"]) as response:
                details, files = assignment_details(html(response), response.url)
            item.update(details)
            for parts, (source, category) in files.items():
                nested = tuple(map(clean_name, parts[4:-1]))
                if category == "Consignes":
                    folder = (*assignment.get("section", []), directory, *nested)
                    sync_documents(client, inventory, False, [(folder, source, title)], warnings)
                    continue
                folder = ("Rendus", directory, category, *nested)
                sync_documents(client, inventory, False, [(folder, source, title)])
                row = inventory[reference_key(folder, source)]
                item["files"].append({"path": row["path"], "category": category})
        except LoginRequired:
            raise
        except (SyncError, requests.RequestException) as error:
            item["error"] = str(error) if isinstance(error, SyncError) else "Le devoir n’a pas pu être récupéré."
            logging.warning("Devoir %s : %s", identifier, item["error"])
        result.append(item)
    return result


def course_sync(client, config, course, dry_run):
    storage = configure(config, course)
    warnings = []
    inventory = load_json(INVENTORY, {})
    details = {"title": course["title"], "assignments": {}, "links": {}}
    if dry_run:
        print(course["title"], flush=True)
    try:
        count = sync_documents(client, inventory, dry_run, discover(client, details, warnings), warnings)
        if dry_run:
            return count, False
        local_path(()).mkdir(mode=0o700, parents=True, exist_ok=True)
        assignments = sync_assignments(client, details, inventory, warnings)
        partial = bool(warnings or any(item.get("error") for item in assignments))
        document_count = len({tuple(item["path"]) for item in inventory.values() if item["path"][0] != "Rendus"})
        save_json(storage / "cours.json", {"title": details["title"], "assignments": assignments,
                                          "documents": document_count, "links": list(details["links"].values()),
                                          "updated": timestamp()})
        save_json(storage / "execution.json", {"status": "partial" if partial else "success",
                                               "finished": timestamp(), "count": document_count,
                                               "warnings": len(warnings)})
        return document_count, partial
    except LoginRequired:
        raise
    except (SyncError, requests.RequestException, OSError) as error:
        if not dry_run:
            message = str(error) if isinstance(error, SyncError) else "La matière n’a pas pu être synchronisée."
            save_json(storage / "execution.json", {"status": "error", "message": message, "finished": timestamp()})
        logging.warning("%s : %s", course["title"], str(error) if isinstance(error, SyncError) else type(error).__name__)
        return 0, True


def run_sync(args, config):
    batch = load_json(BATCH_STATE, {})
    started = timestamp()

    def update(status, **data):
        if not args.dry_run:
            batch.update({"status": status, "started": started} | data)
            save_json(BATCH_STATE, batch)

    try:
        if args.login:
            update("login", message="Connecte-toi dans la fenêtre Centrale Nantes.", progress=None)
            login_script = Path(__file__).with_name("login.cjs")
            node = shutil.which("node")
            if not node:
                raise LoginRequired("Node.js est nécessaire pour ouvrir la fenêtre de connexion.")
            finished = run([node, str(login_script), MY_COURSES, str(COOKIES)], check=False)
            if finished.returncode:
                raise LoginRequired("Connexion non confirmée. Relance la connexion Centrale Nantes.")
        update("running", message="Lecture de tes inscriptions Hippocampus…", progress=None)
        with restore_session() as client:
            if args.dry_run and args.course:
                courses = [course for course in read_catalog() if course["id"] == args.course]
            else:
                courses = find_courses(client, config)
            if not args.dry_run:
                save_json(CATALOG, courses)
                persist_session(client)
            total = len(courses)
            documents = 0
            partial = 0
            for index, course in enumerate(courses):
                update("running", message=course["title"], progress={"done": index, "total": total})
                count, failed = course_sync(client, config, course, args.dry_run)
                documents += count
                partial += int(failed)
                logging.info("%d/%d %s, %d document(s)%s", index + 1, total, course["title"],
                             count, " à vérifier" if failed else "")
            persist_session(client)
        status = "partial" if partial else "success"
        message = f"{total} matière(s) synchronisée(s)." if not partial else f"{partial} matière(s) à vérifier."
        update(status, success=timestamp(), message=message, progress=None)
        logging.info("Synchronisation terminée. %d matière(s), %d document(s), %d à vérifier.", total, documents, partial)
        return 0
    except LoginRequired as error:
        status, message = "expired", str(error)
    except SyncError as error:
        status, message = "error", str(error)
    except requests.RequestException:
        status, message = "error", "Hippocampus est temporairement inaccessible."
    except (OSError, ValueError):
        status, message = "error", "Impossible de mettre à jour le dossier ou l’état local."
    logging.error("%s", message)
    update(status, message=message, progress=None)
    return 1


def main():
    parser = argparse.ArgumentParser(description="Synchronise les documents et rendus Hippocampus.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--login", action="store_true", help="Se connecter dans le navigateur puis synchroniser.")
    mode.add_argument("--dry-run", action="store_true", help="Afficher les chemins sans télécharger les documents.")
    mode.add_argument("--cron", action="store_true", help="Exécuter la synchronisation planifiée selon les réglages.")
    mode.add_argument("--ui", action="store_true", help="Démarrer et ouvrir l’interface locale.")
    mode.add_argument("--serve", action="store_true", help="Démarrer l’interface locale sans ouvrir de navigateur.")
    parser.add_argument("--course", help="Limiter l’aperçu à une matière déjà détectée.")
    args = parser.parse_args()
    if args.course and not args.dry_run:
        parser.error("--course s’utilise avec --dry-run.")
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.ui or args.serve:
        from app import serve
        serve(open_browser=args.ui)
        return 0

    try:
        with sync_lock():
            config = settings()
            interval = config["interval"]
            now = datetime.now()
            if args.cron and (not interval or (now.hour * 60 + now.minute) % interval):
                return 0
            handler = RotatingFileHandler(STATE / "sync.log", maxBytes=256_000, backupCount=2, encoding="utf-8")
            handlers = [handler] if args.cron else [handler, logging.StreamHandler()]
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
            return run_sync(args, config)
    except Timeout:
        if not args.cron:
            print("Une synchronisation est déjà en cours.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

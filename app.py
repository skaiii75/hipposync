from datetime import datetime
from functools import lru_cache
import os
from pathlib import Path
import shlex
import secrets
import subprocess
import sys
from threading import Lock, Thread
import time
import webbrowser

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import Timeout
from pydantic import BaseModel
import requests
from starlette.middleware.trustedhost import TrustedHostMiddleware

import sync

ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8765
ADDRESS = f"http://{HOST}:{PORT}"
TOKEN = secrets.token_urlsafe(32)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])
app.mount("/assets", StaticFiles(directory=ROOT / "ui"), name="assets")
PROCESS_LOCK = Lock()
active_process = None


class Settings(BaseModel):
    root: str
    interval: int


class Folder(BaseModel):
    path: list[str]
    course: str | None = None


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        expected = f"http://{request.url.netloc}"
        if origin != expected or not secrets.compare_digest(request.headers.get("x-hippocampus-token", ""), TOKEN):
            return JSONResponse({"detail": "Recharge l’interface locale pour continuer."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/")
@app.get("/matieres")
def index():
    return FileResponse(ROOT / "ui" / "index.html")


@app.get("/api/health")
def health():
    return {"application": "hippocampus-sync"}


def work_active():
    with PROCESS_LOCK:
        if active_process is not None and active_process.poll() is None:
            return True
    try:
        with sync.sync_lock():
            return False
    except Timeout:
        return True


@lru_cache(maxsize=1)
def system_schedule(minute):
    if sys.platform == "win32":
        command = ["schtasks", "/Query", "/TN", "HippocampusSync"]
    elif sys.platform == "darwin":
        command = ["launchctl", "print", f"gui/{os.getuid()}/fr.hippocampus.sync"]
    else:
        command = ["crontab", "-l"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return False
    if sys.platform.startswith("linux"):
        for line in result.stdout.splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) != 6 or fields[:5] != ["*", "*", "*", "*", "*"]:
                continue
            try:
                arguments = shlex.split(fields[5], comments=True)
            except ValueError:
                continue
            if "--cron" in arguments and any(Path(arg).name in ("hippocampus-sync", "sync.py") for arg in arguments):
                return True
        return False
    return result.returncode == 0


def path_in_destination(destination, parts):
    destination = destination.resolve()
    if any(not part or part in (".", "..") or Path(part).name != part for part in parts):
        raise HTTPException(400, "Dossier invalide.")
    result = destination.joinpath(*parts).resolve()
    if not result.is_relative_to(destination):
        raise HTTPException(400, "Le chemin sort du dossier de cours.")
    return result


NATIVE_FILE_TYPES = frozenset(
    ".pdf .doc .docx .odt .ppt .pptx .odp .xls .xlsx .ods "
    ".png .jpg .jpeg .gif .webp .mp3 .mp4 .wav .zip .tar .gz .7z .vi".split()
)
TEXT_FILE_TYPES = frozenset(
    ".txt .csv .md .log .html .htm .css .js .json .xml .svg "
    ".cpp .c .hpp .h .kt .java .m .py .sh .bat .ps1 .desktop .yml .yaml".split()
)


def course_for(identifier):
    course = next((item for item in sync.read_catalog() if item["id"] == identifier), None)
    if course is None:
        raise HTTPException(404, "Cette matière est introuvable.")
    return course


def synced_file(identifier, parts):
    if not identifier or not parts:
        raise HTTPException(400, "Sélectionne un fichier de cours.")
    course = course_for(identifier)
    path = path_in_destination(sync.course_destination(sync.settings(), course), parts)
    inventory = sync.load_json(sync.course_storage(identifier) / "fichiers.json", {})
    if not path.is_file() or not any(entry["path"] == parts for entry in inventory.values()):
        raise HTTPException(404, "Ce fichier n’est plus disponible dans le dossier synchronisé.")
    return path


def open_on_desktop(path):
    if sys.platform == "win32":
        os.startfile(path)
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([command, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@app.get("/api/file")
def view_file(course: str, path: list[str] = Query()):
    file = synced_file(course, path)
    suffix = file.suffix.lower()
    preview_type = "text/plain" if suffix in TEXT_FILE_TYPES else "application/pdf" if suffix == ".pdf" else None
    return FileResponse(file, filename=file.name, media_type=preview_type or "application/octet-stream",
                        content_disposition_type="inline" if preview_type else "attachment")


@app.post("/api/open-file")
def open_file(file: Folder):
    path = synced_file(file.course, file.path)
    if path.suffix.lower() not in NATIVE_FILE_TYPES:
        raise HTTPException(400, "Ce format s’ouvre dans le navigateur ou se télécharge.")
    try:
        open_on_desktop(path)
    except OSError:
        raise HTTPException(500, "Impossible d’ouvrir ce fichier avec l’application du système.") from None
    return {"ok": True}


def document_rows(destination, storage, include_submissions=False):
    rows = {}
    for entry in sync.load_json(storage / "fichiers.json", {}).values():
        parts = entry["path"]
        if parts[0] == "Rendus" and not include_submissions:
            continue
        try:
            path = path_in_destination(destination, parts)
            stat = path.stat()
            if not path.is_file():
                continue
        except (OSError, HTTPException):
            continue
        rows[tuple(parts)] = {
            "path": parts, "size": stat.st_size, "native": path.suffix.lower() in NATIVE_FILE_TYPES,
            "updated": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        }
    return sorted(rows.values(), key=lambda row: tuple(part.casefold() for part in row["path"]))


@app.get("/api/state")
def state(course: str | None = None, view: str | None = None):
    config = sync.settings()
    catalog = sync.read_catalog()
    active = work_active()
    execution = sync.load_json(sync.BATCH_STATE, {})
    if active and execution.get("status") not in ("login", "running"):
        execution |= {"status": "running", "message": "Démarrage de la synchronisation…"}
    elif not active and execution.get("status") in ("login", "running"):
        execution |= {"status": "error", "message": "La synchronisation précédente a été interrompue."}
    now = datetime.now().astimezone()
    scheduled = system_schedule(now.strftime("%Y-%m-%d %H:%M"))
    upcoming = sync.next_scheduled(now, config["interval"]) if scheduled else None

    summaries = []
    deadlines = []
    selected = None
    for item in catalog:
        storage = sync.course_storage(item["id"])
        details = sync.load_json(storage / "cours.json", {})
        result = sync.load_json(storage / "execution.json", {})
        destination = sync.course_destination(config, item)
        files = None
        modified = None
        if item["id"] == course or view in {"matieres", "dashboard"}:
            files = document_rows(destination, storage, include_submissions=view == "dashboard")
            if view == "dashboard":
                times = (datetime.fromisoformat(file["updated"]) for file in files)
                latest = max(times, default=None)
                modified = latest.isoformat() if latest else None
                files = [file for file in files if file["path"][0] != "Rendus"]
        document_count = len(files) if files is not None else details.get("documents", result.get("count", 0))
        summary = item | {"documents": document_count, "modified": modified,
                          "assignments": len(details.get("assignments", [])),
                          "links_count": len(details.get("links", [])),
                          "status": result.get("status"),
                          "warning": result.get("message")}
        summaries.append(summary)
        assignments = [
            assignment | {"submission_state": assignment.get("submission_state")
                          or sync.submission_state(assignment.get("status", ""))}
            for assignment in details.get("assignments", [])
        ]
        for assignment in assignments:
            if assignment.get("due") or assignment["submission_state"] == "submitted":
                deadlines.append({
                    "course_id": item["id"],
                    "course_title": item["title"],
                    "title": assignment["title"],
                    "due": assignment.get("due", ""),
                    "status": assignment.get("status", ""),
                    "submission_state": assignment["submission_state"],
                    "url": assignment["url"],
                })
        if item["id"] == course:
            selected = summary | {
                "files": files,
                "assignments": [
                    assignment | {"files": [
                        file | {"native": Path(file["path"][-1]).suffix.lower() in NATIVE_FILE_TYPES}
                        for file in assignment.get("files", [])
                    ]}
                    for assignment in assignments
                ],
                "links": details.get("links", []),
                "checked": details.get("updated"),
                "folder_exists": destination.is_dir(),
            }
    return {
        "token": TOKEN, "config": config, "courses": summaries, "selected": selected,
        "deadlines": deadlines,
        "execution": execution, "active": active, "courses_url": sync.MY_COURSES,
        "scheduled": scheduled, "next": upcoming.isoformat() if upcoming else None,
        "root_exists": Path(config["root"]).is_dir(),
    }


@app.post("/api/settings")
def save_settings(settings: Settings):
    config = settings.model_dump()
    try:
        destination = Path(config["root"].strip()).expanduser()
        if not destination.is_absolute() or destination.parent == destination:
            raise ValueError("Entre le chemin absolu du dossier de destination.")
        if destination.exists() and not destination.is_dir():
            raise ValueError("La destination doit être un dossier.")
        if config["interval"] not in sync.INTERVALS:
            raise ValueError("Choisis une fréquence proposée.")
        config["root"] = str(destination.resolve())
    except (OSError, ValueError) as error:
        raise HTTPException(400, str(error)) from None
    if work_active():
        raise HTTPException(409, "Attends la fin de la synchronisation pour modifier les réglages.")
    try:
        with sync.sync_lock():
            sync.save_json(sync.SETTINGS, sync.settings() | config)
    except Timeout:
        raise HTTPException(409, "Attends la fin de la synchronisation pour modifier les réglages.") from None
    return {"ok": True}


@app.post("/api/work/{mode}", status_code=202)
def run_work(mode: str):
    global active_process
    if mode not in ("sync", "login"):
        raise HTTPException(404)
    if work_active():
        raise HTTPException(409, "Une synchronisation est déjà en cours.")
    with PROCESS_LOCK:
        if active_process is not None and active_process.poll() is None:
            raise HTTPException(409, "Une synchronisation est déjà en cours.")
        args = [sys.executable, str(ROOT / "sync.py")]
        if mode == "login":
            args.append("--login")
        try:
            active_process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise HTTPException(500, "Le moteur de synchronisation n’a pas pu démarrer.") from None
    return {"ok": True}


@app.post("/api/open")
def open_folder(folder: Folder):
    config = sync.settings()
    if folder.course is None:
        if folder.path:
            raise HTTPException(400, "Sélectionne une matière.")
        destination = Path(config["root"])
    else:
        destination = sync.course_destination(config, course_for(folder.course))
    path = path_in_destination(destination, folder.path)
    if not path.is_dir():
        raise HTTPException(404, "Le dossier sera disponible après le téléchargement.")
    try:
        open_on_desktop(path)
    except OSError:
        raise HTTPException(500, "Impossible d’ouvrir le gestionnaire de fichiers.") from None
    return {"ok": True}


def is_running():
    try:
        response = requests.get(ADDRESS + "/api/health", timeout=0.5)
        return response.ok and response.json().get("application") == "hippocampus-sync"
    except (requests.RequestException, ValueError, AttributeError):
        return False


def serve(open_browser):
    import uvicorn

    if open_browser and is_running():
        webbrowser.open(ADDRESS)
        return
    if open_browser:
        def show_when_ready():
            for _ in range(40):
                if is_running():
                    webbrowser.open(ADDRESS)
                    return
                time.sleep(0.25)
        Thread(target=show_when_ready, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT)

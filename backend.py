import os, uuid, threading
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

BASE_DIR = os.path.dirname(__file__)
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = FastAPI(title="Open YouTube Downloader")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = {}
cancel_flags = {}
history = []

# ---------------- INFO (ALL QUALITIES + EMOJIS) ----------------
@app.get("/info")
def info(url: str):
    with yt_dlp.YoutubeDL({"skip_download": True, "quiet": True}) as ydl:
        d = ydl.extract_info(url, download=False)

    formats = []
    seen = set()

    for f in d["formats"]:
        fid = f.get("format_id")
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        ext = (f.get("ext") or "").upper()
        height = f.get("height")
        abr = f.get("abr")

        if not fid:
            continue

        # 🎥 Video formats (including HD video-only)
        if vcodec != "none":
            if height:
                label = f"🎥 {height}p {ext}"
            else:
                label = f"🎥 {ext}"

        # 🎵 Audio-only formats
        elif vcodec == "none" and acodec != "none":
            label = f"🎵 {int(abr) if abr else ''}kbps {ext}"

        else:
            continue

        # remove duplicate labels
        if label in seen:
            continue
        seen.add(label)

        formats.append({
            "id": fid,
            "label": label
        })

    # sort highest resolution first (video only)
    formats = sorted(
        formats,
        key=lambda x: int(x["label"].split("p")[0].replace("🎥 ", ""))
        if "🎥" in x["label"] and "p" in x["label"]
        and x["label"].split("p")[0].replace("🎥 ", "").isdigit()
        else 0,
        reverse=True
    )

    return {
        "title": d["title"],
        "thumbnail": d["thumbnail"],
        "formats": formats
    }

# ---------------- WORKER ----------------
def worker(job_id, url, fmt):
    def hook(d):
        if cancel_flags.get(job_id):
            raise Exception("cancelled")
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                jobs[job_id]["progress"] = int(
                    d.get("downloaded_bytes", 0) / total * 100
                )

    opts = {
        "format": f"{fmt}+bestaudio/best",
        "outtmpl": os.path.join(
            DOWNLOAD_DIR, "%(title).80s_%(id)s.%(ext)s"
        ),
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "quiet": True,
        "noplaylist": False
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    file = sorted(
        os.listdir(DOWNLOAD_DIR),
        key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x))
    )[-1]

    jobs[job_id]["status"] = "done"
    jobs[job_id]["file"] = file
    history.append(file)

# ---------------- DOWNLOAD ----------------
@app.post("/download")
def download(url: str, format_id: str):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"progress": 0, "status": "downloading"}
    cancel_flags[job_id] = False

    threading.Thread(
        target=worker,
        args=(job_id, url, format_id),
        daemon=True
    ).start()

    return {"job_id": job_id}

# ---------------- CANCEL ----------------
@app.post("/cancel/{job_id}")
def cancel(job_id: str):
    cancel_flags[job_id] = True
    return {"status": "cancelled"}

# ---------------- STATUS ----------------
@app.get("/status/{job_id}")
def status(job_id: str):
    return jobs.get(job_id, {})

# ---------------- HISTORY ----------------
@app.get("/history")
def get_history():
    return history[::-1]

# ---------------- FILE ----------------
@app.get("/file/{filename}")
def file(filename: str):
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=filename)

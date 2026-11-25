"""Ansys pipeline for the fast-scripted variant: pre-open, then SpaceClaim prep and Fluent
the moment the CAD is ready.

Launched ONCE, fire-and-forget, by the solidworks-engineer as the first call of Gate 1
(submit_ansys_job, cwd <RUN>\\00_lead):

    import os, runpy
    os.environ["BRAKE_RUN"] = r"C:\\brake-runs\\<run-id>"
    runpy.run_path(r"<PROJECT>\\scripts\\stages\\pipeline.py", run_name="__main__")

Sequence (each stage is the unchanged vetted stage script, run as a child process):
  1. prewarm.py      - opens SpaceClaim and Fluent in parallel (holds SpaceClaim)
  2. wait            - for 10_cad\\STEP_READY.json (written by sw_build.py on PASS);
                       stops if cad_report.json says FAIL or after WAIT_CAD_S
  3. spaceclaim_prep.py - connects to the pre-opened SpaceClaim, writes tag_report.json
  4. fluent_fast.py     - only if step 3 exits 0; connects to the pre-opened Fluent,
                          writes results.json, leaves Fluent open on PASS

The specialists still judge every artifact (tag_report.json, results.json) against the
gates; this job only removes the hand-off wait between stages. Progress and per-stage
timing go to <RUN>\\00_lead\\pipeline.json. ASCII only.
"""
import json, os, subprocess, sys, time
from pathlib import Path

RUN = Path(os.environ.get("BRAKE_RUN") or sys.argv[1])
STAGES = Path(__file__).resolve().parent
OUT = RUN / "00_lead" / "pipeline.json"
READY = RUN / "10_cad" / "STEP_READY.json"
CADREP = RUN / "10_cad" / "cad_report.json"
STOP = RUN / "20_spaceclaim" / "PREWARM_STOP"
FL_FILE = RUN / "30_fluent" / "prewarm_fluent.json"
WAIT_CAD_S = float(os.environ.get("PIPELINE_WAIT_CAD_S", "1200"))
for d in ("00_lead", "10_cad", "20_spaceclaim", "30_fluent"):
    (RUN / d).mkdir(parents=True, exist_ok=True)

T0 = time.time()
now = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
P = {"run_id": RUN.name, "started_at": now(), "status": "RUNNING", "events": [], "stages": {}}


def save():
    OUT.write_text(json.dumps(P, indent=1))


def event(what, **kw):
    e = {"t_s": round(time.time() - T0, 1), "at": now(), "event": what}
    e.update(kw)
    P["events"].append(e)
    print("PIPELINE", json.dumps(e), flush=True)
    save()


def run_stage(name, script, folder):
    t = time.time()
    env = dict(os.environ, BRAKE_RUN=str(RUN), PYTHONUNBUFFERED="1")
    event(f"{name}_start")
    log = open(RUN / folder / f"{name}_stage.log", "w", encoding="utf-8", errors="replace")
    p = subprocess.run([sys.executable, str(STAGES / script)], env=env, cwd=str(RUN / folder),
                       stdout=log, stderr=subprocess.STDOUT)
    log.close()
    P["stages"][name] = {"exit_code": p.returncode, "seconds": round(time.time() - t, 1),
                         "log": str(RUN / folder / f"{name}_stage.log")}
    event(f"{name}_end", exit_code=p.returncode, seconds=round(time.time() - t, 1))
    return p.returncode


def close_prewarm_fluent():
    """Abort path only: close the Fluent that prewarm.py started (its own PIDs, nothing else)."""
    try:
        import psutil
        pids = json.loads(FL_FILE.read_text()).get("pids") or []
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
            except Exception:
                pass
        event("prewarm_fluent_closed", pids=pids)
    except Exception as e:
        event("prewarm_fluent_close_failed", error=repr(e)[:200])


# ---- window focus: the app that is working is on screen, the pre-opened ones wait minimized ----
SC_FILE = RUN / "20_spaceclaim" / "prewarm_spaceclaim.json"
FOCUS = os.environ.get("PIPELINE_WINDOW_FOCUS", "1") == "1"
try:
    import psutil, win32con, win32gui, win32process
except Exception:  # noqa: BLE001 - focus is cosmetic, never fatal
    FOCUS = False


def _windows(pids):
    out = []
    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) and win32gui.GetParent(h) == 0:
                if win32process.GetWindowThreadProcessId(h)[1] in pids:
                    out.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return out


def app_pids(which):
    try:
        if which == "solidworks":
            return {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "sldworks.exe"}
        f = SC_FILE if which == "spaceclaim" else FL_FILE
        d = json.loads(f.read_text())
        if d.get("state") != "ready":
            return set()
        return {d["pid"]} if which == "spaceclaim" and d.get("pid") else set(d.get("pids") or [])
    except Exception:
        return set()


def minimize(which):
    if not FOCUS:
        return 0
    n = 0
    for h in _windows(app_pids(which)):
        try:
            if not win32gui.IsIconic(h):
                win32gui.ShowWindow(h, win32con.SW_SHOWMINNOACTIVE)
                n += 1
        except Exception:
            pass
    return n


def show(which):
    if not FOCUS:
        return
    hs = _windows(app_pids(which))
    for h in hs:
        try:
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
            win32gui.SetWindowPos(h, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            win32gui.SetWindowPos(h, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            try:
                win32gui.SetForegroundWindow(h)
            except Exception:
                pass  # Windows may refuse focus to a background process; the window is still raised
        except Exception:
            pass
    event("window_shown", app=which, windows=len(hs))


env = dict(os.environ, BRAKE_RUN=str(RUN), PYTHONUNBUFFERED="1")
pw_log = open(RUN / "00_lead" / "prewarm_stage.log", "w", encoding="utf-8", errors="replace")
prewarm = subprocess.Popen([sys.executable, str(STAGES / "prewarm.py")], env=env, cwd=str(RUN),
                           stdout=pw_log, stderr=subprocess.STDOUT)
event("prewarm_launched", pid=prewarm.pid)

try:
    # ---- wait for the CAD ----
    cad_ok = False
    show("solidworks")
    mins = {"spaceclaim": 0, "fluent": 0}
    while time.time() - T0 < WAIT_CAD_S:
        for app in mins:                      # keep the pre-opened apps out of the way while SolidWorks builds
            n = minimize(app)
            if n and not mins[app]:
                event("window_minimized", app=app, windows=n)
            mins[app] += n
        if READY.exists():
            cad_ok = True
            event("cad_ready", **json.loads(READY.read_text()))
            break
        if CADREP.exists():
            try:
                st = json.loads(CADREP.read_text()).get("status")
            except Exception:
                st = None
            if st and st not in ("RUNNING", "PASS"):
                event("cad_failed", cad_status=st)
                break
        time.sleep(1)
    else:
        event("cad_wait_timeout", seconds=WAIT_CAD_S)

    if not cad_ok:
        P["status"] = "ABORTED_NO_CAD"
        STOP.write_text("pipeline abort: no CAD\n")
        close_prewarm_fluent()
    else:
        minimize("fluent")
        show("spaceclaim")
        rc = run_stage("g2_spaceclaim_prep", "spaceclaim_prep.py", "20_spaceclaim")
        if rc != 0:
            P["status"] = "STOPPED_AT_G2"
            close_prewarm_fluent()
        else:
            show("fluent")
            rc = run_stage("g3_fluent_fast", "fluent_fast.py", "30_fluent")
            P["status"] = "DONE" if rc == 0 else "STOPPED_AT_G3"
finally:
    if prewarm.poll() is None:
        if not STOP.exists():
            STOP.write_text("pipeline end\n")
        try:
            prewarm.wait(timeout=60)
        except Exception:
            prewarm.terminate()
    pw_log.close()
    P["finished_at"] = now()
    P["seconds"] = round(time.time() - T0, 1)
    event("pipeline_end", status=P["status"])
    save()
sys.exit(0 if P["status"] == "DONE" else 1)

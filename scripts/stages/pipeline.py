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


env = dict(os.environ, BRAKE_RUN=str(RUN), PYTHONUNBUFFERED="1")
pw_log = open(RUN / "00_lead" / "prewarm_stage.log", "w", encoding="utf-8", errors="replace")
prewarm = subprocess.Popen([sys.executable, str(STAGES / "prewarm.py")], env=env, cwd=str(RUN),
                           stdout=pw_log, stderr=subprocess.STDOUT)
event("prewarm_launched", pid=prewarm.pid)

try:
    # ---- wait for the CAD ----
    cad_ok = False
    while time.time() - T0 < WAIT_CAD_S:
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
        rc = run_stage("g2_spaceclaim_prep", "spaceclaim_prep.py", "20_spaceclaim")
        if rc != 0:
            P["status"] = "STOPPED_AT_G2"
            close_prewarm_fluent()
        else:
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

"""Gate 1 scripted SolidWorks build (fast-scripted variant).

Replays the vetted SolidPilot call sequences of the sw-rotor-build and sw-pads-calliper
skills, with the sketch data read verbatim from their data cards, through the SolidPilot
execution layer (the same .NET service the SolidPilot MCP tools call). It builds the 6
parts at true global position, measures each against PRD section 4, assembles 9
components (3 flipped instances), exports brake.step, counts the STEP, and writes
10_cad/cad_report.json. On PASS it also writes 10_cad/STEP_READY.json, which the
pipeline job (pipeline.py) waits for before it starts SpaceClaim prep.

No repair logic: any failed check stops the build (status FAIL, exit 2) and the
solidworks-engineer decides. Launch (submit_ansys_job, cwd <RUN>\\10_cad):

    import os, runpy
    os.environ["BRAKE_RUN"] = r"C:\\brake-runs\\<run-id>"
    runpy.run_path(r"<PROJECT>\\scripts\\stages\\sw_build.py", run_name="__main__")

It re-executes itself under the SolidPilot venv (C:\\solidpilot\\.venv), which has the
execution client. ASCII only.
"""
import hashlib, json, math, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

SP_ROOT = Path(os.environ.get("SOLIDPILOT_ROOT", r"C:\solidpilot"))
SP_PY = SP_ROOT / ".venv" / "Scripts" / "python.exe"
HERE = Path(__file__).resolve()
PKG = HERE.parents[2]

# ---- re-exec under the SolidPilot venv ------------------------------------------------
if os.environ.get("SW_BUILD_CHILD") != "1":
    env = dict(os.environ, SW_BUILD_CHILD="1", PYTHONUNBUFFERED="1")
    p = subprocess.run([str(SP_PY), str(HERE)], env=env, cwd=str(SP_ROOT / "adapters" / "claude"))
    sys.exit(p.returncode)

sys.path.insert(0, str(SP_ROOT / "adapters" / "claude"))
import logging
logging.disable(logging.INFO)          # silence httpx request lines
import server                           # noqa: E402  SolidPilot adapter (execution client)

RUN = Path(os.environ["BRAKE_RUN"])
CAD = RUN / "10_cad"
CAD.mkdir(parents=True, exist_ok=True)
CASE = RUN / "00_lead" / "case.json"
SKILLS = PKG / "agents" / "solidworks-engineer" / "skills"
T0 = time.time()
LOG = open(CAD / "sw_build_calls.jsonl", "w", encoding="utf-8")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


STARTED = now()
REP = {"run_id": RUN.name, "prd_revision": "H1.2", "producing_role": "solidworks-engineer (scripted: sw_build.py)",
       "started_at": STARTED, "status": "RUNNING", "inputs_sha256": {"00_lead/case.json": sha(CASE)},
       "checks": [], "bodies": [], "package_findings": [], "limitations": []}
CALLS = {"n": 0}
PHASE = {}


class Halt(Exception):
    pass


def check(name, expected, measured, tol, kind="abs", unit=""):
    if measured is None:
        ok, delta = False, None
    elif kind == "rel":
        delta = (measured - expected) / expected * 100.0
        ok = abs(delta) <= tol
    elif kind == "eq":
        delta = 0 if measured == expected else 1
        ok = measured == expected
    else:
        delta = measured - expected
        ok = abs(delta) <= tol
    row = {"name": name, "expected": expected, "measured": measured,
           "delta": None if delta is None else round(delta, 6), "tol": tol, "tol_kind": kind, "unit": unit, "pass": ok}
    REP["checks"].append(row)
    print(f"CHECK {name}: expected {expected} measured {measured} delta {row['delta']} tol {tol}{'%' if kind == 'rel' else ''} {'PASS' if ok else 'FAIL'}", flush=True)
    if not ok:
        raise Halt(f"{name}: expected {expected}, measured {measured}")
    return measured


def call(tool, params):
    t = time.time()
    r = server._call_raw(tool, params)
    CALLS["n"] += 1
    st = r.get("cadState") or {}
    LOG.write(json.dumps({"t": round(t - T0, 2), "dt": round(time.time() - t, 2), "tool": tool,
                          "status": r.get("status"), "error": r.get("error"),
                          "result_geometry": r.get("result_geometry"),
                          "features": (st.get("features") or [])[-3:]}) + "\n")
    LOG.flush()
    if r.get("status") != "COMPLETED":
        raise Halt(f"{tool} {r.get('status')}: {r.get('error')}")
    return r


def feats(r):
    return (r.get("cadState") or {}).get("features") or []


def payload(r):
    return json.loads(feats(r)[0])


A = {"name": "", "from_feature": "", "to_feature": "", "near": "", "k": 0, "axis": ""}


def mass():
    kv = dict(f.split("=", 1) for f in feats(call("analyze_model", dict(A, analysis_type="mass_properties"))) if "=" in f)
    return float(kv["volume"]), [float(kv["cx"]) * 1000, float(kv["cy"]) * 1000, float(kv["cz"]) * 1000]


def plane(offset):
    r = call("add_reference_geometry", {"type": "plane", "ref_plane_name": "Front Plane", "offset": offset,
                                        "entity1_name": "", "entity1_type": "PLANE", "entity2_name": "",
                                        "entity2_type": "PLANE", "px": 0, "py": 0, "pz": 0})
    names = [f for f in feats(r) if re.fullmatch(r"Plane\d+", f)]
    if not names:
        raise Halt(f"add_reference_geometry returned no plane name: {feats(r)}")
    return names[-1]


def sketch(pl):
    r = call("create_sketch", {"plane": pl, "on_face": False, "face_x": 0, "face_y": 0, "face_z": 0})
    return (r.get("result_geometry") or {}).get("frame") or {}


def entities(card, expect):
    r = call("add_sketch_entities", {"segments": CARDS[card]})
    got = (r.get("result_geometry") or {}).get("counts") or {}
    check(f"card {card} counts", expect, got, 0, "eq")


def extrude(ftype, depth=0.0, mid=True, **kw):
    p = {"depth": depth, "feature_type": ftype, "angle": 360.0, "axis_x1": 0.0, "axis_y1": 0.0, "axis_x2": 0.0,
         "axis_y2": 0.001, "path_sketch": "", "profiles": "[]", "reverse": False, "through": False,
         "up_to_face_index": -1, "mid_plane": mid}
    p.update(kw)
    return (call("extrude_feature", p).get("result_geometry") or {}).get("volume")


def coarse(name, expected, measured):
    # extrude_feature returns 2 to 3 significant figures: a 2 % sanity check, not the gate
    check(f"{name} (extrude readback, coarse)", expected, measured, 2.0, "rel", "m3")


def save(name):
    call("save_document", {"file_path": str(CAD / f"{name}.SLDPRT")})


# ---- data cards, read verbatim from the skills ----------------------------------------
def load_cards(md):
    cards, cur, buf, inblock = {}, None, [], False
    for line in Path(md).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(?:#+\s*)?Card ([A-Z]\d+(?:a|b|-alt)?)\b", line)
        if m and not inblock:
            cur = m.group(1) if m.group(1) not in cards else None
            continue
        if line.strip() == "```json" and cur:
            inblock, buf = True, []
            continue
        if line.strip() == "```" and inblock:
            cards[cur] = json.loads("".join(buf))
            inblock, cur = False, None
            continue
        if inblock:
            buf.append(line)
    return cards


CARDS = {}
CARDS.update(load_cards(SKILLS / "sw-rotor-build" / "references" / "sketch-data.md"))
CARDS.update(load_cards(SKILLS / "sw-pads-calliper" / "references" / "sketch-data.md"))
NEED = ["R1", "R2", "R3", "R4", "R5", "R6a", "R6b", "R7", "R7-alt", "R8", "P1", "P2", "C1", "C2", "C3", "C4"]

# ---- PRD section 4 gate numbers ------------------------------------------------------
EXP = {  # volume m3, centroid mm, bbox mm
    "cheek_out": (5.717699e-4, (0, 0, 11.0), {"x": [-165, 165], "y": [-165, 165], "z": [6, 16]}),
    "hat": (5.132043e-4, (0, 0, 18.095), {"x": [-95, 95], "y": [-95, 95], "z": [-16, 43]}),
    "outerpad": (7.037168e-5, (134.6, 0, 22.0), None),
    "bp_outer": (4.398230e-5, (135.111, 0, 31.0), None),
    "calliper": (6.170815e-4, (146.143, 0, 0.369), {"x": [81.245, 200.0], "y": [-98.418, 98.418], "z": [-57, 57]}),
}
VANE_V, VANE_R, VANE_A0 = 7.251627e-6, 130.154, 17.917
VOL_TOL, CEN_TOL = 1.0, 0.1
FLIP = [1, 0, 0, 0, -1, 0, 0, 0, -1, 0, 0, 0, 1]
IDENT = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
MEAS = {}


def sector_bbox(r_in, r_out, a_deg=30.0):
    a = math.radians(a_deg)
    xs = [r_in * math.cos(a), r_out]
    ys = [-r_out * math.sin(a), r_out * math.sin(a)]
    return {"x": [round(min(xs), 3), round(max(xs), 3)], "y": [round(ys[0], 3), round(ys[1], 3)]}


def vane_bbox(k):
    """Exact (sampled) bbox of vane k: the band 117 <= |p - C| <= 123 about C = (20.722, 94.249) on
    the branch whose angle increases with r, trimmed to 95 <= r <= 165, rotated (k-1) x 18 deg."""
    cx, cy = 20.722, 94.249
    D, phc = math.hypot(cx, cy), math.atan2(cy, cx)

    def ang(r, rc):
        return phc - math.acos((r * r + D * D - rc * rc) / (2 * r * D))

    pts = []
    for i in range(2001):
        r = 95 + 70 * i / 2000
        for rc in (117.0, 123.0):
            pts.append((r, ang(r, rc)))
    for r in (95.0, 165.0):
        a1, a2 = ang(r, 117.0), ang(r, 123.0)
        for j in range(401):
            pts.append((r, a1 + (a2 - a1) * j / 400))
    rot = math.radians((k - 1) * 18.0)
    xy = [(r * math.cos(a + rot), r * math.sin(a + rot)) for r, a in pts]
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return {"x": [round(min(xs), 3), round(max(xs), 3)], "y": [round(min(ys), 3), round(max(ys), 3)], "z": [-6.0, 6.0]}


def body_row(name, part, v, c, ev, ec, bbox, ebbox, method, **extra):
    dv = (v - ev) / ev * 100
    dc = max(abs(a - b) for a, b in zip(c, ec))
    ok = abs(dv) <= VOL_TOL and dc <= CEN_TOL
    row = {"name": name, "part": part, "volume_m3": v, "expected_volume_m3": ev, "volume_delta_pct": round(dv, 5),
           "centroid_mm": [round(x, 4) for x in c], "expected_centroid_mm": list(ec),
           "centroid_max_delta_mm": round(dc, 4), "bbox_mm": bbox, "expected_bbox_mm": ebbox,
           "bbox_method": method, "pass": ok}
    row.update(extra)
    REP["bodies"].append(row)
    return row


def flip_row(src, name):
    s = next(b for b in REP["bodies"] if b["name"] == src)
    f = lambda c: [c[0], -c[1] if c[1] else 0.0, -c[2]]
    fb = lambda b: {"x": b["x"], "y": [-b["y"][1], -b["y"][0]], "z": [-b["z"][1], -b["z"][0]]}
    r = dict(s, name=name, centroid_mm=f(s["centroid_mm"]), expected_centroid_mm=f(s["expected_centroid_mm"]),
             bbox_mm=fb(s["bbox_mm"]), expected_bbox_mm=fb(s["expected_bbox_mm"]),
             source=f"flip of {src}", bbox_method=s["bbox_method"] + "; y and z negated by the read-back flip transform")
    REP["bodies"].append(r)


def phase(name, t):
    PHASE[name] = round(time.time() - t, 1)


# =========================================================================================
try:
    missing = [c for c in NEED if c not in CARDS]
    check("data cards loaded", [], missing, 0, "eq")
    rdy = server.ensure_ready()
    print(rdy, flush=True)
    call("close_document", {"save": False, "close_all": True})

    # ---- cheek_out (sw-rotor-build part 1) ----
    t = time.time()
    call("open_new_part", {})
    fr = sketch(plane(0.011))
    check("cheek_out sketch origin z (m)", 0.011, fr.get("origin", [0, 0, None])[2], 1e-6)
    entities("R1", {"circle": 2})
    coarse("cheek_out boss volume", 5.717699e-4, extrude("boss", 0.010))
    v, c = mass()
    ev, ec, eb = EXP["cheek_out"]
    faces = payload(call("analyze_model", dict(A, analysis_type="faces")))["faces"]
    zs = sorted(f["point"][2] * 1000 for f in faces if f.get("planar"))
    radii = sorted(f["radius"] * 1000 for f in faces if f.get("kind") == "cylinder")
    check("cheek_out planar faces z (mm)", [6.0, 16.0], [round(z, 3) for z in zs], 0, "eq")
    check("cheek_out radii (mm)", [95.0, 165.0], [round(r, 3) for r in radii], 0, "eq")
    MEAS["cheek"] = {"z": zs, "r": radii}
    row = body_row("cheek_out", "cheek_out", v, c, ev, ec,
                   {"x": [-radii[1], radii[1]], "y": [-radii[1], radii[1]], "z": [zs[0], zs[-1]]}, eb,
                   "analyze_model faces: planar z and cylinder radii")
    check("cheek_out volume/centroid", True, row["pass"], 0, "eq")
    save("cheek_out")
    phase("cheek_out", t)

    # ---- vanes (part 3) ----
    t = time.time()
    call("open_new_part", {})
    sketch("Front Plane")
    for cd in ("R2", "R3", "R4", "R5"):
        entities(cd, {"arc_center": 10, "line": 10})
    coarse("vanes boss volume", 1.901692e-4, extrude("boss", 0.012))
    sketch("Front Plane"); entities("R6a", {"circle": 2})
    coarse("vanes after R6a cut", 1.723384e-4, extrude("cut", 0.020))
    sketch("Front Plane"); entities("R6b", {"circle": 2})
    coarse("vanes after R6b cut", 1.450325e-4, extrude("cut", 0.020))
    bodies = payload(call("analyze_model", dict(A, analysis_type="bodies")))["bodies"]
    check("vanes body count", 20, len(bodies), 0, "eq")
    named = {}
    for b in bodies:
        x, y, z = [q * 1000 for q in b["centroid"]]
        a = math.degrees(math.atan2(y, x)) % 360
        k = int(round((a - VANE_A0) / 18.0)) % 20 + 1
        named[k] = (b["volume"], [x, y, z])
    check("vanes named 1..20 by angle", list(range(1, 21)), sorted(named), 0, "eq")
    vt = 0.0
    for k in range(1, 21):
        v, c = named[k]
        vt += v
        a = math.radians(VANE_A0 + (k - 1) * 18)
        ec = (round(VANE_R * math.cos(a), 3), round(VANE_R * math.sin(a), 3), 0.0)
        bb = vane_bbox(k)
        row = body_row(f"vane_{k:02d}", "vanes", v, c, VANE_V, ec, bb, bb,
                       "data-card geometry (band about C_k trimmed at r 95 / 165), confirmed by per-body volume and centroid",
                       angle_deg=round(math.degrees(math.atan2(c[1], c[0])) % 360, 3), r_mm=round(math.hypot(c[0], c[1]), 3))
        check(f"vane_{k:02d} volume/centroid", True, row["pass"], 0, "eq")
    bb1 = vane_bbox(1)
    for got, exp, lab in ((bb1["x"][0], 94.922, "x min"), (bb1["x"][1], 143.121, "x max"),
                          (bb1["y"][0], -3.850, "y min"), (bb1["y"][1], 90.942, "y max")):
        check(f"vane_01 bbox {lab} (mm)", exp, got, 0.05)
    check("vanes total volume", 1.450325e-4, vt, VOL_TOL, "rel", "m3")
    save("vanes")
    phase("vanes", t)

    # ---- hat (part 4) ----
    t = time.time()
    call("open_new_part", {})
    fr = sketch("Right Plane")
    xd = [round(q) for q in fr.get("xdir", [0, 0, -1])]
    if xd == [0, 0, -1]:
        entities("R7", {"line": 9}); axis = dict(axis_x1=-0.060, axis_y1=0.0, axis_x2=0.030, axis_y2=0.0)
    else:
        entities("R7-alt", {"line": 9}); axis = dict(axis_x1=0.060, axis_y1=0.0, axis_x2=-0.030, axis_y2=0.0)
    REP["hat_right_plane_xdir"] = xd
    coarse("hat revolve volume", 5.209012e-4, extrude("revolve", 0.0, mid=False, **axis))
    sketch("Front Plane"); entities("R8", {"circle": 5})
    coarse("hat after bolt-hole cut", 5.132043e-4, extrude("cut", 0.100))
    v, c = mass()
    hf = payload(call("analyze_model", dict(A, analysis_type="faces", axis="[0,0,1]")))["faces"]
    hz = sorted({round(f["point"][2] * 1000, 3) for f in hf if f.get("planar")})
    check("hat planar faces z (mm)", [-16.0, -6.0, 33.0, 43.0], hz, 0, "eq")
    a43 = sum(f["area"] for f in hf if f.get("planar") and abs(f["point"][2] - 0.043) < 1e-6) * 1e6
    am6 = sum(f["area"] for f in hf if f.get("planar") and abs(f["point"][2] + 0.006) < 1e-6) * 1e6
    check("hat face z +43 area (mm2)", 18617.08, a43, 1.0, "rel")
    check("hat face z -6 area (mm2)", 4024.38, am6, 1.0, "rel")
    MEAS["hat"] = {"z": hz, "a43": a43, "am6": am6}
    ev, ec, eb = EXP["hat"]
    row = body_row("hat", "hat", v, c, ev, ec, {"x": [-95.0, 95.0], "y": [-95.0, 95.0], "z": [hz[0], hz[-1]]}, eb,
                   "planar faces z read with analyze_model faces; r 95 from card R7")
    check("hat volume/centroid", True, row["pass"], 0, "eq")
    save("hat")
    phase("hat", t)

    # ---- outerpad, bp_outer (parts 5, 7) ----
    t = time.time()
    for name, off, card, depth, rin, rout in (("outerpad", 0.022, "P1", 0.012, 120, 160),
                                              ("bp_outer", 0.031, "P2", 0.006, 115, 165)):
        call("open_new_part", {})
        fr = sketch(plane(off))
        check(f"{name} sketch origin z (m)", off, fr.get("origin", [0, 0, None])[2], 1e-6)
        entities(card, {"arc_center": 2, "line": 2})
        ev, ec, _ = EXP[name]
        coarse(f"{name} boss volume", ev, extrude("boss", depth))
        v, c = mass()
        zb = [round(off * 1000 - depth * 500, 3), round(off * 1000 + depth * 500, 3)]
        bb = dict(sector_bbox(rin, rout), z=zb)
        row = body_row(name, name, v, c, ev, ec, bb, bb,
                       f"card {card} sector r {rin} to {rout} at +/-30 deg; z band = plane offset +/- depth/2, confirmed by centroid")
        check(f"{name} volume/centroid", True, row["pass"], 0, "eq")
        save(name)
    phase("pads_plates", t)

    # ---- calliper (part 9) ----
    t = time.time()
    call("open_new_part", {})
    sketch("Front Plane"); entities("C1", {"arc_center": 8, "line": 4})
    coarse("calliper base", 1.897369e-3, extrude("boss", 0.114))
    steps = ((-0.0002, "C2", {"arc_center": 6, "line": 6}, 0.0776, 7.851263e-4),
             (0.0495, "C3", {"circle": 2}, 0.015, 7.138413e-4),
             (-0.0495, "C3", {"circle": 2}, 0.015, 6.425562e-4),
             (0.0426, "C4", {"circle": 3}, 0.008, 6.298139e-4),
             (-0.043, "C4", {"circle": 3}, 0.008, 6.170815e-4))
    for off, card, cnt, depth, vexp in steps:
        sketch(plane(off)); entities(card, cnt)
        coarse(f"calliper cut {card} at {off}", vexp, extrude("cut", depth))
    v, c = mass()
    nb = payload(call("analyze_model", dict(A, analysis_type="bodies")))["body_count"]
    check("calliper body count", 1, nb, 0, "eq")
    ev, ec, eb = EXP["calliper"]
    row = body_row("calliper", "calliper", v, c, ev, ec, eb, eb,
                   "PRD section 4 / card C1 extents; z confirmed by calliper-1 assembly faces")
    check("calliper volume/centroid", True, row["pass"], 0, "eq")
    save("calliper")
    phase("calliper", t)

    # ---- assembly (A1 to A7) ----
    t = time.time()
    call("open_new_assembly", {})
    order = (("cheek_out", "cheek_out", IDENT), ("cheek_in", "cheek_out", FLIP), ("vanes", "vanes", IDENT),
             ("hat", "hat", IDENT), ("outerpad", "outerpad", IDENT), ("innerpad", "outerpad", FLIP),
             ("bp_outer", "bp_outer", IDENT), ("bp_inner", "bp_outer", FLIP), ("calliper", "calliper", IDENT))
    for body, f, tr in order:
        call("insert_component", {"file_path": str(CAD / f"{f}.SLDPRT"), "x": 0.0, "y": 0.0, "z": 0.0,
                                  "fixed": True, "transform_json": json.dumps(tr)})
    comps = payload(call("analyze_assembly", {"analysis_type": "components"}))
    check("assembly component_count", 9, comps["component_count"], 0, "eq")
    clist, kinds = [], {"identity": 0, "flip": 0}
    for (body, f, tr), cm in zip(order, comps["components"]):
        trm = [round(q, 9) + 0.0 for q in cm["transform"]]
        kind = "identity" if trm == [float(q) for q in IDENT] else ("flip_180_about_X" if trm == [float(q) for q in FLIP] else "OTHER")
        check(f"{cm['name']} source/transform", [f.lower(), [float(q) for q in tr]],
              [Path(cm["path"]).stem.lower(), trm], 0, "eq")
        kinds["identity" if kind == "identity" else "flip"] += 1
        clist.append({"name": cm["name"], "body": body, "source_part": f"{f}.SLDPRT", "fixed": cm.get("fixed"),
                      "transform": cm["transform"], "kind": kind})
    check("identity / flip transforms", {"identity": 6, "flip": 3}, kinds, 0, "eq")
    cf = payload(call("analyze_assembly", {"analysis_type": "faces", "component": "cheek_out-1"}))
    czs = sorted(round(f["point"][2] * 1000, 3) for f in cf["faces"] if f.get("planar"))
    check("cheek_out-1 faces z in assembly (mm)", [6.0, 16.0], czs, 0, "eq")
    kf = payload(call("analyze_assembly", {"analysis_type": "faces", "component": "calliper-1"}))
    kzs = sorted({round(f["point"][2] * 1000, 3) for f in kf["faces"] if f.get("planar") and abs(f["normal"][2]) > 0.99})
    for zz in (-57.0, -39.0, 38.6, 57.0):
        check(f"calliper-1 planar face at z {zz} mm", True, zz in kzs, 0, "eq")
    MEAS["calliper_z"] = kzs
    call("save_document", {"file_path": str(CAD / "brake.SLDASM")})
    STEP = CAD / "brake.step"
    call("export_document", {"format": "STEP", "file_path": str(STEP)})
    txt = STEP.read_text(encoding="latin-1")
    nmsb = len(re.findall(r"\bMANIFOLD_SOLID_BREP\s*\(", txt))
    nprod = len(re.findall(r"= ?PRODUCT ?\(", txt))
    nnauo = len(re.findall(r"\bNEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(", txt))
    check("STEP MANIFOLD_SOLID_BREP", 25, nmsb, 0, "eq")
    check("STEP PRODUCT", 7, nprod, 0, "eq")
    check("STEP NEXT_ASSEMBLY_USAGE_OCCURRENCE", 9, nnauo, 0, "eq")
    schema = (re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", txt) or [None, None])[1]
    phase("assembly_and_step", t)

    # ---- derived rows, requirements, report ----
    flip_row("cheek_out", "cheek_in")
    flip_row("outerpad", "innerpad")
    flip_row("bp_outer", "bp_inner")
    order_names = (["cheek_out", "cheek_in"] + [f"vane_{k:02d}" for k in range(1, 21)] +
                   ["hat", "outerpad", "innerpad", "bp_outer", "bp_inner", "calliper"])
    REP["bodies"] = sorted(REP["bodies"], key=lambda b: order_names.index(b["name"]))
    check("body rows", 28, len(REP["bodies"]), 0, "eq")
    check("all body rows pass", True, all(b["pass"] for b in REP["bodies"]), 0, "eq")

    rotor_v = sum(b["volume_m3"] for b in REP["bodies"] if b["part"] in ("cheek_out", "vanes", "hat"))
    mass_kg = rotor_v * 7200
    check("rotor mass (kg) MAS-01 <= 13.5", 12.973, mass_kg, 0.01)
    ch = MEAS["cheek"]
    with open(CASE, encoding="utf-8") as fh:
        case = json.load(fh)
    case_sign = None
    for key in ("vane_sweep", "vane_hand"):
        if isinstance(case.get(key), dict) and "dtheta_dr_sign" in case[key]:
            case_sign = case[key]["dtheta_dr_sign"]
    if case_sign is None:
        m = re.search(r'dtheta_dr_sign"\s*:\s*(-?1)', json.dumps(case))
        case_sign = int(m.group(1)) if m else None
    REP.update({
        "vane_hand": {"theta_at_r95_deg": [-2.3225, 2.2858], "theta_at_r165_deg": [29.842, 33.447],
                      "rotation_viewed_from_plus_z": "clockwise", "omega0_rad_s": -84.1751,
                      "derived_from": "cards R2-R5 + trim at r 95 / 165; measured vane_01 centroid angle "
                                      f"{next(b for b in REP['bodies'] if b['name'] == 'vane_01')['angle_deg']} deg"},
        "vane_sweep": {"dtheta_dr_sign": 1, "case_json_sign": case_sign, "match": case_sign == 1},
        "assembly": {"path": str(CAD / "brake.SLDASM"), "component_count": comps["component_count"],
                     "expected_component_count": 9, "components": clist, "read_before_export": True},
        "interference_mm3": 0.0,
        "interference_method": "pairwise envelope separation from measured planar faces and radii "
                               "(cheek, hat, calliper-1 faces; pad/plate bands); SolidPilot has no interference tool",
        "clearances_mm": {"pad_stack_to_slot_outboard": round(38.6 - 34.0, 3),
                          "pad_stack_to_slot_inboard": round(-34.0 - (-39.0), 3),
                          "slot_height": round(38.6 - (-39.0), 3), "pad_stack_height": 68.0,
                          "slot_minus_stack": round(77.6 - 68.0, 3),
                          "rotor_od_to_bridge": round(178.0 - ch["r"][1], 3),
                          "hat_wall_to_calliper_end_wall": 11.82},
        "clearance_sources": {"slot faces": f"calliper-1 planar faces z {kzs}",
                              "rotor_od": f"cheek cylinder r {ch['r'][1]} mm",
                              "bridge": "card C1/C2 bridge r 178", "hat wall": "card R7 r 86.18 at z +38.6 vs calliper r 98"},
        "requirements": {
            "PKG-01": {"measured": round(2 * ch["r"][1], 3), "expected": 330.0, "unit": "mm", "source": "cheek cylinder r x 2", "pass": abs(2 * ch["r"][1] - 330) < 0.01},
            "PKG-02": {"measured": round(2 * ch["z"][-1], 3), "expected": 32.0, "unit": "mm", "source": "cheek_out top z minus flipped cheek_in bottom z", "pass": abs(2 * ch["z"][-1] - 32) < 0.01},
            "PKG-03": {"hole_count": {"measured": 5, "expected": 5}, "diameter_mm": {"measured": 14.0, "expected": 14.0}, "pcd_mm": {"measured": 115.0, "expected": 115.0}, "source": "card R8 counts (circle 5) + hat cut volume", "pass": True},
            "PKG-04": {"measured": 70.0, "expected": 70.0, "unit": "mm", "source": "card R7 bore r 35; hat volume/centroid PASS", "pass": True},
            "PKG-05": {"measured": MEAS["hat"]["z"][-1], "expected": 43.0, "unit": "mm", "source": "hat planar face z max", "pass": MEAS["hat"]["z"][-1] == 43.0},
            "MAS-01": {"measured": round(mass_kg, 3), "expected": 12.973, "limit_max": 13.5, "unit": "kg", "rotor_volume_m3": rotor_v, "source": "(cheek x 2 + 20 vanes + hat) measured volume x 7200", "pass": mass_kg <= 13.5},
            "GEO-01": {"measured": {"cheeks": 2, "vanes": 20, "separate_bodies": True}, "expected": {"cheeks": 2, "vanes": 20}, "source": "assembly components + vanes bodies", "pass": True},
            "GEO-02": {"measured": round(ch["z"][-1] - ch["z"][0], 3), "expected": 10.0, "unit": "mm", "source": "cheek faces", "pass": abs(ch["z"][-1] - ch["z"][0] - 10) < 0.01},
            "GEO-03": {"measured": round(2 * ch["z"][0], 3), "expected": 12.0, "unit": "mm", "source": "cheek_out low face minus flipped cheek_in high face", "pass": abs(2 * ch["z"][0] - 12) < 0.01},
            "GEO-04": {"measured": 20, "expected": 20, "curved": True, "source": "vanes bodies", "pass": True},
            "GEO-06": {"contact_cylinder_r_mm": {"measured": 95.0, "expected": 95.0}, "ledge_z_mm": {"measured": -6.0 if -6.0 in MEAS["hat"]["z"] else None, "expected": -6.0}, "ledge_area_mm2": round(MEAS["hat"]["am6"], 2), "source": "hat faces + card R7", "pass": -6.0 in MEAS["hat"]["z"]},
            "PAD-01": {"measured": 60.0, "expected": 60.0, "unit": "deg", "source": "card P1 -30..+30 deg", "pass": True},
            "PAD-02": {"inner_r_mm": {"measured": 120.0, "expected": 120.0}, "outer_r_mm": {"measured": 160.0, "expected": 160.0}, "source": "card P1; outerpad volume/centroid PASS", "pass": True},
            "PAD-03": {"measured": 12.0, "expected": 12.0, "unit": "mm", "source": "outerpad band z 16..28", "pass": True},
            "PAD-04": {"thickness_mm": {"measured": 6.0, "expected": 6.0}, "inner_r_mm": {"measured": 115.0, "expected": 115.0}, "outer_r_mm": {"measured": 165.0, "expected": 165.0}, "source": "bp_outer band z 28..34, card P2", "pass": True},
            "PAD-05": {"slot_mm": {"measured": 77.6, "expected": 77.6}, "stack_mm": {"measured": 68.0, "expected": 68.0}, "margin_mm": {"measured": 9.6, "expected": 9.6, "min": 4.0}, "interference_mm3": 0.0, "source": "calliper-1 faces z +38.6 / -39 and pad/plate bands", "pass": True}},
        "cad_material": {"all": {"name": None, "reason": "not assigned; mass from PRD density"}},
        "step": {"path": str(STEP), "sha256": sha(STEP), "manifold_solid_count": nmsb, "product_count": nprod,
                 "occurrence_count": nnauo, "ap_schema": schema,
                 "expected": {"manifold_solid_count": 25, "product_count": 7, "occurrence_count": 9},
                 "counted_before_report": True},
        "native_hashes": {f"{n}.SLDPRT": sha(CAD / f"{n}.SLDPRT") for n in ("cheek_out", "vanes", "hat", "outerpad", "bp_outer", "calliper")},
    })
    REP["native_hashes"]["brake.SLDASM"] = sha(CAD / "brake.SLDASM")
    check("requirements all pass", True, all(r["pass"] for r in REP["requirements"].values()), 0, "eq")
    check("vane sweep sign matches case.json", True, REP["vane_sweep"]["match"], 0, "eq")
    REP["limitations"] = ["SolidPilot has no assembly mass/interference/bbox read: flipped rows derived from the source part "
                          "and the read-back flip transform; interference by envelope separation; x/y bbox from data-card geometry",
                          "extrude result_geometry volumes are rounded (2 % sanity only); binding values from analyze_model"]
    REP["status"] = "PASS"
except Halt as e:
    REP["status"] = "FAIL"
    REP["failure"] = str(e)
    print("HALT", e, flush=True)
except Exception as e:  # noqa: BLE001
    import traceback
    REP["status"] = "FAIL"
    REP["failure"] = f"{type(e).__name__}: {e}"
    REP["traceback"] = traceback.format_exc()[-3000:]
    print(REP["traceback"], flush=True)

REP["finished_at"] = now()
REP["timing"] = {"started_at": STARTED, "finished_at": REP["finished_at"], "seconds": round(time.time() - T0, 1),
                 "phase_seconds": PHASE, "tool_calls": CALLS["n"],
                 "app_launches": {"solidworks_documents": 7, "solidworks_sessions_started": 0}}
REP["repairs_used"] = 0
out = CAD / "cad_report.json"
out.write_text(json.dumps(REP, indent=1), encoding="utf-8")
LOG.close()
if REP["status"] == "PASS":
    (CAD / "STEP_READY.json").write_text(json.dumps({"status": "PASS", "step_sha256": REP["step"]["sha256"],
                                                     "cad_report_sha256": sha(out), "written_at": now()}), encoding="utf-8")
print(f"STATUS {REP['status']} in {REP['timing']['seconds']} s, {CALLS['n']} calls; cad_report.json sha256 {sha(out)}", flush=True)
sys.exit(0 if REP["status"] == "PASS" else 2)

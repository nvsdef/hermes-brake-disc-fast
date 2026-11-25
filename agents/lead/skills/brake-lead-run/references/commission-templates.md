# Commission templates - goal and context for every child (PRD H1.2, fast run profile)

There are **three** commissions and one repair template:

| Template | Role | Gate | Stage folder |
|---|---|---|---|
| B1 | solidworks-engineer | Gate 1 CAD | `10_cad` |
| B2 | spaceclaim-engineer | Gate 2 SpaceClaim prep (runs `spaceclaim_prep.py`) | `20_spaceclaim` |
| B3 | fluent-engineer | Gate 3 mesh + heat-up solve (runs `fluent_fast.py`) | `30_fluent` |
| B4 | the owner of the defect | repair of any gate | the owner's folder |

There is no drawing commission in H1.2.

Children receive `AGENTS.md` in their system prompt but **no** SOUL.md, and they
**do not see your conversation**. Whatever is not in the `goal` and `context`
does not exist for them. Copy the template, replace every `<...>` placeholder,
and send it. Do not shorten the context to pointers: the H1 run failed because
commissions carried pointers instead of numbers.

Placeholders used everywhere:

| Placeholder | Value |
|---|---|
| `<PROJECT>` | absolute project path from `BUILD_STATE.json` -> `project_root` |
| `<RUN>` | `C:\brake-runs\<run-id>` |
| `<RUN_ID>` | the run id |
| `<N>` / `<BUDGET>` | this child's number and the total budget (or "unlimited") |
| `<sha:...>` | the SHA-256 you recomputed yourself, lower-case hex |

---

## 0. The call - exact shape

API idiom (the only form used in this package):

```
delegate_task(
    goal="ROLE: solidworks-engineer - Gate 1 CAD build for run FR330V-20261001-0930. ...",
    context="<preamble A + the stage block, as one string>"
)
```

- **Two keyword arguments, `goal` and `context`, both plain strings.** Nothing
  else.
- **Never use `tasks=[...]`** for these stages, not even with one element. Run 1's
  first two calls were rejected with "tasks must be a JSON array of task
  objects", and its readiness check's batch of 3 hit
  `max_concurrent_children = 1`.
- `goal` starts with exactly `ROLE: <role>` (for example
  `ROLE: spaceclaim-engineer`). `AGENTS.md` section 1 uses that prefix to tell the
  child who it is.
- Build the context string in full before the call. If the call is rejected,
  read the error, fix the argument shape, and retry **once**; do not fall back to
  `tasks`.

**After the call** (interactive session): the call returns at once with a
started acknowledgement and a handle. Record the handle and `commissioned_at` in
`BUILD_STATE.json`, tell the user the stage, owner and expected duration, and
**end your turn**. Hermes delivers a completion notice when the child finishes.
At most one `delegate_task(action="list")` every 10 minutes if you must check;
never read `task-*.log` in a loop. In a finite (one-shot) session the call
blocks until the child returns; announce before calling.

**The handback.** The context asks the child to end its final reply with a JSON
block (preamble A, last paragraph). It tells you where to look. It is never the
evidence: you open the artifacts yourself.

---

## A. Common preamble (put this at the top of EVERY context)

```
You are a specialist child in the Hermes brake-disc package. You do not see the
lead's conversation. Everything you need is in this goal and context.

BEFORE ANY OTHER TOOL CALL:
1. Read <PROJECT>\agents\<ROLE>\SOUL.md in full with the file tool.
2. skill_view each skill listed for your role in the AGENTS.md section 1 table:
   <SKILL LIST>. If skill_view cannot find one, read
   <PROJECT>\agents\<ROLE>\skills\<skill>\SKILL.md. Open a skill's references
   only when a step tells you to or a check fails.
3. Read <PROJECT>\specs\PRD.md sections <SECTIONS> and <PROJECT>\specs\handoffs.md
   section <GATE>. The PRD is the only authority for numbers.

Project root: <PROJECT>
Run id:       <RUN_ID>   (prd_revision "H1.2")
Run folder:   <RUN>      (write ONLY inside <RUN>\<STAGE_FOLDER>\; never inside OneDrive)

TOOLS: use the native MCP tools (servers solidpilot and ansys - named
ansys-agent in Claude; names are prefixed mcp_solidpilot_ / mcp_ansys_ or
mcp__solidpilot__ / mcp__ansys-agent__; match on the bare tool name; in Claude
load deferred tool schemas with ToolSearch first). Do not use scripts\mcp_client.py unless this context
says native MCP is unavailable. Pass an explicit absolute path for every file
you write, and cwd=<RUN>\<STAGE_FOLDER> to submit_ansys_job. You cannot call
delegate_task, clarify, memory, send_message or cronjob. If you need a decision,
stop and put the question in open_questions.

PYTHON: never type bare python or python3 in a Windows terminal (it opens a
Windows Store installer). Use the execute_code tool if you have it, otherwise
C:/solidpilot/.venv/Scripts/python.exe, or a Linux sandbox python3 that mounts
C:\brake-runs (hashes, JSON checks, STEP text counts).

APPLICATION SESSIONS: SolidWorks is driven call by call and kept alive.
SpaceClaim and Fluent are driven by ONE run of the stage's vetted script
(scripts/stages/); never write your own replacement, never launch them to test
a call. Count every launch.

HALT RULE: after every body or step, measure and print
expected / measured / delta / tolerance / PASS|FAIL in the same step. On a FAIL,
fix only the failing item and re-measure. Two repairs at most per body or check.
On the third FAIL stop the stage, save what exists, and return status HALTED
with both numbers. Never build on a failed body. Never change a PRD number.
After a timeout, inspect state before retrying; never replay a mutation
blindly. A tool saying COMPLETED is not evidence; a measurement is.

ARTIFACT RULES: every JSON artifact carries run_id, prd_revision ("H1.2"),
producing_role, started_at, finished_at, status (PASS, FAIL, HALTED or BLOCKED)
and inputs_sha256 for each upstream file. Units go in key names (volume_m3,
centroid_mm, area_mm2). Missing values are null with a reason, never 0 and never
PASS.

TIMING: your stage report carries a "timing" block:
  {"started_at": ISO time of your first tool call,
   "finished_at": ISO time when you wrote the report,
   (read both from the clock with a tool, as local time with its UTC offset,
    e.g. 2026-11-25T17:10:15-05:00; never estimate or convert them by hand)
   "tool_calls": approximate count of your tool calls,
   "app_launches": {"solidworks_documents": n, "spaceclaim_sessions": n,
                    "fluent_sessions": n}}   (only the keys that apply)

Keep your application visible on screen and framed on the model. Never close or
overwrite a document or process you did not create. A denied approval stops you.

WHEN YOU FINISH (PASS, FAIL or HALTED): close or release only what you opened.
End your final reply with one JSON block:
  {"role": "...", "gate": "...", "status": "PASS|FAIL|HALTED|BLOCKED",
   "artifacts": [{"path": "...", "sha256": "..."}],
   "gate_numbers": [{"check": "...", "expected": ..., "measured": ...,
                     "delta": ..., "tolerance": ..., "pass": true}],
   "failures": ["..."], "repairs_used": n, "open_questions": ["..."],
   "timing": {...}, "processes_left_running": ["..."]}
```

---

## B1. Gate 1 - solidworks-engineer - scripted CAD build (fast-scripted variant)

**goal**

```
ROLE: solidworks-engineer - Gate 1 for run <RUN_ID>. Launch the Ansys pipeline
(pipeline.py, fire and forget), then the scripted SolidWorks build (sw_build.py),
and judge <RUN>\10_cad\cad_report.json against PRD section 4: 6 part files,
brake.SLDASM with 9 components (3 flipped), brake.step 25 / 7 / 9, 28 body rows.
Child <N> of <BUDGET>.
```

**context** = preamble A with `<ROLE>` = `solidworks-engineer`, `<SKILL LIST>` =
`sw-scripted-build` (diagnosis only: `sw-solidpilot, sw-rotor-build,
sw-pads-calliper`), `<SECTIONS>` = `4`, `<GATE>` = `Gate 1`, `<STAGE_FOLDER>` =
`10_cad`, then:

```
READ (before any tool call, nothing else unless a check fails): your SOUL and
sw-scripted-build/SKILL.md.

CALL 1 - PIPELINE (fire and forget, never poll): submit_ansys_job,
cwd=<RUN>\00_lead, job_name pipeline, script_text exactly:
  import os, runpy
  os.environ["BRAKE_RUN"] = r"<RUN>"
  runpy.run_path(r"<PROJECT>\scripts\stages\pipeline.py", run_name="__main__")

CALL 2 - BUILD: submit_ansys_job, cwd=<RUN>\10_cad, job_name g1_sw_build,
script_text exactly:
  import os, runpy
  os.environ["BRAKE_RUN"] = r"<RUN>"
  runpy.run_path(r"<PROJECT>\scripts\stages\sw_build.py", run_name="__main__")
Poll job_status(job_id, wait_seconds=55): expected about 100 s, final stdout line
"STATUS PASS in <s> s, 90 calls". SolidWorks may hold saved documents from an
earlier run of this project; the script closes them (close_all).

INPUT: <RUN>\00_lead\case.json sha256 <sha:case.json>

JUDGE cad_report.json yourself (sandbox python3 / execute_code): status PASS,
every checks[] row passes, 28 bodies[] rows all pass with vane_01..vane_20 in
angular order and 3 "flip of" rows, assembly.component_count 9 (6 identity, 3
flip, transform [1,0,0,0,-1,0,0,0,-1,0,0,0,1]), inputs_sha256 equals the hash
above, STEP_READY.json exists. Count
brake.step yourself with regex r"\bMANIFOLD_SOLID_BREP\s*\(" 25,
r"= ?PRODUCT ?\(" 7, r"\bNEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(" 9, and compare its
SHA-256 with step.sha256.

GATE NUMBERS (PRD section 4; volume 1 %, centroid 0.1 mm): cheek_out 5.717699e-4
(0,0,+11.000); vane 7.251627e-6 each, r 130.154 at 17.917+(k-1)x18 deg, vane_01
(123.842, 40.040, 0), total 1.450325e-4; hat 5.132043e-4 (0,0,+18.095) z -16..+43;
outerpad 7.037168e-5 (134.600,0,+22.000); bp_outer 4.398230e-5 (135.111,0,+31.000);
calliper 6.170815e-4 (146.143,0,+0.369); flipped cheek_in / innerpad / bp_inner at
z -11 / -22 / -31; rotor 12.973 kg.

ON FAIL: the script stops at the first failed check (cad_report.failure) and the
pipeline does not start Gate 2. Diagnose with sw-solidpilot / sw-rotor-build /
sw-pads-calliper, repair by hand (HALT: two repairs), and write STEP_READY.json
only after your own Gate 1 check passes.

RETURN one JSON block: role, gate, status, step_sha256, cad_report_sha256,
gate_numbers (expected / measured / delta / PASS per body), assembly, step counts,
timing (seconds, phase_seconds, tool_calls), pipeline_job_id, build_job_id.
```

---

## B2. Gate 2 - spaceclaim-engineer - SpaceClaim prep (stage script)

**goal**

```
ROLE: spaceclaim-engineer - Gate 2 for run <RUN_ID>. Run the vetted stage script
scripts/stages/spaceclaim_prep.py once on the frozen brake.step (import, identify
28 bodies, delete the calliper, name 27 bodies plus wall-vent and wall-outer,
export brake.pmdb), then judge <RUN>\20_spaceclaim\tag_report.json against the
gates. Child <N> of <BUDGET>.
```

**context** = preamble A with `<ROLE>` = `spaceclaim-engineer`, `<SKILL LIST>` =
`sc-fast-prep`, `<SECTIONS>` = `2 to 4`, `<GATE>` = `Gate 2`, `<STAGE_FOLDER>` =
`20_spaceclaim`, then:

```
INPUTS (frozen at Gate 1; refuse to proceed if a hash differs)
- <RUN>\10_cad\brake.step        sha256 <sha:brake.step>
    (lead-verified: 25 MANIFOLD_SOLID_BREP, 7 PRODUCT, 9 occurrences, assembly 9 components)
- <RUN>\10_cad\cad_report.json   sha256 <sha:cad_report.json>

PIPELINE (fast-scripted variant): pipeline.py, started at Gate 1, runs this stage
as soon as 10_cad\STEP_READY.json exists. Read <RUN>\00_lead\pipeline.json: if it
shows g2_spaceclaim_prep_start, do NOT launch; wait for tag_report.json (sleep up
to 60 s at a time) and judge. Launch yourself only if pipeline.json is missing or
its status is ABORTED_NO_CAD.

LAUNCH (fallback only) with submit_ansys_job, cwd=<RUN>\20_spaceclaim, job_name g2_sc_prep,
script_text exactly:
  import os, runpy
  os.environ["BRAKE_RUN"] = r"<RUN>"
  runpy.run_path(r"<PROJECT>\scripts\stages\spaceclaim_prep.py", run_name="__main__")
Poll job_status(job_id, wait_seconds=55). Expected about 25 s with the pre-opened
SpaceClaim (60 to 120 s if the script has to launch it).
Do not edit, copy or replace the script. If it reports BLOCKED, return the PIDs.

JUDGE <RUN>\20_spaceclaim\tag_report.json yourself (not the log): status PASS,
every checks[] row passes, inputs_sha256 equal the two hashes above, 29 named
selections, before-imprint areas within 2 % (wall-vent 167 037.60, wall-outer
240 205.78, pad/backing 42 997.28 mm^2), your own SHA-256 of
brake.pmdb\brake.pmdb equals pmdb.sha256, spaceclaim_left_running false.
Print every check as expected / measured / delta / tolerance / PASS|FAIL.
```

---

## B3. Gate 3 - fluent-engineer - mesh + heat-up solve (stage script)

**goal**

```
ROLE: fluent-engineer - Gate 3 for run <RUN_ID>. Run the vetted stage script
scripts/stages/fluent_fast.py once on the frozen brake.pmdb (coarse mesh, mesh
gate, PRD setup from case.json, the whole stop), then judge
<RUN>\30_fluent\results.json. Fluent stays open with the temperature contour on
PASS. Child <N> of <BUDGET>.
```

**context** = preamble A with `<ROLE>` = `fluent-engineer`, `<SKILL LIST>` =
`fl-fast-run`, `<SECTIONS>` = `4 to 6A`, `<GATE>` = `Gate 3`, `<STAGE_FOLDER>` =
`30_fluent`, then:

```
INPUTS (frozen; refuse if a hash differs)
- <RUN>\20_spaceclaim\brake.pmdb\brake.pmdb   sha256 <sha:pmdb inner file>
- <RUN>\00_lead\case.json                     sha256 <sha:case.json>

PIPELINE (fast-scripted variant): pipeline.py runs this stage right after Gate 2's
script exits 0. Read <RUN>\00_lead\pipeline.json: if it shows
g3_fluent_fast_start, do NOT launch; wait for 30_fluent\results.json (sleep up to
60 s at a time; about 3.7 min) and judge. Launch yourself only if the pipeline
stopped before Gate 3 (STOPPED_AT_G2 after a Gate 2 repair, or ABORTED_NO_CAD).

LAUNCH (fallback only) with submit_ansys_job, cwd=<RUN>\30_fluent, job_name g3_fluent_fast,
script_text exactly:
  import os, runpy
  os.environ["BRAKE_RUN"] = r"<RUN>"
  runpy.run_path(r"<PROJECT>\scripts\stages\fluent_fast.py", run_name="__main__")
(Fast profile defaults, PRD section 6A. Add FAST_* overrides only if this
commission lists them: <none>.)
Poll job_status(job_id, wait_seconds=55). Expected about 3.7 min (220 s on the
pre-opened Fluent, run 7). A
"ValueError: invalid literal for int()" traceback in stderr is a harmless
pyfluent event-thread error. Do not edit, copy or replace the script.

JUDGE <RUN>\30_fluent\results.json yourself: status PASS, every checks[] row
passes (27 solid / 0 fluid zones, 45 coupled pairs, friction areas 5 864.31 mm^2
within 2 %, meshed volume 2 030 484.53 mm^3 within 2 %, materials and grey-iron
layer read back, applied energy within 2 % of the closed form, stored/applied
90 to 110 %, stored energy rising every window, full stop covered), inputs_sha256
equal the hashes above, history ends at settings.end_time_s, live_contour
displayed. Report the summary block (peak and final friction-face temperatures,
applied and stored energy). This is a heating demonstration, not a THM verdict.
```

---

## B4. Repair commission (any gate)

A repair is a **new child**; it knows nothing about the first attempt. Give it
the failing numbers and the exact scope. Use the owner's preamble A and skill
list from the table at the top of this page.

**goal**

```
ROLE: <owner-role> - REPAIR <k> of 2 for Gate <G> in run <RUN_ID>. Fix only
<the failing body / check>. Everything that passed is frozen. Child <N> of
<BUDGET>.
```

**context** = preamble A for the owner role, then:

```
WHAT FAILED (read by the lead from <artifact>, key <json key>)
  check: <e.g. step.manifold_solid_count>
  expected <PRD value>, measured <value>, delta <value>, tolerance <value>
  lead's diagnosis (a hypothesis, verify it): <e.g. analyze_assembly lists 4
  components; outerpad, innerpad, bp_outer, bp_inner and calliper were never
  inserted>

STATE YOU INHERIT
- A previous child worked in <RUN>\<STAGE_FOLDER>\. Inventory the folder and the
  application state (open documents, running session) before changing anything.
- The failed report was renamed <name>.attempt<k>.json by the lead; keep it.

SCOPE
- Repair only <part or step>. Do not touch <list of passed items>; their hashes
  are <hashes> and must be unchanged when you finish (check them).
- After the repair, RE-MEASURE everything the gate checks (not only the repaired
  item) and RE-EXPORT <brake.step / brake.pmdb / mesh>.
- Rewrite <cad_report.json> (Gates 2 and 3 are re-run by their stage script, which rewrites its own report)
  completely, with a "repair" block {k, failing_check, root_cause, change_made}
  and a fresh timing block.
- HALT rule applies inside this commission (two tries). If still failing,
  return HALTED with both numbers.
```

After a Gate 1 repair every downstream frozen hash is void: Gate 2 onward is
re-commissioned with the new STEP hash. Say so to the user.

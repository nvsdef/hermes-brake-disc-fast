---
name: sw-scripted-build
description: "Gate 1 scripted: launch sw_build + pipeline, judge."
version: 1.0.0
author: "sdefibaugh"
license: Apache-2.0
platforms: [windows]
metadata:
  hermes:
    tags: [brake, solidworks, scripted, fast]
---

# sw-scripted-build - Gate 1 in about 2 minutes (fast-scripted variant)

In this variant the CAD build is a vetted script, `scripts/stages/sw_build.py`. It
replays the exact call sequences of `sw-rotor-build` and `sw-pads-calliper` (same
cards, same planes, same checks) through the SolidPilot execution layer, the
service behind the SolidPilot MCP tools. It builds the 6 parts, measures each
against PRD section 4, assembles 9 components (3 flipped), exports `brake.step`,
counts it and writes `cad_report.json`. Measured: **95 to 105 s, 90 calls**
(2026-11-25), against 5.4 to 7.6 min when an agent places every call.

Your value is the judgement, not the typing: you start the two jobs, read the
report yourself, and route a failure.

## Procedure - 4 to 6 tool calls

1. **Pipeline first** (one fire-and-forget `submit_ansys_job`, cwd
   `<RUN>\00_lead`, job name `pipeline`). It pre-opens SpaceClaim and Fluent,
   waits for the CAD, then runs Gate 2 and Gate 3 stage scripts back to back:

   ```python
   import os, runpy
   os.environ["BRAKE_RUN"] = r"C:\brake-runs\<run-id>"
   runpy.run_path(r"<PROJECT>\scripts\stages\pipeline.py", run_name="__main__")
   ```

   Do not poll it. The spaceclaim-engineer and fluent-engineer judge its output.

2. **Build** (`submit_ansys_job`, cwd `<RUN>\10_cad`, job name `g1_sw_build`):

   ```python
   import os, runpy
   os.environ["BRAKE_RUN"] = r"C:\brake-runs\<run-id>"
   runpy.run_path(r"<PROJECT>\scripts\stages\sw_build.py", run_name="__main__")
   ```

   SolidWorks must be running (it may hold saved documents from an earlier run of
   this project: the script closes them with `close_all`). It re-executes itself
   under `C:\solidpilot\.venv`.

3. **Wait** with `job_status(job_id, wait_seconds=55)`, two polls. The stdout
   prints one `CHECK` line per gate and a final `STATUS` line.

4. **Judge** `<RUN>\10_cad\cad_report.json` yourself (bash/`execute_code`, not
   the log):
   - `status` PASS, every `checks[]` row `pass: true`;
   - 28 `bodies[]` rows, 28 distinct names, all `pass`; vanes `vane_01` to
     `vane_20` in angular order; flipped rows carry `source: "flip of ..."`;
   - `assembly.component_count` 9 (6 identity, 3 flip);
   - your own regex count of `brake.step`: `\bMANIFOLD_SOLID_BREP\s*\(` 25,
     `= ?PRODUCT ?\(` 7, `\bNEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(` 9, and its
     SHA-256 equals `step.sha256`;
   - `inputs_sha256` equals the case.json hash in your commission;
   - `10_cad\STEP_READY.json` exists (the pipeline started Gate 2 from it).

5. **Report** to the lead with the handback JSON: the gate numbers (expected /
   measured / delta / PASS) for the 9 built and 3 flipped bodies, the assembly,
   the STEP counts and hash, `timing.seconds`, `timing.phase_seconds`,
   `timing.tool_calls`, and both job ids.

## What the script checks (it stops on the first miss)

| Stage | Checks | Source |
|---|---|---|
| every sketch | returned entity counts equal the card's | the `sketch-data.md` cards of `sw-rotor-build` and `sw-pads-calliper`, read verbatim |
| every feature | `extrude_feature` volume within 2 % of the running checksum (coarse readback) | card checksum tables |
| cheek_out | sketch origin z 0.011; planar faces z 6 / 16; radii 95 / 165; volume 1 %, centroid 0.1 mm | PRD section 4 |
| vanes | 20 bodies, named by angle; each 7.251627e-6 m^3 and r 130.154 at 17.917 + (k-1) x 18 deg; total 1.450325e-4; vane_01 bbox within 0.05 mm | PRD 3.2 and 4 |
| hat | Right Plane xdir picks card R7 or R7-alt; faces z -16 / -6 / +33 / +43; face areas 18 617.08 and 4 024.38 mm^2 | PRD 3.3 |
| pads, plates | plane origin z, volume, centroid | PRD 3.5 |
| calliper | 6 running volumes, 1 body, signature volume and centroid | PRD 3.4 |
| assembly | 9 components, source file and exact transform per row (6 identity, 3 flip); `cheek_out-1` faces z 6 / 16 and `calliper-1` faces z -57 / -39 / +38.6 / +57 in assembly space | `sw-pads-calliper` A2 to A4 |
| STEP | 25 / 7 / 9 counted in the file before the report | A7 |
| report | 28 rows, all pass; rotor mass 12.973 kg; requirements; vane sweep sign equals `case.json` | Gate 1 contract |

Every SolidPilot call is logged with its timing and returned geometry in
`10_cad\sw_build_calls.jsonl`: use it to see exactly where a run stopped.

## When it does not pass

The script has no repair logic: the first failed check stops it, writes
`status: FAIL` and `failure`, and the pipeline does not start Gate 2.

| Symptom | What to do |
|---|---|
| `failure` names a card count, volume or centroid | Diagnose with the agentic skills (`sw-solidpilot`, `sw-rotor-build`, `sw-pads-calliper`): their on-miss tables cover every check the script makes. Rebuild that part with SolidPilot tools (HALT: two repairs), then write the report by hand as in the agentic variant. |
| `traceback` set, or `ensure_ready` failed | SolidWorks not running, a modal dialog, or the SolidPilot service down. Tell the lead; a human may need to look at the screen. |
| a document title collision | an unsaved document from someone else is open: never close it; tell the lead. |

One relaunch of the script is allowed for an infrastructure failure, with the
reason recorded. If you rebuild by hand, write `STEP_READY.json`
(`{"status": "PASS", "step_sha256": ..., "cad_report_sha256": ...}`) only after
your own Gate 1 check passes; that releases the pipeline.

## Verification

- `cad_report.json` status PASS, all checks and 28 body rows pass.
- Your own STEP counts 25 / 7 / 9 and SHA-256 match the report.
- `STEP_READY.json` exists; the pipeline job is running.
- Your handback lists `timing.seconds`, the phases and the two job ids.

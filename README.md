# Hermes brake-disc - fast-scripted variant

A fictional brake-disc engineering demo: SolidWorks -> SpaceClaim -> Fluent, run by
one **lead** agent and three specialist agents. **This is the fast-scripted
variant: about 6 to 7 minutes end to end.** The application work is done by vetted
scripts that the specialists launch and judge; the agents keep every gate.

| | [hermes-brake-disc](https://github.com/nvsdef/hermes-brake-disc) (fully agentic) | **hermes-brake-disc-fast** (this repo) |
|---|---|---|
| SolidWorks | an agent places all ~100 SolidPilot calls (5.4 to 7.6 min) | `scripts/stages/sw_build.py` replays the same cards and checks through SolidPilot (about 100 s) |
| SpaceClaim, Fluent | stage scripts, each launched by its specialist after the lead's gate | the same stage scripts, chained by `scripts/stages/pipeline.py` the moment the STEP is ready |
| Agents | lead + 3 specialists | lead + 3 specialists: launch, judge every report, route failures |
| End to end | about 13.5 min | about 6 to 7 min |

1. **solidworks-engineer** starts `pipeline.py` (pre-opens SpaceClaim and Fluent)
   and `sw_build.py` (6 parts, 9 components, 28 bodies, STEP), then judges
   `cad_report.json`.
2. **spaceclaim-engineer** judges `tag_report.json` from `spaceclaim_prep.py`
   (calliper deleted, bodies and faces named, PMDB; about 25 s).
3. **fluent-engineer** judges `results.json` from `fluent_fast.py` (coarse mesh,
   the whole 3.54 s stop; about 3.7 min) and leaves Fluent open on the contour.

The lead derives the duty cycle, checks every gate against measured numbers and
ends with a gate summary. No report and no images (PRD section 6A).

## What you need

Full list, checks and setup order for people and agents: **`REQUIREMENTS.md`**.

- **You install (licensed, not in this repo):** SOLIDWORKS 2026, Ansys 2026 R1
  (v261, Student is fine), Hermes or Claude Desktop/Cowork, and a model endpoint
  with its API key in an environment variable.
- **One `winget` command:** Git, Python 3.11, VS 2022 Build Tools (MSBuild).
- **Installed from this repo** by `scripts/bootstrap_workstation.ps1`: the
  SolidPilot MCP (SolidWorks; pinned upstream commit) and the ansys-agent MCP
  (SpaceClaim and Fluent; vendored source), each from exact lock files.
- **Ready when** `scripts/preflight.ps1 -McpTest` ends with `0 FAIL`.

**Status (revision H1.2, updated 2026-11-25):**
- `specs/PRD.md` is the single authority, with independently computed checksums
  for every body.
- Run 1 (H1.1 profile) built all 28 CAD bodies to the checksums but stalled in
  SpaceClaim. H1.2 fixes those failures and descopes 2D drawings. See
  `docs/VALIDATION.md`.
- The package is portable: the Hermes profile is rendered per workstation from
  `profile/`, and `scripts/preflight.ps1` checks a machine against the pinned
  environment. On 2026-11-02 it passed 29/29 checks against a freshly rendered
  profile, with live MCP connections.
- **Run F1, 2026-11-25** (Claude lead + 3 specialists): all gates PASS first time,
  **6.5 min** from Gate 0 to Fluent on the contour (7.0 min with the lead's final
  check); 193k sub-agent tokens measured. See `docs/VALIDATION.md`.
- **Scripts-only check, 2026-11-25** (FR330V-swtest2, no agents): CAD 103 s,
  SpaceClaim 23 s, Fluent 225 s, all gates PASS, 5.9 min from launch to Fluent
  on the contour. The agent run is recorded in `docs/VALIDATION.md`.
- History before the split (runs 1 to 7, fully agentic CAD) is kept in
  `docs/VALIDATION.md`. Not yet run under Hermes.
- **Self-contained:** both MCP servers install from this repo
  (`scripts/bootstrap_workstation.ps1`), see `docs/SETUP.md` section 1.

## Layout
| Path | What |
|---|---|
| `REQUIREMENTS.md` | What a new workstation needs, how to check each item, setup order (for people and agents). |
| `AGENTS.md` | Loaded into the lead **and every subagent**. Role table, ownership, evidence rules. |
| `specs/PRD.md` | Geometry, checksums, duty cycle, analysis, requirements, deliverables. The only authority. |
| `specs/handoffs.md` | Gate-by-gate artifact contracts and JSON keys. |
| `agents/<role>/SOUL.md` | Each agent's identity and habits. A child reads it itself, because Hermes doesn't inject it. |
| `agents/<role>/skills/*` | Role skills: detailed guidance, traps and checks, with `references/` loaded on demand. |
| `agents/_shared/skills/brake-handoff` | The cross-stage contract, loaded by the lead. |
| `profile/config.template.yaml` | The complete `brake` profile `config.yaml`, with 3 machine placeholders. |
| `profile/SOUL.md` | The lead's profile SOUL (system prompt; survives compaction). |
| `profile/environment.json` | Pinned versions, paths and tool counts that preflight checks. |
| `mcp/ansys-agent/` | The Ansys MCP server (vendored source + `requirements.lock.txt`). |
| `mcp/solidpilot/` | SolidPilot pin (`SOURCE.md`: upstream + commit), `requirements.lock.txt`, `.env` template. |
| `mcp/*-tools.json` | Tool inventories (names and argument schemas) of the SolidPilot (46) and Ansys (10) servers. |
| `profile/claude_mcp_servers.template.json` | MCP registration for Claude Desktop / Cowork. |
| `scripts/bootstrap_workstation.ps1` | New machine: installs both MCP servers from the pins and locks. |
| `docs/RUN_WITH_CLAUDE.md`, `examples/prompt-claude.md` | Running with Claude as the lead (how runs 3 to 7 ran). |
| `scripts/stages/*.py` | The two vetted stage scripts (SpaceClaim prep, Fluent mesh + heat-up), launched by their specialist. |
| `scripts/install_profile.ps1` | Renders the profile for this workstation. |
| `scripts/preflight.ps1` | Read-only machine check: PASS/WARN/FAIL per item. |
| `scripts/publish_github.ps1` | Pushes to GitHub (public by default) after safety checks. |
| `scripts/mcp_client.py` | Diagnostic MCP client (not the run path). |
| `examples/prompt.md` | The 3-line run prompt. |
| `examples/readiness-prompt.md` | A check that all four roles start with their tools and skills. |
| `docs/SETUP.md` | Step-by-step setup and launch. |
| `docs/SKILL_STYLE.md` | Rules for writing skills in this package. |
| `docs/DECISIONS.md` | Why H1/H1.1/H1.2 differ from the CTS and the predecessor. |
| `docs/source/` | The original CTS. Background only. |

## Quick start (any workstation)
```powershell
git clone https://github.com/nvsdef/hermes-brake-disc-fast.git; cd hermes-brake-disc-fast
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_workstation.ps1   # SolidPilot + ansys-agent MCPs
hermes profile create brake --clone                                            # Hermes only, once
powershell -ExecutionPolicy Bypass -File scripts\install_profile.ps1
powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1 -McpTest       # must end "0 FAIL"
```
Then either `hermes -p brake chat` from this folder and paste `examples/prompt.md`,
or open Claude Cowork on this folder and paste `examples/prompt-claude.md`
(`docs/RUN_WITH_CLAUDE.md`). Prerequisites: `REQUIREMENTS.md`; details: `docs/SETUP.md`.

Run output goes to `C:\brake-runs\<run-id>\`. Tests:
`C:/solidpilot/.venv/Scripts/python.exe -B -m unittest discover -s tests -v`.

## Scope
The model is the FR-330V vented rotor, pads, backing plates and a clearance
calliper, analysed as single-stop transient solid conduction with prescribed
convection.

Two items are deferred, with report rows but no geometry or model:
- Cross-drilling (GEO-05).
- Calliper and fluid temperature (THM-06).

This is a teaching case, not a certified brake.

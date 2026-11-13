"""Prompts for the single task Agent.

One static system prompt (kept byte-identical across calls so the provider can
cache the prefix) and one short user prompt per stage.  The stage prompts carry
the task request, the actual paths, prior check feedback, applicable experience
and the exact ``finish_stage`` contract the runner validates.  Task-specific
values (process, beams, event count, cuts, analysis settings) are never baked
into the prompts: they are read from the current task request (audit F4).
"""
from __future__ import annotations

import json
import shlex

from nsbi_agent.workflow.state import PROJECT_ROOT

SYSTEM_PROMPT = """You are the task Agent of Simulations Single Agentic Workflow.
You plan and perform one MadGraph generation task; the workflow runs the five
accepted checkers automatically at stage handoffs and reports their differences
back to you. You cannot see checker internals; act only on reported results.

## Project environment (paths below are relative to the project root)
- Project root: __PROJECT_ROOT__
- Pinned MadGraph5_aMC@NLO 2.9.27: backends/madgraph/2.9.27/source
- Pinned SMEFTsim 3.0.2 UFOs: models/smeftsim/official/3.0.2/<model ID>
- Python: .venv/bin/python (3.12.12, matplotlib installed)
- Launch MadGraph only through the project wrappers:
  * scripts/mg5.sh -f <script.mg5>        # import model / generate / output
  * scripts/mg5_run.sh <process_dir> -f   # event generation
  Never edit files under models/ or backends/.
- Baseline cards (verified against checker 3; adapt them to the request):
  src/nsbi_agent/workflow/templates/proc_card.mg5, run_card.dat, param_card.dat
- Analysis program template: src/nsbi_agent/workflow/templates/analysis.py
  with analysis_config.json; it implements the m_ee histogram and plot and
  honours the analysis settings you put in the config, including pair_pdg_ids
  for the invariant mass of any requested pair of stable final-state particles.

## MadGraph card facts
- Model import line: import model <UFO path from the request>[-<restriction>]
  A trailing -full means restriction=full (no restriction card is applied).
- run_card.dat format is `value = key ! comment`; booleans are `.true.`/`.false.`.
  The baseline lists every neutral cut explicitly. Change only the fields the
  request needs and keep every other cut neutral (0.0 lower / -1.0 upper).
- param_card.dat: each line ends with `# <parameter name>`. Change only the
  values the request names (SM inputs, Wilson coefficients, cutoff).
- proc_card.mg5 contains the import line, the multiparticle aliases required by
  the request process, and one `generate` line. Do not add `set ...` lines;
  extra commands become checker review items.
- Coupling-order selectors follow the request's production orders and EFT
  contribution/prediction definition; derive them and let checker 3 verify them.

## How the workflow checks you
1 environment versions; 2 structured request contracts; 3 actual cards vs
request under MG5 2.9.27 native rules; 4 LHE events/weights vs job exit code and
log; 5 experience admission. `succeeded` advances. `failed` returns the exact
differences to fix. `needs_review` asks you to record one disposition per review
topic: accepted advances, repaired sends the workflow back to the owning stage
for a recheck, deferred pauses the task and asks the user to decide.

## Operating rules
- Keep all your files inside your task workspace (writes outside it are rejected).
- Follow the stage instructions directly and keep file reads relevant to the task.
- If applicable experience is supplied in a stage prompt, use it and end that
  stage's summary with: "Applied experience <first 12 characters of its hash>: <what you did>".
- Read log tails and grep specific fields; never paste whole logs into context.
- Every number you report must come from a file you read or from a checker
  record supplied in the stage prompt; quote the source path.
- Do not describe settings other than those in the request/verified configuration
  entries given to you. If a requested setting is outside the local driver's
  supported scope, say so explicitly in the stage summary instead of reusing an
  unrelated baseline.
- End every stage with the finish_stage tool. The workflow validates artifacts
  and runs the corresponding checker itself.
- Keep messages focused on what the stage needs.
""".replace('__PROJECT_ROOT__', str(PROJECT_ROOT))

PLAN_SKELETON = """{
  "simulation": {
    "process": {"initial_state": [<legs>], "final_state": [<legs>],
                "multiparticles": {<alias>: [<members>]}, "kind": <decay|collision|hadron_collision|lepton_collision>},
    "model": {"collection": "official", "model_id": <model id from request>,
              "version": <version>, "basis": <basis>, "flavour_scheme": <scheme>,
              "ew_input_scheme": <scheme>, "restriction": <restriction or full>},
    "eft": {"cutoff_GeV": <Lambda>, "coefficients": {<name>: <value>},
            "coefficient_policy": "zero_then_set", "width_policy": "fixed_ufo_defaults",
            "prediction": <requested prediction; omit if absent>,
            "propagator_corrections": <requested included|excluded; omit if absent>,
            "sm_higgs_loop_vertices": "excluded"},
    "generation": {"nevents": <count>, "seed": <seed>, "beam_energies_GeV": [<e1>, <e2>],
                    "beam_types": [<lpp1>, <lpp2>], "pdf": {"label": <label>, "set_id": <id>},
                    "cuts": [{"observable": <name>, "minimum": <value>, "maximum": <value>, "unit": <unit>}]},
    "stage": <parton stage>
  },
  "production": {"qcd_order": <n>, "qed_order": <n>,
                  "renormalization_scale_GeV": <value>, "factorization_scale_GeV": <value|null>},
  "analysis": {"observable": <name>, "bins": <n>, "range_GeV": [<lo>, <hi>],
               "normalization": "per-bin sum of stored nominal XWGTUP weights"}
}"""


def ungated_system_prompt() -> str:
    """Keep operational knowledge; remove promises of automatic validation."""
    prompt = SYSTEM_PROMPT.replace(
        'You plan and perform one MadGraph generation task; the workflow runs the five\n'
        'accepted checkers automatically at stage handoffs and reports their differences\n'
        'back to you. You cannot see checker internals; act only on reported results.',
        'You plan and perform one MadGraph generation task. This ablation disables the\n'
        'five automatic checkers and their repair/review handoffs. Inspect your own\n'
        'files, commands and physics setup using the general tools. Do not invoke\n'
        'the five checker APIs or checker CLI commands in this ablation.')
    start = prompt.index('## How the workflow checks you')
    end = prompt.index('## Operating rules', start)
    prompt = prompt[:start] + prompt[end:]
    return prompt.replace('derive them and let checker 3 verify them.',
                          'derive them and inspect the actual process card yourself.')


def _json(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def _experience_block(experience: dict | None, stage: str) -> list:
    if not experience or not experience.get('content'):
        return []
    return [
        '',
        '## Applicable experience (retrieved before this task)',
        experience['content'].strip(),
        'If it applies to THIS stage, follow it and end the summary with '
        '"Applied experience <first 12 characters of the entry hash>: <what you did>". '
        'If it does not apply, say so in one short clause.',
    ]


def stage_plan(request_text: str, workspace: str, experience: dict | None,
               check_feedback: dict | None, clarifications: list | None = None) -> str:
    lines = [
        '## Stage: plan_resolve (no MadGraph)',
        'Resolve the user request into one explicit structured request. Use the request '
        'values; do not copy settings from examples or from this prompt. Read '
        'docs/ParaList.md only if a field meaning is genuinely unclear.',
        '',
        'User request:',
        request_text.strip(),
        '',
        'STEP 1 - write ' + workspace + '/request.json using this skeleton '
        '(replace every <...> from the user request; omit optional eft fields the request '
        'does not name):',
        PLAN_SKELETON,
        '',
        'Rules:',
        '- For a material physics ambiguity, or when the user asks to clarify first, call '
        'ask_user with one concrete question in the user\'s language and optional choices. '
        'The workflow pauses; you will receive the answer when planning resumes. '
        'Use documented defaults for routine omitted settings and record their sources.',
        '- If the user request leaves a setting open, choose a defensible default from '
        'docs/ParaList.md and record it in defaults.json with its source.',
        '- If the request needs a process/model/observable outside the local driver and '
        'checker scope, do not force a baseline onto it: finish with complete=false and '
        'state the unsupported part in the summary.',
        '- Do not run MadGraph in this stage.',
        '- For a pair invariant-mass histogram, put the two final-state PDG IDs into '
        'analysis.pair_pdg_ids (e.g. [11,-11], [23,25], or [6,-6]). Preserve requested '
        'event counts, Wilson points and the full prediction definition.',
        '- When the request says SM + interference + dimension-six squared, explicitly set '
        'simulation.eft.prediction="sm_plus_linear_and_dim6_squared". Preserve an explicit '
        'propagator exclusion as simulation.eft.propagator_corrections="excluded".',
        '- Generation cuts use physics names in request.json: charged_lepton_pt, '
        'dilepton_mass, etc. Native names ptl/mmll belong only in the run card. '
        'Omit an absent upper bound instead of writing the MadGraph sentinel -1.',
        '',
        'STEP 2 - write ' + workspace + '/defaults.json mapping each choice you supplied '
        'beyond the request to {"value": ..., "source": ...}.',
        '',
        'STEP 3 - call finish_stage(stage="plan_resolve", complete=true, '
        'artifacts={"request": <abs request.json path>, "defaults": <abs defaults.json path>}, '
        'summary=<one or two sentences: process, model, parameter point, analysis goal>).',
    ]
    lines += _experience_block(experience, 'plan_resolve')
    if clarifications:
        lines += ['', '## User clarifications (retain these answers in this plan)', _json(clarifications)]
    if check_feedback:
        lines += ['', '## Checker 2 feedback from the previous attempt (fix these)', _json(check_feedback)]
    return '\n'.join(lines)


def stage_prepare_cards(request: dict, workspace: str, attempt: int,
                        check_feedback: dict | None, experience: dict | None = None) -> str:
    attempt_dir = f'{workspace}/attempts/{attempt:03d}'
    simulation = request.get('simulation') or {}
    process = simulation.get('process')
    aliases = process.get('multiparticles') or {} if isinstance(process, dict) else {}
    alias_lines = ('; '.join('define %s = %s' % (name, ' '.join(members))
                             for name, members in aliases.items())
                   if aliases else 'define the aliases given in simulation.process.multiparticles')
    lines = [
        '## Stage: prepare_cards (attempt %03d)' % attempt,
        'Produce one complete MadGraph process directory whose cards match the request.',
        'Request being checked:',
        _json(request),
        '',
        'Procedure:',
        '1. Create %s and write %s/cards.mg5 with: the model import line, the multiparticle '
        'aliases required by the request process, one generate line, and the output directory.'
        % (attempt_dir, attempt_dir),
        '   - import model: take the UFO path and restriction from simulation.model; append '
        '"-<restriction>" (use "-full" for restriction=full).',
        '   - aliases: ' + alias_lines + '.',
        '   - generate: use the request process legs and derive coupling-order selectors from '
        'production.qcd_order/qed_order and the EFT contribution/prediction definition.',
        '     Write explicit NP, NPprop and SMHLOOP selectors: SM point NP=0; '
        'sm_plus_linear_and_dim6_squared uses NP=1 NP^2=2. For excluded propagator '
        'corrections / SM Higgs loops use NPprop=0 / SMHLOOP=0.',
        '   - output %s/pp_ee' % attempt_dir,
        f'2. Run it: run_command("bash {shlex.quote(str(PROJECT_ROOT / "scripts/mg5.sh"))} -f %s/cards.mg5", cwd="%s", '
        'timeout_seconds=1800). Check the log tail for "Output to directory ... done".' % (attempt_dir, attempt_dir),
        '3. Configure the cards in %s/pp_ee/Cards/:' % attempt_dir,
        '   run_card.dat: copy the baseline template src/nsbi_agent/workflow/templates/run_card.dat '
        'and set the request-driven fields (run_tag, nevents, iseed, beam energies/types, PDF label '
        'and set id, fixed scales and scale values, and the requested generation cuts; map cut '
        'observables to the native fields, e.g. charged_lepton_pt -> ptl/ptlmax, '
        'dilepton_mass -> mmll/mmllmax). Keep every other cut neutral.',
        '   param_card.dat: start from MG5\'s own default card %s/pp_ee/Cards/param_card_default.dat '
        '(copy it to param_card.dat), fix the Gf rounding (`1.166379e-05` -> `1.1663787e-05`), then '
        'apply only the request\'s SM input / Wilson coefficient / cutoff values (each line ends '
        'with "# <name>").' % attempt_dir,
        '     Match comments after stripping whitespace, preserve the requested scalar precision '
        'when writing scales, and check the resulting LambdaSMEFT and nonzero coefficients.',
        '   proc_card_mg5.dat: write exactly the same content lines as cards.mg5 (no `set` lines).',
        f'4. Write %s/run_events.sh containing: cd %s/pp_ee && bash {shlex.quote(str(PROJECT_ROOT / "scripts/mg5_run.sh"))} '
        '$(pwd) -f' % (attempt_dir, attempt_dir),
        '   (no output redirection: the workflow job runner captures stdout/stderr in the job log).',
        '5. Before finishing, grep the three cards to confirm the values you set (quote the paths and lines).',
        '',
        'Do the card edits with one run_command that '
        'runs a short python here-document. The baseline cards are verified against checker 3: if checker '
        '3 reports a parse/type error that points at a baseline line, report it in your summary instead '
        'of investigating MadGraph internals.',
        'Finish with finish_stage(stage="prepare_cards", complete=true, '
        'artifacts={"param_card": <abs>, "run_card": <abs>, "process_card": <abs>}, '
        'proposed_job={"script": <abs run_events.sh>, "cwd": <abs pp_ee>, '
        '"expected_outputs": [<abs pp_ee/Events>]}, summary=<short summary>).',
    ]
    lines += _experience_block(experience, 'prepare_cards')
    if check_feedback:
        lines += ['', '## Checker 3 feedback from the previous attempt (fix exactly these)', _json(check_feedback)]
    return '\n'.join(lines)


def stage_dispositions(check_record: dict, context: dict | None = None,
                       experience: dict | None = None,
                       user_decisions: dict | None = None) -> str:
    lines = [
        '## Stage: dispositions',
        'Checker %s returned needs_review. Record one review disposition per topic below.'
        % check_record.get('number'),
        _json(check_record.get('review_items', [])),
    ]
    if context:
        lines += ['', '## What this task actually asks for (use it; do not describe a different task)',
                  _json(context)]
    if user_decisions:
        lines += ['', '## User decisions already supplied (use these as authoritative)', _json(user_decisions)]
    lines += [
        '',
        'For each topic choose exactly one disposition:',
        '- accepted: the native value is the intended configuration; explain why with the actual '
        'request point and card lines.',
        '- repaired: you changed an artifact; say which file and what changed. The workflow will '
        'return to the owning stage and re-run the corresponding check.',
        '- deferred: a user decision is required; state the concrete question. The task pauses and '
        'asks the user before continuing.',
        'Do not invent evidence: cite the actual card or check file paths you read (at most one '
        'quick grep needed).',
        'Finish with finish_stage(stage="dispositions", complete=true, '
        'dispositions=[{"topic": ..., "disposition": "accepted|repaired|deferred", '
        '"reason": ..., "evidence": [<abs paths>]}], summary=<one line>).',
    ]
    lines += _experience_block(experience, 'dispositions')
    return '\n'.join(lines)


def stage_analysis(task_id: str, request: dict, configuration: dict | None,
                   workspace: str, attempt: int, event_file: str,
                   sample_check: dict, init_cross_section: str,
                   experience: dict | None = None, gates_enabled: bool = True) -> str:
    out = f'{workspace}/analysis'
    generation = request.get('simulation', {}).get('generation', {})
    analysis = request.get('analysis', {}) or {}
    bins = analysis.get('bins') if isinstance(analysis.get('bins'), int) else 10
    range_gev = analysis.get('range_GeV') or [66.0, 116.0]
    observable = analysis.get('observable') or 'm_ee'
    config = {
        'task_id': task_id,
        'sample_label': (configuration or {}).get('sample_label') or 'pp collisions',
        'event_file': event_file,
        'nevents': generation.get('nevents'),
        'seed': generation.get('seed'),
        'observable': observable,
        'bins': bins,
        'range_GeV': range_gev,
        'pair_pdg_ids': analysis.get('pair_pdg_ids') or [11, -11],
    }
    lines = [
        '## Stage: analyze_deliver (do NOT explore cards or logs)',
        'Checker 4 already reported the sample statistics:', _json(sample_check),
        f'Init-reported cross sections (pb): {init_cross_section}',
        '',
        '## Verified configuration to describe in the summary (from the request and the checks)',
        _json(configuration or {}),
        '',
        'A verified analysis program template is provided; it reads the analysis settings from the '
        'config and implements the histogram, per-bin sumw/sumw2, flows and the error-bar plot. '
        'It supports the invariant mass of the pair_pdg_ids supplied below; use those '
        'actual particle IDs and retain their physical labels in the summary.',
        f'1. One run_command: mkdir -p {out} && cp '
        f'{shlex.quote(str(PROJECT_ROOT / "src/nsbi_agent/workflow/templates/analysis.py"))} '
        f'{out}/analysis.py',
        f'2. write_file {out}/analysis_config.json with exactly this object (analysis settings come '
        'from request.analysis):',
        _json(config),
        f'3. One run_command: cd {out} && {shlex.quote(str(PROJECT_ROOT / ".venv/bin/python"))} analysis.py',
        f'4. view_image {out}/plot.png; note labels/units/error bars/title readability.',
        '5. write_file ' + out + '/summary.md (short, <25 lines). Describe ONLY the verified '
        'configuration above: process and model, beams/PDF, generation cuts, scales, parameter point '
        'and contribution, event/weight readings, init cross section in pb, histogram reading, plot '
        'meaning and your visual review. Describe the actual pair_pdg_ids; for electrons the '
        'local UFO uses e- = PDG 11 and e+ = PDG -11. Pair invariant mass is symmetric.',
        'Finish with finish_stage(stage="analyze_deliver", complete=true, artifacts={"plot": <png>, '
        '"table": <analysis.json>, "program": <analysis.py>, "summary": <summary.md>}, summary=<short '
        'readout including events, sumw, cross section and the visual check>).',
    ]
    lines += _experience_block(experience, 'analyze_deliver')
    prompt = '\n'.join(lines)
    if not gates_enabled:
        prompt = prompt.replace('do NOT explore cards or logs', 'inspect native cards/banner as needed')
        prompt = prompt.replace('Checker 4 already reported the sample statistics:',
                                'No automatic sample-check report is supplied in this ablation:')
        prompt = prompt.replace('## Verified configuration to describe in the summary (from the request and the checks)',
                                '## Requested setup (target settings; read native artifacts for actual values)')
        prompt = prompt.replace('Describe ONLY the verified configuration above:',
                                'Read native cards/banner and analysis outputs; describe actual values and any differences from the requested setup:')
    return prompt


def stage_experience(request: dict, workspace: str, evidence: list, scope: dict) -> str:
    return '\n'.join([
        '## Stage: experience',
        'Draft one reusable lesson from this task\'s failure/repair evidence and self-review it.',
        'Evidence records (absolute paths; role observation cites the failed check, role validation '
        'cites a successful check):', _json(evidence),
        'Applies-to scope requested:', _json(scope),
        'Lesson must describe a procedure another task can reuse, not the specific numbers. Mention '
        'that the repaired card check showed zero differences after repair and which review items '
        'were dispositioned, if relevant.',
        'Review record: {"by": "agent", "verdict": "accept", "reason": <specific reason citing what '
        'you compared>}.',
        f'Write {workspace}/experience/candidate.json with keys: lesson, applies_to (nonempty '
        'name->text map), evidence (list of {"path","role"}), review.',
        'Finish with finish_stage(stage="experience", complete=true, '
        'artifacts={"candidate": <abs path>}, summary=<one line>). The workflow runs checker 5 and '
        'persists the entry only when admission=ready.',
    ])


def repair_note(check_number: int, record: dict) -> str:
    return ('Checker %s returned failed. Fix exactly the reported differences in your artifacts, '
            'then finish this stage again. Report: %s' % (check_number, _json(record)))

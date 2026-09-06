# FLE Integration Notes

Findings about the Factorio Learning Environment, recorded so future sessions do not
re-derive them. **Consume this document instead of re-reading FLE source.**

Originally verified against `JackHopkins/factorio-learning-environment` @ `main`,
2026-09-02, FLE version 0.3.0. **The installed version is 0.4.3** (PyPI, 2026-09-06);
findings re-checked against it are marked *confirmed 0.4.3*, and one recorded detail
turned out to be wrong - see the `reset()` note below. Re-verify before relying on
anything marked *unverified*.

---

## Install and prerequisites

- Python 3.10+, Docker.
- `pip install factorio-learning-environment[eval]` (extras: `eval`, `mcp`, `psql`).
  `uv sync` is the recommended path.
- **Factorio 2.0.73+ is required only for optional rendering**, not for headless play.
- CLI: `fle cluster start`, then `fle eval --config configs/gym_run_config.json`.
- Linux-first. `fle/cluster/run-envs.sh` is bash; cluster startup assumes POSIX paths
  and Linux Docker networking. On Windows, run everything inside WSL2.

### Local WSL2 setup - verified 2026-09-06

Recorded because two of these cost real time and neither is documented by FLE.

| Item | Value |
|---|---|
| Distro | Ubuntu 24.04.4 LTS (Noble Numbat), default user `leong` (uid 1000, `sudo`) |
| Kernel | 6.18.33.2-microsoft-standard-WSL2, WSL 2.7.13.0 |
| Repo path in WSL | `/mnt/c/Users/leong/dev/Factorio-maxxing` (D29) |
| Python | 3.13.15, installed by `uv python install 3.13` |
| `uv` | 0.12.10, installed to `~/.local/bin` |

**`wsl --install` alone was not sufficient.** It enabled `VirtualMachinePlatform` and
installed the kernel, but distro registration then failed with
`HCS_E_HYPERV_NOT_INSTALLED`, whose error text misleadingly says to enable Virtual
Machine Platform - which was already enabled. The actual cause was
`hypervisorlaunchtype Off` in the boot configuration, so no hypervisor started at boot
despite every hardware requirement being met. Fix, elevated, then reboot:

```
bcdedit /set hypervisorlaunchtype auto
```

Diagnose it with `HypervisorPresent` from `Get-ComputerInfo`: features enabled plus all
four `HyperVRequirement*` true plus `HypervisorPresent: False` means the launch type,
not the firmware and not the optional components.

**Ubuntu 24.04 ships Python 3.12.3, below this project's `>=3.13` floor** (D14). Chosen
fix is `uv`-managed 3.13 rather than the deadsnakes PPA or lowering the floor, so the
interpreter is pinned by the project rather than by the distro. 24.04 LTS was chosen over
the available 26.04 LTS deliberately: FLE is the risky dependency, and a newer glibc plus
a system Python that binary wheels may lag on would make install friction hard to
distinguish from harness defects (the D3 attribution argument).

Docker Engine runs inside the distro rather than Docker Desktop (D30): Docker 29.8.0,
Compose v5.5.1, containerd v2.3.4, service enabled under systemd.

### Installing FLE - two things that block a fresh install

Environment: `~/venvs/fle`, created with `uv venv --python 3.13`, FLE installed as
`factorio-learning-environment[eval]` (0.4.3, 148 packages).

**1. `a2a-sdk` must be pinned below 1.0.** FLE 0.4.3 declares `a2a-sdk` with **no version
constraint**, so a fresh resolve picks up 1.x, in which `a2a.types` no longer exports
`TextPart`, `Message`, `AgentCard`, `Role`, `AgentCapabilities`, `AgentProvider` or
`AgentSkill`. Six FLE modules import those names - including `fle/agents/agent_abc.py`,
which `fle.env.gym_env.registry` pulls in transitively - so `make_factorio_env` and
`list_available_environments` both die at import with:

```
ImportError: cannot import name 'TextPart' from 'a2a.types'
```

Fix: `uv pip install "a2a-sdk<1"`, which resolves to 0.3.26. All eight symbols are then
present and `registry` imports cleanly. **This constraint must travel with the `fle`
optional extra when item 17 adds it to `pyproject.toml`**, or the next fresh install
reproduces the failure.

**2. The `[eval]` extra builds `psycopg2` from source**, which needs `pg_config`. Install
`build-essential` and `libpq-dev` first, or the install aborts partway. (`psycopg2-binary`
is also pulled in, but does not satisfy the `psycopg2` requirement.)

### Cluster - confirmed working 2026-09-06

`fle cluster start -n 1 -s open_world` pulls `factoriotools/factorio:2.0.73` and starts
`cluster-factorio_0-1`, publishing `34197/udp` (game) and `27000->27015/tcp` (RCON).
`list_available_environments()` then returns **30 task keys**, including `open_play`,
`open_play_production`, and throughput tasks from `iron_ore_throughput` up to
`utility_science_pack_throughput`.

**`fle` writes state into the current working directory** - a `.env` template of `XXX`
placeholders, and `.fle/data.db` for its SQLite store. Invoked through `wsl.exe`, the
working directory is inherited from Windows, so running it from the repo drops those
files into the repository. `.env` is gitignored so nothing leaks, but the template sets
`ANTHROPIC_API_KEY=XXX`, which would shadow the real key that D27 requires from the
environment. **Run `fle` from `~/fle-work`**, not from the repo.

---

## Gym interface

### `fle/env/gym_env/action.py`

```python
@dataclass
class Action:
    code: str
    agent_idx: int = 0
    game_state: Optional[GameState] = None
```

Passing `game_state` causes the environment to reset to that state before executing —
this is the checkpoint/restore mechanism (future scope).

### `fle/env/gym_env/environment.py`

```python
# confirmed 0.4.3, read from the installed source by inspect.signature
def reset(self, options: Optional[Dict[str, Any]] = None,
          seed: Optional[int] = None) -> Dict[str, Any]
def step(self, action: Action) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]
    # (observation, reward, terminated, truncated, info)
```

**Correction.** This document previously recorded `reset()` as returning
`tuple[dict, dict]` - an `(observation, info)` pair, the Gymnasium convention. In 0.4.3
it is annotated as returning a **single observation dict**, with no `info`. `step()` is
unchanged and does return the five-tuple. `RealFactorioEnv` (item 17) must not unpack
`reset()` as a pair. *Unverified:* this is the annotation, not an observed return value -
confirm against an actual `reset()` call when item 17 first connects, since an annotation
can lie.

`reward = production_score - initial_score - error_penalty`, unless the task supplies
`REWARD_OVERRIDE_KEY` in `task_success.meta`. Observations are returned as **dicts**
matching the gym observation space, not `Observation` objects — use
`Observation.from_dict(...)` to get the dataclass.

`close()` calls `instance.cleanup()`. `background_step()` clears enemies and requests
chunk generation via RCON.

### `fle/env/gym_env/observation.py`

```python
@dataclass
class Observation:
    raw_text: str
    entities: List[Dict[str, Any]]
    inventory: Inventory
    research: ResearchState
    game_info: GameInfo               # tick, time, speed
    score: float
    automated_score: float            # excludes harvested + manually crafted
    flows: ProductionFlows            # input/output/crafted/harvested/price_list
    task_verification: Optional[TaskResponse]
    messages: List[AgentMessage]
    serialized_functions: List[Dict[str, Any]]
    task_info: Optional[TaskInfo]
    map_image: str                    # base64 PNG
    character_positions: List[CharacterPosition]
```

`ResearchState.technologies` is a dict of `TechnologyState`, each carrying
`researched`, `enabled`, `level`, `research_unit_count`, `research_unit_energy`,
**`prerequisites`**, and `ingredients`. The live prerequisite graph is therefore
readable at runtime — the tech-tree curriculum can be derived by topological sort
rather than hand-authored (future scope, M3).

### `fle/env/gym_env/registry.py`

- `list_available_environments() -> List[str]` — all registered task keys.
- `get_environment_info(task_key) -> dict`
- `make_factorio_env(spec, run_idx)` — creates the `FactorioInstance`, sets speed 10,
  unpauses, calls `task.setup(instance)`, returns `FactorioGymEnv`. Confirmed 0.4.3:
  `make_factorio_env(spec: GymEnvironmentSpec, run_idx: int) -> FactorioGymEnv`, so
  item 17 needs a `GymEnvironmentSpec`, not a bare task-key string.
- Environments auto-register on module import.
- Server discovery: `FACTORIO_SERVER_ADDRESS` / `FACTORIO_SERVER_PORT` env vars
  override local container discovery. `PORT_OFFSET` selects among local containers.

---

## Tasks

Registry: `fle/eval/tasks/task_definitions/task_registry.py` —
`list_all_tasks()`, `get_task_info(key)`, `create_task(key)`.

Task types: `throughput`, `unbounded_throughput`, `default`, and
`unbounded_production` (the last is handled by the Inspect solver/scorer, **not** the
`TaskABC` hierarchy).

Known keys include `open_play` (`DefaultTask`, `trajectory_length=5000`),
`open_play_production`, and throughput tasks such as `steel_plate_throughput`.

Lab-play throughput tasks verify by **sleeping `holdout_wait_period` (60s) and
measuring achieved throughput**, repeatedly, taking the maximum. This is why our
verifier must see production flows over a window rather than a single snapshot.

---

## Agents

### `fle/agents/agent_abc.py`

`AgentABC` with abstract `step(conversation, response, namespace) -> Policy`, `end(...)`,
and `check_step_completion(response) -> (update_state, completed)`.

**We do not subclass this** — see `decisions.md` D1.

### `fle/agents/gym_agent.py`

`GymAgent` is the published baseline. **Never modify it** (`decisions.md` D2).

Useful details for keeping our prompt comparable:
- `GYM_AGENT_INSTRUCTIONS` uses a PLANNING stage then a POLICY stage with fenced
  Python.
- Instructs "MAXIMUM 50 lines of code per policy".
- Contains the line `DON'T REPEAT YOUR PREVIOUS STEPS` — evidence that short history
  windows cause rebuilding. We use a 16-step window to match its formatter.
- `RecursiveReportFormatter(chunk_size=16)` handles conversation compaction.
- `GenerationParameters(n=1, max_tokens=4096)`.

### `fle/agents/llm/api_factory.py`

`APIFactory.PROVIDERS` maps provider → `{base_url, api_key_env, ...}`:

| Provider | Base URL | Key env |
|---|---|---|
| `claude` | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `gemini` | `.../v1beta/openai/` | `GEMINI_API_KEY` |
| `together` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| `open-router` | `https://openrouter.ai/api/v1` | `OPEN_ROUTER_API_KEY` |
| `ollama` | `$OLLAMA_BASE_URL` or `localhost:11434/v1` | `OLLAMA_API_KEY` |

**All providers route through a generic `AsyncOpenAI` client. There is no model
allowlist** — model strings pass through, with `model_transform` only stripping
prefixes (`open-router-`, `ollama-`). The leaderboard is a record of what was run, not
a supported-models list.

*Unverified:* the exact provider-routing function (`_get_provider_config`) was not
read; prefix matching is inferred from the `model_transform` lambdas. Confirm the exact
model string format on first live call.

*Note:* the generic OpenAI-compatible client may need patching for reasoning models
that return a separate `reasoning_content` field or reject `temperature`.

---

## Technologies

`fle/env/game_types.py::Technology` is a curated enum (not the full Factorio tree).
Includes `SteamPower`, `AutomationSciencePack`, `Automation`/`2`/`3`, `Logistics`/`2`/`3`,
`Electronics`, `AdvancedElectronics`/`2`, `SteelProcessing`,
`AdvancedMaterialProcessing`/`2`, `OilProcessing`, `AdvancedOilProcessing`,
`SulfurProcessing`, `Plastics`, `Lubricant`, `Robotics`, `NuclearPower`, `RailwayTransportation`,
power-armor and equipment lines, productivity modules. Several entries are commented
out upstream (e.g. `CircuitNetwork`, `Modules`, `RocketSilo`, most military tiers).

Tools: `fle/env/tools/agent/set_research/` and `.../get_research_progress/`.
`set_research(Technology) -> List[Ingredient]` cancels current research, validates
prerequisites and science-pack availability, and raises on invalid transitions.
`get_research_progress(tech=None) -> List[Ingredient]` returns remaining packs; raises
if no research is active when called with no argument.

---

## Baseline numbers (leaderboard, March 2025, open-play 5000 steps)

| Model | Production Score | Milestones (auto) | Lab tasks | Deepest item |
|---|---|---|---|---|
| Claude 3.5 Sonnet | 293,206 | 30 (13) | 21.9% | `plastic-bar` |
| Gemini 2 Flash | 115,782 | 20 (6) | 13.0% | `iron-gear-wheel` |
| GPT-4o | 87,599 | 30 (9) | 16.6% | `plastic-bar` |
| Llama 3.3 70B | 54,998 | 16 (4) | 5.2% | `iron-plate` |
| DeepSeek V3 | 48,585 | 22 (7) | 15.1% | `plastic-bar` |
| GPT-4o-mini | 26,756 | 14 (4) | 4.2% | `iron-plate` |

Submission format: a JSON file in `docs/leaderboard/results/`, via PR.
No 2026-generation model has published FLE results.

---

## Policy API reference - NOT YET RECORDED (blocks Phase 5 item 18)

The harness renders an `ENVIRONMENT API` section into the policy prompt from a file
named by the `api_reference` config key. **That file does not exist yet, and its
contents cannot be written from this document.**

Evidence it is needed: on the first live call (Haiku 4.5, mock environment, 2026-09-03)
the policy model invented an API - `game.scan(radius=10)`,
`game.insert('burner-mining-drill', 'coal', 2)`, `game.get_inventory()`. None of those
exist. The prompt told it to emit Python and never said which functions were callable,
so it produced something plausible. Against the mock this is invisible, because the mock
ignores submitted code by design (D10); against live Factorio every policy would fail on
its first call.

**What to record here once FLE is installed**, and only from the installed source:

- the callable surface under `fle/env/tools/agent/` - one line per tool, with signature
  and return type;
- how those names enter the namespace a policy executes in;
- what `GYM_AGENT_INSTRUCTIONS` tells the model about the API, so our reference stays
  comparable to the published baseline without copying its wording (D1);
- any import or setup a policy must perform before calling them.

Then write the reference to a file, point `api_reference` at it, and confirm against a
live run that policies stop inventing calls.

Do not author this section from memory or from a web search. A wrong API reference is
worse than none: it would teach the model incorrect calls with the harness's authority
behind them, and the resulting failures would look like model incapability in the
results.

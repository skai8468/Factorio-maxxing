# FLE Integration Notes

Findings about the Factorio Learning Environment, recorded so future sessions do not
re-derive them. **Consume this document instead of re-reading FLE source.**

Verified against `JackHopkins/factorio-learning-environment` @ `main`, 2026-09-02.
FLE version 0.3.0. Re-verify before relying on anything marked *unverified*.

---

## Install and prerequisites

- Python 3.10+, Docker.
- `pip install factorio-learning-environment[eval]` (extras: `eval`, `mcp`, `psql`).
  `uv sync` is the recommended path.
- **Factorio 2.0.73+ is required only for optional rendering**, not for headless play.
- CLI: `fle cluster start`, then `fle eval --config configs/gym_run_config.json`.
- Linux-first. `fle/cluster/run-envs.sh` is bash; cluster startup assumes POSIX paths
  and Linux Docker networking. On Windows, run everything inside WSL2.

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
def reset(self, options=None, seed=None) -> tuple[dict, dict]   # (observation, info)
def step(self, action: Action) -> tuple[dict, float, bool, bool, dict]
    # (observation, reward, terminated, truncated, info)
```

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
  unpauses, calls `task.setup(instance)`, returns `FactorioGymEnv`.
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

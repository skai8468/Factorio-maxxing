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
| FLE venv | `~/venvs/fle`, with this package installed editable from `/mnt/c` |
| `fle` working dir | `~/fle-work` - run every `fle` command from here, never the repo |

**API credentials must be set inside WSL.** They do not cross from Windows: the harness
runs in the WSL virtualenv, so `ANTHROPIC_API_KEY` and `ANTHROPIC_WORKSPACE_ID` (D27) have
to exist there. Keep them in `~/fle-work/.env.local` - outside the repository, so they
cannot be committed - and source it before a live run. Do not use FLE's own
`~/fle-work/.env`: its `ANTHROPIC_API_KEY=XXX` placeholder would shadow the real key.

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

### Factorio version - moved to 2.0.77, and the pin is in THREE places

FLE's README states **"version 2.0.73 or later"** - a minimum, not a pin - and the
installed package contains no runtime version check and ships no mods beyond `base`. The
cluster therefore runs **2.0.77** so the server matches a current game client; Steam
offers 2.0.77/2.0.76/2.0.72 but not 2.0.73, so matching the other way is not possible
(D34).

**The pin appears three times, and only one of them matters:**

| File | Line | Role |
|---|---|---|
| `fle/cluster/run-envs.sh` | 116 | **The effective one.** Regenerates `docker-compose.yml` on every `fle cluster start` |
| `fle/cluster/run_envs.py` | 62 | `ComposeGenerator.image` class attribute |
| `fle/cluster/docker-compose.yml` | 5 | Regenerated from the shell script - patching it alone does nothing |

Patching the `.py` and `.yml` and restarting **silently reverts to the old image**,
because the shell script rewrites the compose file. A `grep` restricted to
`--include=*.py --include=*.yml` misses `run-envs.sh` entirely; grep without filters.
(`fle/eval/inspect/sandbox/Dockerfile:16` carries a fourth reference, unused by this
project.)

**These edits live in `site-packages` and a reinstall discards all three.** `.orig`
backups sit beside each patched file. After any `uv pip install` that touches FLE,
re-apply and re-verify.

### Cluster - confirmed working 2026-09-06, re-verified on 2.0.77 2026-09-07

`fle cluster start -n 1 -s open_world` pulls the pinned image and starts
`cluster-factorio_0-1`, publishing `34197/udp` (game) and `27000->27015/tcp` (RCON).
`list_available_environments()` then returns **30 task keys**, including `open_play`,
`open_play_production`, and throughput tasks from `iron_ore_throughput` up to
`utility_science_pack_throughput`.

### Watching a run with a real Factorio client - verified 2026-09-07

Optional, and needed by nothing in the harness; the FLE renderer produces map images
without a game client at all. Recorded because getting a client connected took four
distinct fixes, none of them obvious from the error message, which is only ever
"could not establish network communication with server".

1. **Version must match exactly.** Factorio multiplayer refuses a mismatch. Steam offers
   2.0.77/2.0.76/2.0.72 but not FLE's pinned 2.0.73, which is why the server moved to
   2.0.77 (D34).
2. **Connect to the WSL VM's IP, not `localhost`.** WSL2's default NAT networking forwards
   TCP on localhost but not UDP, and Factorio's game port is UDP. Get the address with
   `ip -4 addr show eth0` inside WSL - it was `172.25.110.245`, and **it changes when WSL
   restarts**. `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig` would make
   `localhost` work permanently, at the cost of a `wsl --shutdown`.
3. **The Hyper-V firewall blocks all inbound traffic to the WSL VM.** This is the one that
   looks like a Factorio problem and is not. `Get-NetFirewallHyperVVMSetting -PolicyStore
   ActiveStore` reports `DefaultInboundAction: Block`, and nothing from Windows reaches
   the VM on any port - ping and TCP fail too, which is the quickest way to tell this
   apart from a UDP-specific problem. Fix, elevated, and narrow:

   ```
   New-NetFirewallHyperVRule -Name "Factorio-WSL-Game" -DisplayName "Factorio game port (WSL)" `
     -VMCreatorId "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}" -Direction Inbound `
     -Protocol UDP -LocalPorts 34197 -Action Allow
   ```

   Prefer this to `Set-NetFirewallHyperVVMSetting -DefaultInboundAction Allow`, which
   opens every port to the VM.
4. **Do not restart the cluster while someone is connecting.** A `fle cluster stop/start`
   cycle logs `Quitting: signal` and drops the player mid-join, which reads exactly like a
   connection failure.

**Diagnosing this from the WSL side** is far faster than guessing from the client. Capture
with `tcpdump -i any -n udp port 34197` and read the direction: no packets at all means
they are blocked before the VM; one-way traffic means the server is not answering;
a two-way exchange of growing sizes (14 -> 26 -> 50 -> 131 bytes) is a real handshake.
Confirm the outcome in `docker logs`, which prints `[JOIN] <name> joined the game`, and
`/players online` over RCON.

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
# signatures as annotated in 0.4.3
def reset(self, options: Optional[Dict[str, Any]] = None,
          seed: Optional[int] = None) -> Dict[str, Any]     # ANNOTATION LIES - see below
def step(self, action: Action) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]
    # (observation, reward, terminated, truncated, info)
```

**`reset()`'s annotation is wrong - it returns a 2-tuple.** Confirmed 2026-09-06 by
calling it against a live container: `reset()` returns `(observation, info)`, the
Gymnasium convention, where `info` is an empty dict. The `-> Dict[str, Any]` annotation
describes only the first element.

This document briefly recorded the opposite, on the strength of the annotation alone,
and was corrected by measurement. The original `tuple[dict, dict]` note was right.
**`RealFactorioEnv` must unpack the pair** and return only the observation, because our
`EnvProtocol.reset()` returns a single observation dict (`envs.py`). `step()` genuinely
does return the five-tuple.

The lesson is cheap to state and was expensive to learn twice: in this codebase,
annotations are evidence of intent, not of behaviour. Verify against a live call.

### Observation dict - confirmed against a live `reset()` 2026-09-06

Top-level keys, settling the `unverified` note in D16:

```
automated_score  character_positions  entities   flows      game_info
inventory        map_image            messages   raw_text   research
score            serialized_functions task_info  task_verification
```

`research` carries the field *names* of
`fle/commons/models/research_state.py::ResearchState` - `technologies`,
`current_research`, `research_progress`, `research_queue`, `progress` - but **not its
types**. The gym observation is a plain dict built for the observation space, and the
dataclass annotations do not survive the conversion.

### Observation value shapes - measured, and they break the renderer

Read off a live `step()` after harvesting coal and stone. These are the shapes
`rendering.py` actually has to cope with, and it currently does not:

| Key | Real shape | What the renderer had assumed |
|---|---|---|
| `inventory` | **list** of `{"quantity": np.int32, "type": str}` | dict of `name -> count` |
| `entities` | list of dicts, values are **live objects** | list of plain dicts |
| `entity["position"]` | a `Position` **object**, not a dict | dict with `x`/`y` |
| `entity["status"]`, `["direction"]` | **enum members** (`EntityStatus.NO_FUEL`) | plain strings |
| `research.technologies` | **list** of `{"name", "researched", "enabled", "level", "prerequisites", ...}` | `dict` of states |
| `research.current_research` | the **literal string `"None"`** when idle, not `None` | `None` when idle |
| `research.research_progress` | **int** (`0`), not a float or ingredient list | ingredient counts |
| `flows.input/output/harvested` | **lists**: `[{"type": "coal", "rate": 7}]`; harvested uses `"amount"` | dict-ish counts |
| `flows.crafted` | craft **events**: `{"crafted_count", "inputs", "outputs"}` | item counts |
| `flows` also has | `price_list`, `static_items` | - |

Two of these are the gym observation space leaking through. `current_research` is typed
as a string, so an absent research serialises to `"None"` - which is truthy, and rendered
as `current: None` until it was special-cased. And `crafted` records *events* rather than
totals, so it carries what was consumed as well as produced; only the outputs are shown,
because what the policy needs to know is what it now has.

Consequence, observed live before the fix: after the agent harvested 5 coal,
`render_observation` emitted `INVENTORY (none)` - **the policy could not see its own
inventory**. `RESEARCH` printed `remaining: 0`, and `FLOWS` degraded to `output: 1 items`.
D16 anticipated exactly this ("confirm against a real observation at Phase 5 and tighten
then"). Fixed and verified live in D33; the renderer now emits:

```
INVENTORY
  stone 7
ENTITIES
  stone-furnace at (63, -52) facing UP [NO_FUEL]
RESEARCH
  current: none
  researched: 1/196
FLOWS
  input: stone 5
  output: stone-furnace 1, stone 12
  crafted: stone-furnace 1
  harvested: stone 12
```

Note also the **numpy scalars** (`np.int32`): `json.dumps` cannot serialise them.
`trajectory.py` already passes `default=str`, so a live run does not crash on the first
record - but the consequence is that inventory quantities land in the JSONL as **strings**
(`"quantity": "7"`), and any analysis over trajectories must coerce them back. That is a
data-quality wrinkle to know about, not a defect to fix blindly: coercing at record time
would mean the recorder editing what it observed, which cuts against its passive role
(D6).

**Size, and where it goes.** On a fresh `open_play` world the observation serialises to
**41,455 bytes, of which `research` is 40,853 - 98.5%**, because it carries the entire
technology tree with prerequisites and ingredients on every observation. Everything else
is under 200 bytes. This is a *trajectory* cost, not a prompt cost: the renderer collapses
`technologies` to a `researched: N/M` count, so the tree never reaches the model.

### Cost of `enable_vision` - measured 2026-09-06

Rendering happens in the Python process (`namespace._render().to_base64()`), not in the
container, which `fle/cluster/docker-compose.yml:18` caps at `memory: 1024m` regardless.

| | vision off | vision on |
|---|---|---|
| Python RSS | 116.0 MB | 126.8 MB |
| `reset()` | 0.8 s | 2.0 s |
| observation JSON | 41,455 B | 53,903 B |
| decoded PNG | - | 9,335 B |
| 32-step trajectory | 1.26 MB | 1.64 MB |

Sprites are a **one-time 365 MB** in `~/fle-work/.fle` (300 MB spritemaps, 44 MB
`sprites-hr`, 22 MB `sprites`; 9,496 files) taking ~18 minutes to fetch via `fle sprites`.
Without them, rendering silently produces empty images rather than failing.

**These are floor values, measured on an empty world** where `entities` is 2 bytes. Both
the PNG and the 1.2 s render cost grow with the size of the factory. The dominant
trajectory cost is `research` either way, not the image.

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

## Policy API reference - RECORDED 2026-09-06 (`configs/fle_api_reference.md`)

The harness renders an `ENVIRONMENT API` section into the policy prompt from the file
named by the `api_reference` config key. That file now exists, and it is **generated by
FLE itself**, not written by hand (D31).

Why it was needed: on the first live call (Haiku 4.5, mock environment, 2026-09-03) the
policy model invented an API - `game.scan(radius=10)`,
`game.insert('burner-mining-drill', 'coal', 2)`, `game.get_inventory()`. None exist. The
prompt asked for Python without saying which functions were callable, so the model
produced something plausible. The mock hides this because it ignores submitted code
(D10); against live Factorio every policy would fail on its first call.

### The callable surface

**28 tools** under `fle/env/tools/agent/`, each a package of `client.py` (the Python
callable), `server.lua` (the Factorio side) and `agent.md` (its documentation):

```
can_place_entity      connect_entities      craft_item            extract_item
get_connection_amount get_entities          get_entity            get_prototype_recipe
get_research_progress get_resource_patch    harvest_resource      insert_item
inspect_inventory     launch_rocket         move_to               nearest
nearest_buildable     pickup_entity         place_entity          place_entity_next_to
print                 rotate_entity         score                 send_message
set_entity_recipe     set_research          shift_entity          sleep
```

They are **bare names in the policy's namespace** - `place_entity(...)`, not
`env.place_entity(...)`. `FactorioNamespace` (`fle/env/namespace.py:57`) binds each tool
with `setattr(self, name, func)` at line 118. **A policy performs no imports and no
setup** - verified against the installed source, these are bound too:

- `Prototype`, `Direction`, `Position` (lines 128-131);
- direction constants as bare names, with aliases - `UP`/`ABOVE`/`TOP`,
  `RIGHT`/`EAST`, `LEFT`/`WEST`, `DOWN`/`BELOW`/`BOTTOM` (lines 152-155);
- entity classes (line 149) and Python builtins (line 76).

Policy code is executed by `FactorioInstance.eval` / `eval_with_error`
(`fle/env/instance.py:422,441`), which takes an `agent_idx` and a 60-second default
timeout.

### How the reference is generated

`SystemPromptGenerator.generate()`
(`fle/env/utils/controller_loader/system_prompt_generator.py`) composes four parts:

| Part | Source | Chars | ~Tokens |
|---|---|---|---|
| `types` | `fle/env/game_types.py` | 13,129 | 3,282 |
| `entities` | `fle/env/entities.py` | 17,915 | 4,478 |
| `methods` schema | signatures + docstrings from `tools/agent/` | 9,617 | 2,404 |
| manual | the 28 `agent.md` files, concatenated | 76,085 | 19,021 |
| **total** | | **116,827** | **~29,206** |

Regenerate with the script recorded in D31 after any FLE upgrade.

**Do not tidy the generated file.** `system_prompt_generator.py:42-43` concatenates the
closing ``` fence with the sentence after it and no newline between, so the reference
contains a literal line ` ```Here is the manual for the tools available to you `. It is
malformed Markdown and it is exactly what the baseline sends. Correcting it would make
our prompt differ from the published baseline for no gain (D1).

### What the baseline does with it

`fle/eval/entrypoints/gym_eval.py:150` calls `generator.generate_for_agent(...)`, feeds
the result into `GYM_AGENT_INSTRUCTIONS.format(system_prompt=...)`, and installs it as
the conversation's **system message** (`gym_agent.py`, `set_system_message`). So the
published baseline sends the whole ~29k-token reference on **every** call. Ours is sent
as the first section of the policy prompt instead of a system message (D28), but the
content is the same, which is what keeps the comparison honest (D1).

*Note:* `grep cache_control` across FLE returns nothing - **the baseline requests no
prompt caching**. Our `llm.py` records `cache_read_tokens` / `cache_write_tokens` but
does not set `cache_control` either, so today this is ~29k billed input tokens per
policy step. Enabling caching is an open item, not a decided one.

**Still unconfirmed:** that supplying this actually stops the model inventing calls. That
needs a live run (item 18), and until then the fix is reasoned, not demonstrated.

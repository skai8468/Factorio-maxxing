"""Keep a connected Factorio client's camera on the agent, so a run can be watched live.

See docs/decisions.md D45, D47, D59 and docs/fle-integration.md ("Watching a run live").

This is a side channel beside the harness, not part of it. It opens its own RCON
connection, generates the map around spawn before anyone joins, and then ten times a
second makes sure every connected player is locked out of the game, in god mode, and
standing on the agent. It never touches the loop, the recorder, the policy or the
agent: FLE's agent character is a free-standing entity in ``storage.agent_characters``,
attached to no player, and no FLE tool reads a connected player, so where a watcher is
looking cannot reach what the agent does.

The client must join **before** the run starts and stay connected. A join forces a map
save, and a save fails once FLE has loaded its tools into ``storage`` (D43). Joining a
freshly started cluster works; rejoining mid-session does not.

    python -m factorio_maxxing.watch
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable

DEFAULT_INTERVAL = 0.1
"""Seconds between checks. Each check moves the watchers onto the agent, so this is the
camera's update rate: at walking speed a tenth of a second is under a tile (D59)."""

PREGENERATE_RADIUS = 25
"""Chunks around spawn generated before anyone joins - the radius FLE's own
``_generate_chunks`` forces at the start of every run."""

PREGENERATE_LUA = (
    "/silent-command local s = game.surfaces[1] "
    f"s.request_to_generate_chunks({{x = 0, y = 0}}, {PREGENERATE_RADIUS}) "
    "s.force_generate_chunk_requests()"
)
"""Generate the start area now, while nobody is connected (D59).

FLE forces this same generation when a run starts. Measured on a fresh map it stops the
server for 10.4 s; a watching client that is already lagging gives up, reconnects, and
the reconnect's map save fails once FLE has loaded its tools (D43). Done in advance, the
run's own call finds the chunks generated and returns in 0.01 s."""

WATCHER_GROUP = "watchers"

FOLLOW_LUA = " ".join(
    f"""
local g = game.permissions.get_group("{WATCHER_GROUP}")
if not g then
  g = game.permissions.create_group("{WATCHER_GROUP}")
  for _, a in pairs(defines.input_action) do g.set_allows_action(a, false) end
end
local players = 0
for _, p in pairs(game.connected_players) do
  if not (p.permission_group and p.permission_group.group_id == g.group_id) then
    p.permission_group = g
  end
  if p.controller_type ~= defines.controllers.god then
    p.set_controller{{type = defines.controllers.god}}
  end
  players = players + 1
end
if players == 0 then rcon.print("no client") return end
local c = storage.agent_characters and storage.agent_characters[1]
if not (c and c.valid) then rcon.print("no agent") return end
for _, p in pairs(game.connected_players) do
  p.teleport(c.position, c.surface)
end
rcon.print("following")
""".split()
)
"""One check: lock watchers out, put them in god mode, and stand them on the agent (D59).

- **Locked out first**, whether or not a run exists: the ``watchers`` permission group
  allows no input action, so a watcher can look but cannot build, mine, walk or open a
  machine. The human provides text only, and this makes the game enforce it.
- **God mode before any run**, because it has no character for FLE's reset to destroy -
  losing one dropped a client in D38 - so it is set even while there is no agent yet.
- **Teleported onto the agent** every check. A god-mode player renders the real world
  wherever it stands. D47's remote view centred on the agent tracked more smoothly, but
  remote view shows live graphics only where the force has vision, and FLE's agent,
  attached to no player, gives none: measured live, the watcher saw the low-resolution
  map, or black where nothing was charted.

Prints a one-word status so ``watch`` can report what it is doing without the caller
having to read the game."""

STATUS_MESSAGES = {
    "following": "following the agent",
    "no agent": "waiting for a run to create the agent's character",
    "no client": "waiting for a Factorio client to connect",
}

Send = Callable[[str], object]


def pregenerate(send: Send) -> str:
    """Generate the start area unless a client is already connected (D59).

    A connected client would sit through the 10-second stall this avoids, so with
    anyone connected it is skipped and the run's own generation happens as before.
    """
    players = str(send("/sc rcon.print(#game.connected_players)") or "").strip()
    if players not in ("", "0"):
        return "skipped"
    send(PREGENERATE_LUA)
    return "generated"


def follow_once(send: Send) -> str:
    """Check the watchers once and return the status the game reported."""
    response = send(f"/sc {FOLLOW_LUA}")
    return str(response or "").strip()


def watch(
    send: Send,
    interval: float = DEFAULT_INTERVAL,
    *,
    sleep: Callable[[float], None] = time.sleep,
    ticks: int | None = None,
) -> None:
    """Keep watchers on the agent until interrupted, or for ``ticks`` checks.

    Logs only when the status changes, so a long run leaves a short log. A failed check
    is logged and retried on the next tick rather than raised: the camera is a
    convenience, and losing it must never look like the run failing.
    """
    last = None
    count = 0
    while ticks is None or count < ticks:
        try:
            status = follow_once(send)
        except Exception as error:  # noqa: BLE001 - the camera must not stop on a blip
            status = f"error: {error}"
        if status != last:
            logging.info(STATUS_MESSAGES.get(status, status))
            last = status
        count += 1
        if ticks is None or count < ticks:
            sleep(interval)


def connect() -> Send:
    """Open an RCON connection to the local cluster, using FLE's own helpers.

    FLE is imported here rather than at module scope, for the same reason as
    ``RealFactorioEnv``: the offline suite runs on Windows, where FLE is not installed.
    """
    from fle.commons.cluster_ips import get_local_container_ips
    from fle.env import FactorioInstance

    found = get_local_container_ips()
    if not found or not found[0]:
        raise ConnectionError("no running Factorio container; start the cluster first")
    ips, _udp_ports, tcp_ports = found
    client, _address = FactorioInstance.connect_to_server(ips[0], tcp_ports[0])
    return client.send_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factorio_maxxing.watch",
        description="Keep a connected Factorio client's camera on the agent.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between checks (default {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--no-pregenerate",
        action="store_true",
        help="do not generate the start area before a client joins",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("error: --interval must be positive", file=sys.stderr)
        return 2
    try:
        send = connect()
    except (ConnectionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not args.no_pregenerate:
        logging.info("generating the map around spawn before anyone joins (~10 s)")
        try:
            outcome = pregenerate(send)
        except Exception as error:  # noqa: BLE001 - the camera must still start
            outcome = f"failed ({error})"
        if outcome == "skipped":
            logging.warning("a client is already connected; map generation skipped")
        else:
            logging.info("map generation %s", outcome)
    try:
        watch(send, args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

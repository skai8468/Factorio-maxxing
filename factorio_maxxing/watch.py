"""Point a connected Factorio client's camera at the agent, so a run can be watched live.

See docs/decisions.md D45 and docs/fle-integration.md ("Watching a run live").

This is a side channel beside the harness, not part of it. It opens its own RCON
connection, and once a second it switches every connected player to the spectator
controller and moves them to the agent's character. It never touches the loop, the
recorder, the policy or the agent: FLE's agent character is a free-standing entity in
``storage.agent_characters``, attached to no player, and no FLE tool reads a connected
player, so a spectator's position cannot reach what the agent does.

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

DEFAULT_INTERVAL = 1.0
"""Seconds between camera moves. The character walks about nine tiles a second, so a
one-second step keeps it on screen at default zoom without flooding RCON."""

FOLLOW_LUA = " ".join(
    """
local c = storage.agent_characters and storage.agent_characters[1]
if not (c and c.valid) then rcon.print("no agent") return end
local n = 0
for _, p in pairs(game.connected_players) do
  if p.controller_type ~= defines.controllers.spectator then
    p.set_controller{type = defines.controllers.spectator}
  end
  p.teleport(c.position, c.surface)
  n = n + 1
end
if n == 0 then rcon.print("no client") else rcon.print("following") end
""".split()
)
"""One camera move. Spectator first, because FLE's reset destroys every character on the
surface - including a joined player's - and a spectator has none to lose. Re-applied on
every tick rather than once, so a player who joins after ``watch`` starts is picked up.

Prints a one-word status so ``watch`` can report what it is doing without the caller
having to read the game."""

STATUS_MESSAGES = {
    "following": "following the agent",
    "no agent": "waiting for a run to create the agent's character",
    "no client": "waiting for a Factorio client to connect",
}

Send = Callable[[str], object]


def follow_once(send: Send) -> str:
    """Move the camera once and return the status the game reported."""
    response = send(f"/sc {FOLLOW_LUA}")
    return str(response or "").strip()


def watch(
    send: Send,
    interval: float = DEFAULT_INTERVAL,
    *,
    sleep: Callable[[float], None] = time.sleep,
    ticks: int | None = None,
) -> None:
    """Follow the agent until interrupted, or for ``ticks`` moves.

    Logs only when the status changes, so a long run leaves a short log. A failed move
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
        help=f"seconds between camera moves (default {DEFAULT_INTERVAL})",
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
    try:
        watch(send, args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Return types of EA's game code that were checked by hand in the game's
bytecode (tools/ea_stubs.py writes everything else as Any).

Each entry: '<module>.<Class>.<method>' or '<module>.<function>' -> the
annotation text written in the stub. Add one only after reading the game
code (docs/dev/EA-API.md says how, and records what was found).
"""

RETURNS = {
    # (absolute ticks when it ends, rate multiplier); (0, 0) without a timeout.
    'buffs.buff.Buff.get_timeout_time': 'tuple[int, float]',
}

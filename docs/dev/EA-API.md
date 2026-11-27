# EA's game code: what we use, and how we know

The Sims 4 has no official Python API documentation. EA ships its gameplay
code as compiled Python 3.7 (`core.zip`, `simulation.zip` in
`The Sims 4.app/Contents/Data/Simulation/Gameplay`, and in the Windows game
folder). This page records the parts SimSight relies on, each checked in
that code, and how to check more.

## Types from the game: `tools/ea_stubs.py`

`tools/check.sh` writes type stubs for the EA modules the mod imports
(`typings/`, not committed: they come from your installed game) and mypy
checks our code against them. They give every class its real base
classes, methods with their real parameters (and which have defaults),
class and tuned attributes (`INSTANCE_TUNABLES`/`FACTORY_TUNABLES` keys)
and attributes set on `self`. Values are `Any`, except the return types in
`tools/ea_overlay.py`, each checked by hand.

So mypy catches a method or attribute that does not exist, a wrong number
of arguments or a misspelled keyword: the kind of mistake that makes a
feature silently fail in the game. It cannot know what a value *is*
unless the overlay says so.

Without the game installed (GitHub's build), EA modules count as `Any` and
only our own code is checked. Set `SIMS4_APP` if the game is elsewhere.

## Reading the game's code

With the game's Python 3.7 (`$PY37`):

```sh
G="/Applications/EA Games/The Sims 4.app/Contents/Data/Simulation/Gameplay"
unzip -o "$G/simulation.zip" 'buffs/buff.pyc' -d /tmp/ea
$PY37 -I -c 'import dis, marshal, sys; dis.dis(marshal.loads(open(sys.argv[1], "rb").read()[16:]))' /tmp/ea/buffs/buff.pyc
```

Look for the function (its `LOAD_ATTR`/`LOAD_METHOD` names and what it
returns). Then add what you learned below, and to `tools/ea_overlay.py`
when it is a return type.

## What we checked

| What | Fact | Used in |
|---|---|---|
| Console commands, `sims4/commands.py` `parse_args` | Arguments are converted by their annotation, compared with `is int`, `is bool`, `issubclass(..., CustomParam)`: annotations must be the classes, not text. `from __future__ import annotations` makes them text, hence `simsight_commands.real_annotations`. | every console command |
| `buffs.buff.Buff.get_timeout_time()` | Returns `(absolute ticks when it ends, rate multiplier)`; `NO_TIMEOUT = (0, 0)`. `date_and_time.DateAndTime(ticks)` turns the ticks into a time. | moodlet time left (phone) |
| Buff names, `buff_type.buff_name(sim_info)` | A text factory: called with the Sim it is about, the game fills `{M0.father}{F0.mother}` for that Sim. Called with nothing, the male form wins. | moodlet names and descriptions |
| Relationship bits, `bit.display_name(other, sim_info)` | Same: token 0 is the other Sim. | relationship labels |
| Careers, `career.current_track_tuning.get_career_name(sim_info)` | The career's name, as the game's own career panel gets it. Career objects have no `display_name`. | career on the phone |
| `ui.ui_dialog.UiDialogBase.distribute_dialog(self, dialog_type, dialog_msg, immediate=False)` | `immediate` can be positional: wrappers take `*args, **kwargs` (`simsight_patch`). | travel map and pickers |
| `date_and_time.DateAndTime` | A subclass of `int` (ticks). | times |
| `sims4.tuning.tunable.HasTunableFactory.TunableFactory` | A class property: `Thing.TunableFactory()` on the class. | dialogs, menus |

#!/bin/zsh
# Every check of the mod's Python, as the GitHub build runs them:
#   lint and format (ruff), types (mypy), Python 3.7 compile for Mac and
#   Windows, offline tests (mac/test.sh: fake game, every module loads).
# Env: PY37 = the game's Python 3.7 (default: the micromamba env).
#   tools/check.sh          everything
#   tools/check.sh quick    lint, format, types only
set -euo pipefail
ROOT="${0:A:h:h}"
cd "$ROOT"
PY37="${PY37:-$HOME/.local/micromamba/envs/py37/bin/python}"
RUFF=(uvx -q 'ruff==0.17.6')
MYPY=(uvx -q --from 'mypy==1.18.2' mypy)

echo "== Lint (ruff)"
$RUFF check Scripts tools mac/build.py mac/tests
echo "== Format (ruff)"
$RUFF format --check Scripts tools mac/build.py mac/tests
echo "== Errors are reported (tools/check_excepts.py)"
python3 tools/check_excepts.py Scripts
echo "== Types (mypy)"
# EA's game types, when the game is installed here (tools/ea_stubs.py).
GAME="${SIMS4_APP:-/Applications/EA Games/The Sims 4.app}"
if [[ -d "$GAME" && -x "$PY37" ]]; then
  rm -rf typings && "$PY37" -I tools/ea_stubs.py "$GAME" typings --used-by Scripts
else
  echo "(The Sims 4 not found: EA's types are not checked; set SIMS4_APP)"
fi
$MYPY
[[ "${1:-}" == quick ]] && exit 0

echo "== Python 3.7 compile (Mac and Windows)"
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
"$PY37" -I mac/build.py "$ROOT" "$OUT/mac" mac
"$PY37" -I mac/build.py "$ROOT" "$OUT/windows" windows
./mac/test.sh

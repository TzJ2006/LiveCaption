# CLAUDE.md

@AGENTS.md

## Claude Code notes
- Never launch the app in a blocking Bash call: `scripts/start.bat`, `python src/python/win_host.py`,
  and (on this Windows machine) `bash scripts/start.sh` all open a GUI caption overlay that never
  exits. Use `run_in_background` if a launch is really needed, and kill with `scripts/stop.bat`.
- `python src/python/win_host.py --self-test` is the fast sanity check; it exits immediately.
- Do not read or modify `config.json`, `transcripts/`, or `recordings/` — local config and private
  meeting data.

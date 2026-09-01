# RuleArena Benchmark assets

- `development-v1.json` contains the 16 public development cases and their replay evidence.
- `hidden-manifest.json` contains only non-answer metadata for the 8 hidden cases.
- The full hidden suite is not stored in the public repository. Deployment mounts it and sets
  `RULEARENA_PROCESS_ROLE=evaluation` plus `RULEARENA_HIDDEN_SUITE_PATH` for the evaluation job.

The Attack Runtime has no dependency on `rulearena-evaluation`, no hidden-case loader, and no file
tool. This prevents accidental prompt, trace, SSE, or public API exposure. A repository maintainer can
still alter source code to exfiltrate a mounted file; process/container permissions are therefore the
final deployment boundary, not Python object privacy.

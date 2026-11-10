# Memory Bench tests

Run from the repository root in the preinstalled `behavior` environment:

```bash
OMNIGIBSON_HEADLESS=1 python -m pytest memory_bench/tests -q
```

The tests exercise production code with temporary recordings and simulation or hardware substitutes. They do not launch Isaac Sim or connect to teleoperation hardware.

| Location | Coverage |
| --- | --- |
| `test_session.py` | Current YAML configurations, paths, task order and configuration validation |
| `tasks/` | Registered task variants, instructions and live BDDL condition evaluation |
| `collection/` | Instance preparation, asset identity, recording lifecycle and control synchronization |
| `episodes/` | Composition, Bridge generation, rendering and export readback |
| `utils/` | Scene state, recordings, protected writes, navigation and entry selection |
| `tools/` | Recording downloads and replay commands |

Shared fixtures live in `conftest.py`; scene and HDF5 builders live in `helpers.py`. Test modules do not import other test modules. Pipeline fixtures register a minimal test task locally; tests of benchmark definitions use the current registered tasks and configurations.

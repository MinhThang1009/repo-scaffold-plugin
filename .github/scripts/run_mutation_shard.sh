#!/usr/bin/env bash
# Preserve the engine verdict while collecting bounded, non-verdict diagnostics.
set -uo pipefail
shard_index="${1:?missing shard index}"
if [[ ! "$shard_index" =~ ^([0-9]|[1-9][0-9]|1[01][0-9]|12[0-7])$ ]]; then
  printf 'Invalid shard index.\n' >&2
  exit 2
fi

REPO_SCAFFOLD_MUTATION_DIAGNOSTICS=1 python scripts/run_mutation_testing.py --max-children 4 --shard-index "$shard_index" &
mutation_pid=$!
python -u - "$mutation_pid" "$shard_index" <<'PY' &
import json
import os
import select
from pathlib import Path, PurePosixPath
import sys
import time

MAX_READ = 65536
MAX_PROCESSES = 4096
MAX_WORKERS = 16
MAX_SAMPLES = 360
MAX_OUTPUT = 1024 * 1024
INTERVAL = 30


def read(path, limit=MAX_READ):
    try:
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    return value if len(value) <= limit else None


def number(value):
    return int(value) if value and len(value) <= 20 and value.isascii() and value.isdecimal() else None


def process(proc, pid):
    raw = read(proc / str(pid) / "stat", 8192)
    if raw is None:
        return None
    fields = raw.decode("utf-8", errors="replace").rsplit(")", 1)[-1].split()
    if len(fields) < 20:
        return None
    parent, birth = number(fields[1]), number(fields[19])
    return (parent, birth) if parent is not None and birth is not None else None


def counters(path, keys):
    raw = read(path)
    if raw is None:
        return None
    result = {}
    for line in raw.decode("ascii", errors="replace").splitlines():
        fields = line.replace(":", "").split()
        if len(fields) >= 2 and fields[0] in keys:
            result[fields[0]] = number(fields[1])
    return result


def sample(proc, cgroups, pid, birth, names):
    parent_identity = process(proc, pid)
    if parent_identity is None or parent_identity[1] != birth:
        return None
    parents = {}
    partial = False
    with os.scandir(proc) as entries:
        for count, entry in enumerate(entries):
            if count >= MAX_PROCESSES:
                partial = True
                break
            child = number(entry.name)
            if child is not None:
                identity = process(proc, child)
                if identity is not None:
                    parents[child] = identity
    owned = {pid}
    for _ in range(len(parents)):
        expanded = owned | {child for child, (parent, _) in parents.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    workers = []
    processes = []
    engine_memory = counters(proc / str(pid) / "status", {"VmRSS", "VmHWM"})
    for count, child in enumerate(sorted(owned - {pid})):
        if count >= MAX_WORKERS:
            partial = True
            break
        memory = counters(proc / str(child) / "status", {"VmRSS", "VmHWM"})
        if process(proc, child) != parents[child]:
            partial = True
            continue
        processes.append({"pid": child, "memory_kib": memory})
        comm = read(proc / str(child) / "comm", 64)
        if comm is None or not comm.startswith(b"mutmut: "):
            continue
        title = read(proc / str(child) / "cmdline", 8192)
        if title is None:
            continue
        key = title.split(b"\0", 1)[0].decode("utf-8", errors="replace").removeprefix("mutmut: ")
        # Never emit arbitrary command lines: only exact keys assigned to this shard.
        if key in names and process(proc, child) == parents[child]:
            workers.append({"pid": child, "mutant": key, "memory_kib": memory})
    cg = None
    membership = read(proc / str(pid) / "cgroup", 4096)
    if membership is not None:
        for line in membership.decode("utf-8", errors="replace").splitlines():
            if line.startswith("0::"):
                relative = line[3:].removeprefix("/")
                path = PurePosixPath(relative)
                if not path.is_absolute() and ".." not in path.parts and "\\" not in relative and not any(ord(c) < 32 for c in relative) and path.as_posix() == (relative or "."):
                    root = cgroups / relative
                    memory_current = read(root / "memory.current", 128)
                    cg = {"memory_bytes": number(memory_current.decode("ascii", errors="replace").strip()) if memory_current else None, "events": counters(root / "memory.events", {"oom", "oom_kill", "high", "max"})}
    if process(proc, pid) != parent_identity:
        return None
    return {"scan_partial": partial, "system_memory_kib": counters(proc / "meminfo", {"MemAvailable", "MemTotal"}), "cgroup": cg, "engine_memory_kib": engine_memory, "processes": processes, "worker_identity_status": "observed" if workers else "unavailable", "workers": workers}


def main():
    pid, shard = map(int, sys.argv[1:])
    raw = read(Path("mutants/mutation-shards.json"), 16 * 1024 * 1024)
    if raw is None:
        raise ValueError("diagnostic plan unavailable or oversized")
    document = json.loads(raw)
    selected = document["shards"][shard]
    if not isinstance(selected, list) or len(selected) > 100000 or any(not isinstance(key, str) or len(key) > 4096 for key in selected):
        raise ValueError("diagnostic key inventory invalid")
    names = set(selected)
    proc = Path("/proc")
    initial = process(proc, pid)
    if initial is None or not hasattr(os, "pidfd_open"):
        raise ValueError("mutation process unavailable")
    pidfd = os.pidfd_open(pid)
    started = time.monotonic()
    written = 0
    try:
        for _ in range(MAX_SAMPLES):
            value = sample(proc, Path("/sys/fs/cgroup"), pid, initial[1], names)
            if value is None:
                break
            value["elapsed_seconds"] = round(time.monotonic() - started, 1)
            payload = "mutation-diagnostic " + json.dumps(value, ensure_ascii=True) + "\n"
            size = len(payload.encode("utf-8"))
            if written + size > MAX_OUTPUT - 256:
                print('mutation-diagnostic {"status":"output-budget-exhausted"}', flush=True)
                break
            print(payload, end="", flush=True)
            written += size
            # A pidfd wakes on engine exit, without signaling a recycled PID.
            if select.select([pidfd], [], [], INTERVAL)[0]:
                break
    finally:
        os.close(pidfd)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, IndexError, TypeError, RecursionError):
        # Diagnostics are optional evidence, never a replacement engine verdict.
        print('mutation-diagnostic {"status":"unavailable"}', flush=True)
PY
observer_pid=$!
wait "$mutation_pid"
mutation_status=$?
wait "$observer_pid" 2>/dev/null || true
exit "$mutation_status"

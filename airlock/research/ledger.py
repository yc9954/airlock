"""Append-only research ledger, one directory per lab.

    runs/labs/<lab_id>/ledger.jsonl    every experiment: spec, control, treatment,
                                       metric, delta, gate, reason (one JSON per line)
    runs/labs/<lab_id>/champion.json   the current champion, replaced only on `promote`
    runs/labs/<lab_id>/state.json      families, seals, stagnation — for `why` and resume

The ledger is the research record (W&B is a visualisation layer, the ledger is
the science): failed experiments and unlucky seeds are written too. Never edit a
line; append a correction.
"""
import json
import os
import threading
import time


class Ledger:
    def __init__(self, lab_id, runs_dir):
        self.lab_id = lab_id
        self.dir = os.path.join(runs_dir, lab_id)
        self.path = os.path.join(self.dir, "ledger.jsonl")
        self.champion_path = os.path.join(self.dir, "champion.json")
        self.state_path = os.path.join(self.dir, "state.json")
        self._lock = threading.Lock()
        self._n = 0
        os.makedirs(self.dir, exist_ok=True)
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self._n = sum(1 for line in f if line.strip())

    # ---- experiments ----
    def append(self, record):
        """Append one experiment record; returns it with `seq` and `ts` stamped."""
        rec = dict(record)
        with self._lock:
            rec.setdefault("ts", round(time.time(), 3))
            rec["seq"] = self._n
            rec.setdefault("lab_id", self.lab_id)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            self._n += 1
        return rec

    def __len__(self):
        return self._n

    def load(self):
        return load_ledger(self.path)

    def find(self, spec_id=None, hypothesis_id=None, family=None):
        out = []
        for r in self.load():
            if spec_id and r.get("spec_id") != spec_id:
                continue
            if hypothesis_id and r.get("hypothesis_id") != hypothesis_id:
                continue
            if family and r.get("family") != family:
                continue
            out.append(r)
        return out

    # ---- champion ----
    def set_champion(self, champion):
        data = dict(champion)
        data.setdefault("ts", round(time.time(), 3))
        data.setdefault("lab_id", self.lab_id)
        _write_json(self.champion_path, data)
        return data

    def champion(self):
        return _read_json(self.champion_path)

    # ---- state ----
    def save_state(self, state):
        _write_json(self.state_path, dict(state, ts=round(time.time(), 3)))

    def state(self):
        return _read_json(self.state_path)


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_ledger(path):
    """All records of a ledger.jsonl (skips blank / corrupt lines rather than failing)."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def load_champion(runs_dir, lab_id):
    return _read_json(os.path.join(runs_dir, lab_id, "champion.json"))


def list_labs(runs_dir):
    """[{lab_id, experiments, champion}] for every lab directory under runs_dir."""
    out = []
    try:
        names = sorted(os.listdir(runs_dir))
    except OSError:
        return out
    for name in names:
        d = os.path.join(runs_dir, name)
        if not os.path.isdir(d):
            continue
        out.append({"lab_id": name,
                    "experiments": len(load_ledger(os.path.join(d, "ledger.jsonl"))),
                    "champion": _read_json(os.path.join(d, "champion.json"))})
    return out


__all__ = ["Ledger", "load_ledger", "load_champion", "list_labs"]

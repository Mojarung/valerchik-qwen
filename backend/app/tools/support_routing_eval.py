"""Held-out routing acceptance: train/calibration/test, never score a model trained on the test."""

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np

from app.services.routing import RouteSettings, TopicModel, apply_rules, route_from_proba, tokenize
from app.tools.lines_eval import build_dataset, gold_target, read_rows, split, subsample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ds = build_dataset(read_rows(args.export))
    train, held = split(ds.appeals)
    test = subsample(held, ds.appeals, 300, seed=73)
    remaining = [i for i in held if i not in set(test)]
    validation = subsample(remaining, ds.appeals, 600, seed=74)
    bags = {i: set(tokenize(ds.appeals[i].text)) for i in range(len(ds.appeals))}
    inverted = defaultdict(set)
    for i in train:
        for word in bags[i]:
            inverted[word].add(i)
    near = set()
    for i in test + validation:
        counts = Counter(j for word in bags[i] for j in inverted[word])
        near.update(j for j, n in counts.items() if n / max(1, len(bags[i] | bags[j])) >= 0.85)
    train = [i for i in train if i not in near]
    print(
        f"Training {len(train)}; calibration {len(validation)}; test {len(test)}; "
        f"near duplicates excluded {len(near)}",
        flush=True,
    )
    start = time.monotonic()
    model = TopicModel.fit([ds.appeals[i].text for i in train], [ds.appeals[i].topic for i in train])
    model.save(args.output / "model", {"support_eval": True, "training_samples": len(train)})
    settings = replace(RouteSettings.from_dyn(None), line_min_confidence=0)

    def predictions(indices):
        output = []
        for i in indices:
            item = ds.appeals[i]
            route = route_from_proba(model.proba(item.text), model.labels, settings)
            route = apply_rules(route, item.text, "", settings)
            target = f"line:{route.line}" if not route.external else route.addressee
            output.append(
                {
                    "id": hashlib.sha256(item.text.encode()).hexdigest()[:16],
                    "gold": gold_target(item, settings),
                    "predicted": target,
                    "confidence": float(route.proba),
                    "source": route.source,
                }
            )
        return output

    calibration = predictions(validation)
    choices = []
    for threshold in np.arange(0.4, 1.0, 0.01):
        selected = [r for r in calibration if r["confidence"] >= threshold or r["source"] == "rules"]
        precision = sum(r["gold"] == r["predicted"] for r in selected) / max(1, len(selected))
        choices.append((len(selected) / len(calibration), precision, float(threshold)))
    eligible = [x for x in choices if x[1] >= 0.9]
    chosen = max(eligible, default=max(choices, key=lambda x: x[1]), key=lambda x: x[0])
    rows = predictions(test)
    selected = [r for r in rows if r["confidence"] >= chosen[2] or r["source"] == "rules"]
    precision = sum(r["gold"] == r["predicted"] for r in selected) / max(1, len(selected))
    report = {
        "train": len(train),
        "test": len(rows),
        "calibration": len(validation),
        "near_duplicates_removed": len(near),
        "threshold": chosen[2],
        "precision": precision,
        "coverage": len(selected) / len(rows),
        "accepted": precision >= 0.9 and len(selected) / len(rows) >= 0.7,
        "elapsed_s": time.monotonic() - start,
        "rows": rows,
        "label_provenance": (
            "Export topic -> agreed thematic line; incident override. Not a human-reviewed gold set."
        ),
    }
    (args.output / "routing.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

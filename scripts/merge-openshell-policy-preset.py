#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Idempotently merge curated network policy presets into a live OpenShell policy."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import yaml


PRESETS = {
    "telegram_bot": {
        "name": "telegram_bot",
        "endpoints": [
            {"host": "api.telegram.org", "port": 443},
            {"host": "telegram.org", "port": 443},
            {"host": "t.me", "port": 443},
        ],
        "binaries": [
            {"path": "/usr/bin/openclaw"},
            {"path": "/usr/bin/node"},
        ],
    },
    "whatsapp_web": {
        "name": "whatsapp_web",
        "endpoints": [
            {"host": "web.whatsapp.com", "port": 443},
            {"host": "web.whatsapp.net", "port": 443},
            {"host": "static.whatsapp.net", "port": 443},
            {"host": "mmg.whatsapp.net", "port": 443},
            {"host": "g.whatsapp.net", "port": 443},
        ],
        "binaries": [
            {"path": "/usr/bin/openclaw"},
            {"path": "/usr/bin/node"},
        ],
    },
}


def _merge_endpoint_list(existing: list[dict], additions: list[dict]) -> list[dict]:
    merged = list(existing)
    seen = {
        (
            item.get("host"),
            item.get("port"),
            item.get("protocol"),
            item.get("tls"),
            item.get("enforcement"),
            yaml.safe_dump(item.get("rules"), sort_keys=True),
        )
        for item in existing
    }
    for item in additions:
        key = (
            item.get("host"),
            item.get("port"),
            item.get("protocol"),
            item.get("tls"),
            item.get("enforcement"),
            yaml.safe_dump(item.get("rules"), sort_keys=True),
        )
        if key not in seen:
            merged.append(deepcopy(item))
            seen.add(key)
    return merged


def _merge_binaries(existing: list, additions: list[dict]) -> list:
    normalized: list[dict] = []
    seen: set[str] = set()

    for item in existing:
        path = item["path"] if isinstance(item, dict) else str(item)
        normalized.append({"path": path})
        seen.add(path)

    for item in additions:
        path = item["path"]
        if path not in seen:
            normalized.append(deepcopy(item))
            seen.add(path)

    return normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--preset",
        action="append",
        required=True,
        choices=sorted(PRESETS),
        help="Policy preset name to merge into network_policies.",
    )
    args = parser.parse_args()

    policy = yaml.safe_load(args.input.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise SystemExit("Policy root must be a mapping")

    network_policies = policy.setdefault("network_policies", {})

    for preset_name in args.preset:
        preset = PRESETS[preset_name]
        current = deepcopy(network_policies.get(preset_name, {}))
        current["name"] = preset["name"]
        current["endpoints"] = _merge_endpoint_list(
            current.get("endpoints", []), preset["endpoints"]
        )
        current["binaries"] = _merge_binaries(
            current.get("binaries", []), preset["binaries"]
        )
        network_policies[preset_name] = current

    args.output.write_text(
        yaml.safe_dump(policy, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

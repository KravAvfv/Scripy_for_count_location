from __future__ import annotations

import json
from pathlib import Path

from sitesizer import cli
from sitesizer.core.catalog import Catalog


def test_json_mode(tmp_path: Path) -> None:
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"name": "A", "sockets": 10, "cameras": 5, "tier": 3}), encoding="utf-8")
    out = tmp_path / "out.json"
    assert cli.main(["--json", str(inp), "--out", str(out), "--quiet"]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["switches"]["access_switch"]["model"] == "FS-148F"


def test_interactive_flow_matches_prototype_questions(compat_catalog: Catalog) -> None:
    answers = iter(["1", "10", "5", "1", "3", "1", "1", "n", "y", "n", "Тест"])
    site = cli.collect_inputs(compat_catalog, input_fn=lambda _p: next(answers))
    assert (site.sockets, site.cameras, site.tier, site.aggregation) == (10, 5, 1, "yes")
    assert site.ap_groups[0].zone == "low_density" and site.redundant_psu is False


def test_interactive_retries_bad_input(compat_catalog: Catalog, capsys) -> None:
    answers = iter(["9", "1", "abc", "-3", "10", "0", "0", "3", "", "", "", ""])
    site = cli.collect_inputs(compat_catalog, input_fn=lambda _p: next(answers))
    assert site.sockets == 10 and site.tier == 3 and site.aggregation == "auto"
    assert "ціле число" in capsys.readouterr().out

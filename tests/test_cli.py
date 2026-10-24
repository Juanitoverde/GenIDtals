import json

import cv2

from genidtals import decode_key
from genidtals.cli import main


def test_cli_flow(tmp_path, specimens, captures, capsys, monkeypatch):
    monkeypatch.delenv("GENIDTALS_KEYS", raising=False)
    monkeypatch.delenv("GENIDTALS_KEYRING", raising=False)
    paths = []
    for i, img in enumerate(captures(specimens[3], 3)):
        p = tmp_path / f"img{i}.png"
        cv2.imwrite(str(p), img)
        paths.append(str(p))
    other = tmp_path / "other.png"
    cv2.imwrite(str(other), captures(specimens[4], 1)[0])
    home = ["--home", str(tmp_path / "home")]

    assert main(home + ["enroll", "--user", "carol", "--images", *paths[:2]]) == 0
    assert main(home + ["authenticate", "--user", "carol", "--image", paths[2]]) == 0
    assert main(home + ["authenticate", "--user", "carol", "--image", str(other)]) == 1
    assert main(home + ["authenticate", "--user", "carol", "--image", "missing.png"]) == 2
    capsys.readouterr()
    assert main(home + ["--json", "status", "--user", "carol"]) == 0
    assert json.loads(capsys.readouterr().out)["enrolled"] is True
    assert main(home + ["rotate-keys"]) == 0
    assert main(home + ["authenticate", "--user", "carol", "--image", paths[2]]) == 0
    assert main(home + ["delete", "--user", "carol"]) == 0
    assert (tmp_path / "home" / "keyring.json").stat().st_mode & 0o077 == 0


def test_cli_sqlite_store(tmp_path, specimens, captures, monkeypatch):
    monkeypatch.delenv("GENIDTALS_KEYS", raising=False)
    paths = []
    for i, img in enumerate(captures(specimens[5], 3)):
        p = tmp_path / f"img{i}.png"
        cv2.imwrite(str(p), img)
        paths.append(str(p))
    args = ["--home", str(tmp_path / "home"), "--store", str(tmp_path / "templates.db")]
    assert main(args + ["enroll", "--user", "dana", "--images", *paths[:2]]) == 0
    assert main(args + ["authenticate", "--user", "dana", "--image", paths[2]]) == 0
    assert (tmp_path / "templates.db").exists()


def test_keygen(capsys):
    assert main(["keygen"]) == 0
    assert len(decode_key(capsys.readouterr().out)) == 32
    assert main(["keygen", "--api-key"]) == 0
    assert len(capsys.readouterr().out.strip()) >= 32


def test_benchmark_and_calibrate(tmp_path, capsys):
    assert main(["--json", "benchmark", "--subjects", "4", "--images", "4", "--seed", "9"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["eer"] == 0.0
    out = tmp_path / "cal.json"
    assert main(["--json", "calibrate", "--subjects", "6", "--images", "5", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["source"].startswith("synthetic")


def test_diagnostics_and_openapi(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GENIDTALS_KEYS", raising=False)
    assert main(["--home", str(tmp_path), "diagnostics"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert main(["openapi", "--out", str(tmp_path / "openapi.json")]) == 0
    assert "paths" in json.loads((tmp_path / "openapi.json").read_text())

from pathlib import Path


def test_turnstile_http_client_is_a_runtime_dependency():
    requirements = (
        Path(__file__).resolve().parents[2] / "requirements.txt"
    ).read_text(encoding="utf-8")

    package_names = {
        line.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "httpx" in package_names

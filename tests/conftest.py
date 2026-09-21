"""
Shared fixtures for all runner tests.
"""

import pytest

# ── Hermetic by default ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Point the runner's home at a throwaway directory for every test.

    Enforcement switches on when a policy exists at `$ANNONA_HOME/policy.yaml`,
    which is correct behaviour and made the suite depend on the machine it ran
    on: writing a policy in a real home turned twenty-six unrelated tests red,
    because they silently started routing through the perimeter.

    A test that reads the developer's home is not testing the product, it is
    testing the developer.
    """
    home = tmp_path_factory.mktemp("annona-home")
    monkeypatch.setenv("ANNONA_HOME", str(home))
    monkeypatch.setenv("AKAION_HOME", str(home))
    monkeypatch.setenv("AKAION_BRAIN_DIR", str(home / "vault"))
    return home


# ── Minimal config fixture ────────────────────────────────────────────────────


@pytest.fixture
def minimal_config(tmp_path):
    """Minimal config: every tool enabled, permissions wide open."""
    return {
        "tools": {"enabled": ["filesystem", "shell", "document_reader", "explorer"]},
        "permissions": {
            "filesystem": {
                "allowed_paths": [str(tmp_path)],
                "denied_paths": [],
                "max_file_size_mb": 50,
            },
            "shell": {
                "enabled": True,
                "allowed_commands": [],  # empty means everything is allowed
                "denied_commands": ["rm -rf /"],
            },
            "network": {"enabled": True},
        },
        "ai": {
            "provider": "akaion",
            "temperature": 0.7,
            "max_tokens": 4000,
        },
        "logging": {"level": "DEBUG", "file": str(tmp_path / "test.log")},
    }


@pytest.fixture
def open_config(tmp_path):
    """Fully open config: no allowed_paths means everything is allowed."""
    return {
        "tools": {"enabled": ["filesystem", "shell", "document_reader", "explorer"]},
        "permissions": {
            "filesystem": {
                "allowed_paths": [],
                "denied_paths": [],
                "max_file_size_mb": 50,
            },
            "shell": {
                "enabled": True,
                "allowed_commands": [],
                "denied_commands": [],
            },
        },
    }


# ── Sample directory tree ─────────────────────────────────────────────────────


@pytest.fixture
def sample_dir(tmp_path):
    """
    Creates a small sample directory tree:
      sample_dir/
        reports/
          q1_report.txt
          q2_report.txt
          financial_summary.csv
        docs/
          readme.md
          notes.txt
          config.json
        scripts/
          deploy.sh
          build.py
        .hidden_file
    """
    # reports/
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "q1_report.txt").write_text(
        "Q1 Report\n\nRevenue: 1,200,000\nExpenses: 800,000\nProfit: 400,000\n"
        "Key highlights:\n- Product launch successful\n- Customer growth +15%\n"
    )
    (reports / "q2_report.txt").write_text(
        "Q2 Report\n\nRevenue: 1,500,000\nExpenses: 950,000\nProfit: 550,000\n"
        "Key highlights:\n- New market expansion\n- Headcount +20\n"
    )
    (reports / "financial_summary.csv").write_text(
        "Quarter,Revenue,Expenses,Profit\n"
        "Q1,1200000,800000,400000\n"
        "Q2,1500000,950000,550000\n"
        "Q3,1800000,1100000,700000\n"
    )

    # docs/
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "readme.md").write_text(
        "# Akaion Runner\n\nLocal agent for executing tasks.\n\n## Setup\nRun `./install.sh`\n"
    )
    (docs / "notes.txt").write_text(
        "Meeting notes 2024-01-15\n\nAgenda:\n1. Revenue forecast\n2. Team expansion\n"
        "3. Product roadmap\n\nAction items:\n- Review Q1 financials\n- Hire 5 engineers\n"
    )
    (docs / "config.json").write_text(
        '{\n  "version": "1.0.0",\n  "debug": false,\n  "max_workers": 4,\n'
        '  "database": {"host": "localhost", "port": 5432}\n}\n'
    )

    # scripts/
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "deploy.sh").write_text(
        "#!/bin/bash\nset -e\necho 'Deploying...'\ndocker build -t app .\ndocker push app\n"
    )
    (scripts / "build.py").write_text(
        '#!/usr/bin/env python3\n"""Build script."""\nimport subprocess\n\n'
        "def build():\n    subprocess.run(['python', 'setup.py', 'sdist'])\n\n"
        "if __name__ == '__main__':\n    build()\n"
    )

    # hidden file
    (tmp_path / ".hidden_file").write_text("secret=do_not_read\n")

    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_catalog_cache():
    """Catalog indexes are cached in-process; one test's index is not another's."""
    from runner.skills import catalog

    catalog._indexes.clear()
    yield
    catalog._indexes.clear()

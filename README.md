Propagation Exporter
====================

A small tool that watches systemd journal entries from `opendnssec-signer.service`, parses zone update stats, checks downstream DNS SOA serial propagation, and exposes Prometheus metrics.

Install
-------

This project uses `pyproject.toml`. Install in your environment of choice.

Run
---

- As a console script (recommended):

	propagation-exporter -c /etc/coralogix-exporter/zones.yaml --metrics-port 8000

- As a module:

	python -m propagation_exporter -c /etc/coralogix-exporter/zones.yaml

- Legacy script (still supported):

	python main.py -c /etc/coralogix-exporter/zones.yaml

Testing with uv
----------------

This project uses [uv](https://github.com/astral-sh/uv) for Python environment and dependency management in development.

- Install dev deps (once):

	uv sync --extra dev

- Run tests with coverage:

	uv run pytest -q --cov=propagation_exporter --cov-report=term-missing

- Optional: type and style checks:

	uv run mypy propagation_exporter
	uv run flake8 propagation_exporter

CLI options
-----------

- -c, --config-file: YAML with zones and nameservers
- --metrics-port: Prometheus HTTP port (default 8000)
- --zone / --ns: Ad-hoc SOA serial checks without systemd
- --stats-regex: Override the regex used to parse journal "[STATS]" lines.

Regex parsing
-------------

By default, the parser extracts zone, serial, and RR count from lines like:

	[STATS] example.com 2024072826 RR[count=4 time=0(sec)] ...

Default pattern:

	^\[STATS\]\s+(?P<zone>\S+)\s+(?P<serial>\d+)\s+RR\[count=(?P<rr_count>\d+)

You can override it via CLI `--stats-regex` or programmatically when creating `ZoneManager`.

Module layout
-------------

- propagation_exporter/
	- __init__.py: public exports
	- __main__.py: `python -m` entry
	- cli.py: argument parsing and app wiring
	- journal.py: systemd journal reader
	- zone.py: ZoneInfo, ZoneConfig, ZoneManager, and parsing
	- dns_utils.py: DNSChecker utilities
	- metrics.py: Prometheus metrics

Prometheus metrics
------------------

- zone_out_of_sync{zone}
- zone_propagation_delay_seconds{zone, nameserver, serial}

Config file
-----------

YAML example:

	zones:
		example.com.:
			primary_nameserver: 192.0.2.1
			downstream_nameservers:
				- 192.0.2.2
				- 192.0.2.3

Notes
-----

- Ensure `systemd-python`, `dnspython`, `PyYAML`, and `prometheus-client` are available in the runtime environment.

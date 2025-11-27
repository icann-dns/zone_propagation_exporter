#!/usr/bin/env python3
import logging
import threading
from argparse import ArgumentParser, Namespace
from pathlib import Path

from prometheus_client import start_http_server  # type: ignore[import-untyped]

from .dns_utils import DNSChecker
from .journal import JournalReader
from .zone import ZoneManager


def get_log_level(args_level: int) -> int:  # pragma: no cover
    """Convert an integer to a logging log level.

      Arguments:
          args_level (int): The log level as an integer

      Returns:
          int: the logging loglevel
    """
    return {
        0: logging.ERROR,
        1: logging.WARN,
        2: logging.INFO,
        3: logging.DEBUG,
    }.get(args_level, logging.DEBUG)


def get_args() -> Namespace:  # pragma: no cover
    """Parse and return the arguments.

    Returns:
        Namespace: The parsed argument namespace
    """
    parser = ArgumentParser(
        description="Propagation exporter: journal reader and metrics emitter"
    )
    parser.add_argument('-v', '--verbose', action='count', default=0)
    parser.add_argument(
        '-c', '--config-file', type=Path,
        default=Path('/etc/coralogix-exporter/zones.yaml'),
        help='Path to the zone configuration file'
    )
    parser.add_argument(
        '--stats-regex', type=str, default=None,
        help=(
                'Regex pattern to parse journal stats lines; must include named groups'
                'zone, serial, rr_count'
            )
    )
    # Ad-hoc SOA check mode (no systemd required)
    parser.add_argument('--zone', help='DNS zone to check (e.g., example.com.)')
    parser.add_argument('--ns', dest='nameservers', action='append', default=[],
                        help='Downstream nameserver (IP or host). Repeat for multiple.')
    parser.add_argument('--port', type=int, default=53, help='DNS port (default: 53)')
    parser.add_argument(
        '--timeout', type=float, default=3.0, help='DNS timeout seconds (default: 3.0)'
    )
    parser.add_argument(
        '--tcp', action='store_true', help='Use TCP for DNS queries (default: UDP)')
    parser.add_argument(
        '--metrics-port', type=int, default=8000,
        help='Prometheus metrics HTTP server port (default: 8000)')
    return parser.parse_args()


def configure_logging(log_level: int) -> None:
    """Configure logging with thread names for better debugging."""
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(threadName)-15s - %(levelname)-8s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main() -> None:
    args = get_args()
    log_level = get_log_level(args.verbose)
    configure_logging(log_level)

    # Quick SOA check mode if --zone and at least one --ns provided
    if args.zone and args.nameservers:
        zone = args.zone
        # Ensure zone ends with a dot for absolute queries
        if not zone.endswith('.'):
            zone = zone + '.'
        logging.info(
            "Checking SOA serial for %s on %d nameserver(s)...",
            zone,
            len(args.nameservers)
        )
        for ns in args.nameservers:
            serial = DNSChecker.resolve_soa_serial(
                zone, ns, port=args.port, timeout=args.timeout, tcp=args.tcp
            )
            if serial is None:
                print(f"{ns}\tNO_ANSWER")
            else:
                print(f"{ns}\t{serial}")
        return

    logging.info("Starting Prometheus metrics server on port %d...", args.metrics_port)
    start_http_server(args.metrics_port)

    logging.info("Starting systemd journal reader in background thread...")
    zone_manager = ZoneManager.load_from_file(
        args.config_file, zone_serial_regex=args.stats_regex
    )
    journal_reader = JournalReader(zone_manager)

    journal_thread = threading.Thread(
        target=journal_reader.run,
        name="journal-reader",
        daemon=True,
    )
    journal_thread.start()

    # Keep the main thread alive while background threads run
    try:
        journal_thread.join()
    except KeyboardInterrupt:
        logging.info("Shutting down on user interrupt...")

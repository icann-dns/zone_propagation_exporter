import logging
import threading
from datetime import datetime
from time import sleep
from typing import Any, Dict, List

from . import metrics
from .dns_utils import DNSChecker

logger = logging.getLogger(__name__)


class ZoneInfo(object):
    """Information about a DNS zone and its nameserver."""

    def __init__(
        self,
        name: str,
        serial: int,
        update_time: datetime,
        dns_name: str = "",
    ) -> None:
        self.name: str = name
        self.serial: int = serial
        self.update_time: datetime = update_time
        self.dns_name: str = dns_name
        # Resolve DNS name for metrics labels if possible
        name_server = DNSChecker.resolve_a_record(self.dns_name)
        if name_server is None:
            name_server = self.dns_name
        self.name_server = name_server


class ZoneConfig(object):
    def __init__(
        self,
        name: str,
        primary_nameserver: ZoneInfo,
        downstream_nameservers: List[ZoneInfo],
        # Assume the zone is in sync initially
        synced: bool = True,
    ) -> None:
        self.name = name
        self.primary_nameserver = primary_nameserver
        self.downstream_nameservers = downstream_nameservers
        self.synced = synced
        # Track last warning time per nameserver to throttle warnings
        self._last_warning_time: Dict[str, datetime] = {}
        # Track which nameservers have been logged as synced for current serial
        self._synced_logged: Dict[str, int] = {}

    def __repr__(self) -> str:
        return (
            f"ZoneConfig(name={self.name}, "
            f"primary_nameserver={self.primary_nameserver.dns_name}, "
            f"downstream_nameservers={[ns.dns_name for ns in self.downstream_nameservers]}, "
            f"synced={self.synced})"
        )

    def __str__(self) -> str:
        return f"ZoneConfig({self.name}, {self.primary_nameserver.serial})"

    def check_downstream_propagation(self) -> None:
        """Check if the zone is properly propagated to all downstream nameservers."""
        zone = self.name
        primary_serial = self.primary_nameserver.serial
        primary_update_time = self.primary_nameserver.update_time
        self.synced = False

        while True:
            current_time = datetime.now()

            for ns in self.downstream_nameservers:
                # Skip if this nameserver has already synced (its serial matches primary)
                if ns.serial == primary_serial:
                    continue

                logger.debug(
                    "Checking propagation for zone %s on nameserver %s",
                    zone, ns.name_server
                )
                downstream_serial = DNSChecker.resolve_soa_serial(zone, ns.name_server)

                if downstream_serial is None:
                    logger.warning(
                        "No serial obtained from %s for %s", ns.name_server, zone
                    )
                    continue

                logger.debug(
                    "Zone %s: %s serial=%s (primary=%s)",
                    zone, ns.name_server, downstream_serial, primary_serial
                )
                if downstream_serial < primary_serial:
                    ns.serial = downstream_serial
                    ns.update_time = datetime.now()

                    # Update propagation delay metric (still propagating)
                    propagation_delay = (current_time - primary_update_time).total_seconds()
                    for metric in [metrics.zone_propagation_delay, metrics.zone_out_of_sync]:
                        metric.labels(
                            zone=zone,
                            nameserver=ns.dns_name,
                            serial=str(primary_serial),
                        ).set(propagation_delay)
                    if propagation_delay > 300:
                        # Only emit warning if at least 5 minutes have passed since last warning
                        last_warning = self._last_warning_time.get(ns.name_server)
                        if last_warning is None or (
                            current_time - last_warning
                        ).total_seconds() >= 60:
                            logger.warning(
                                "Downstream %s does not match %s: downstream=%s != primary=%s",
                                ns.name_server,
                                zone,
                                downstream_serial,
                                primary_serial,
                            )
                            self._last_warning_time[ns.name_server] = current_time
                    continue

                if downstream_serial > primary_serial:
                    logger.warning(
                        "Downstream %s has higher serial than primary for %s:"
                        " downstream=%s > primary=%s (assuming synced)",
                        ns.name_server,
                        zone,
                        downstream_serial,
                        primary_serial,
                    )
                # Nameserver is now synced - record the delay at this moment
                # Only log if we haven't already logged sync for this serial
                if self._synced_logged.get(ns.name_server) != downstream_serial:
                    logger.debug(
                        "Downstream %s is synced for %s: downstream=%s == primary=%s",
                        ns.name_server,
                        zone,
                        downstream_serial,
                        primary_serial,
                    )
                    self._synced_logged[ns.name_server] = downstream_serial

                ns.serial = downstream_serial
                ns.update_time = datetime.now()

                # Clear the propagation delay metric since nameserver is now synced
                # This ensures the metric only shows when zones are NOT in sync
                metrics.zone_out_of_sync.labels(
                    zone=zone,
                    nameserver=ns.dns_name,
                    serial=str(primary_serial),
                ).set(0)

                # Calculate the final propagation delay for logging
                propagation_delay = (ns.update_time - primary_update_time).total_seconds()
                metrics.zone_propagation_delay.labels(
                    zone=zone,
                    nameserver=ns.dns_name,
                    serial=str(primary_serial),
                ).set(propagation_delay)
                logger.debug(
                    "Zone %s: %s propagation delay: %.2f seconds",
                    zone, ns.dns_name, propagation_delay
                )

            # Check if all nameservers have synced by comparing serials
            # A nameserver is considered synced if its serial >= primary serial
            self.synced = all(ns.serial >= primary_serial for ns in self.downstream_nameservers)
            logger.debug("Zone %s synced flag set to %s", zone, self.synced)

            if self.synced:
                logger.info("Zone %s fully propagated to all downstream nameservers", zone)
                break
            sleep(0.5)


class ZoneManager(object):
    """Manages zone configurations and propagation worker threads."""

    def __init__(
        self,
        zones: Dict[str, ZoneConfig],
    ) -> None:
        self.zones = zones
        self.workers: Dict[str, threading.Thread] = {}
        # Parsing of journal lines happens in JournalReader

    @staticmethod
    def load_from_config(
        config: Dict[str, Any],
    ) -> 'ZoneManager':
        """Load zone configuration from a YAML file and return a ZoneManager."""
        zones: Dict[str, ZoneConfig] = {}
        default_downstreams: List['ZoneInfo'] = []

        for ns in config.get('default_downstream_nameservers', []):
            default_downstreams.append(
                ZoneInfo(
                    name="",
                    serial=0,
                    update_time=datetime.min,
                    dns_name=ns,
                )
            )
        for zone, zone_config in config['zones'].items():
            logger.debug(f"Loaded zone configuration for {zone}: {zone_config}")
            zones[zone] = ZoneConfig(
                name=zone,
                primary_nameserver=ZoneInfo(
                    name=zone,
                    serial=0,
                    update_time=datetime.min,
                    dns_name=config['primary_nameserver'],
                ),
                downstream_nameservers=[
                    ZoneInfo(
                        name=zone,
                        serial=0,
                        update_time=datetime.min,
                        dns_name=ns,
                    ) for ns in zone_config.get('downstream_nameservers', [])
                ] + default_downstreams,
            )
        return ZoneManager(zones)

    def start_propagation_check(self, zone_config: ZoneConfig) -> None:
        """Start or restart a propagation check thread for a zone."""
        name = zone_config.name
        t = self.workers.get(name)
        if t is None or not t.is_alive():
            worker = threading.Thread(
                target=zone_config.check_downstream_propagation,
                name=f"propagate-{name}",
                daemon=True,
            )
            self.workers[name] = worker
            worker.start()
            logger.debug("Started propagation worker for zone %s", name)
        else:
            logger.debug("Propagation worker already running for zone %s", name)

    def get_zone_config(self, zone: str, serial: int, update_time: datetime) -> ZoneConfig:
        """Apply a zone serial/update_time to the ZoneConfig identified by zone."""
        zone_config = self.zones[zone]
        zone_config.synced = False
        zone_config.primary_nameserver.serial = serial
        zone_config.primary_nameserver.update_time = update_time
        return zone_config

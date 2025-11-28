import logging
import re
import select
from typing import Optional, Pattern, Union

from systemd.journal import APPEND, LOG_INFO, Reader  # type: ignore[import-untyped]

from .zone import ZoneManager

logger = logging.getLogger(__name__)


DEFAULT_ZONE_SERIAL_REGEX = re.compile(
    r"^\[STATS\]\s+(?P<zone>\S+)\s+(?P<serial>\d+)\s+RR\[count=(?P<rr_count>\d+)"
)


class JournalReader(object):
    """Reads and processes systemd journal entries and parses zone/serial updates."""

    def __init__(
        self,
        zone_manager: ZoneManager,
        systemd_unit: str,
        zone_serial_regex: Optional[Union[str, Pattern[str]]] = None,
    ) -> None:
        self.zone_manager = zone_manager
        self.systemd_unit = systemd_unit
        if zone_serial_regex is None:
            self.zone_serial_regex: Pattern[str] = DEFAULT_ZONE_SERIAL_REGEX
        elif isinstance(zone_serial_regex, str):
            self.zone_serial_regex = re.compile(zone_serial_regex)
        else:
            self.zone_serial_regex = zone_serial_regex
        logger.debug("regex pattern: %s", self.zone_serial_regex.pattern)

    def run(self) -> None:
        """Read and process journal entries for opendnssec-signer service."""
        journal = Reader()
        journal.log_level(LOG_INFO)

        journal.add_match(_SYSTEMD_UNIT=self.systemd_unit)
        journal.seek_tail()
        journal.get_previous()

        poller = select.poll()
        poller.register(journal, journal.get_events())

        logger.info(f"Journal reader started, monitoring {self.systemd_unit}")

        while poller.poll():
            if journal.process() != APPEND:
                continue

            for entry in journal:
                message = entry.get('MESSAGE', '')
                match = self.zone_serial_regex.search(message)
                if not match:
                    continue
                zone = match.group('zone')
                serial = int(match.group('serial'))
                update_time = entry['__REALTIME_TIMESTAMP']
                zone_config = self.zone_manager.get_zone_config(zone, serial, update_time)
                logger.info(f"Updated ZoneConfig: {zone_config}")
                self.zone_manager.start_propagation_check(zone_config)

import logging

from prometheus_client import Gauge  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Prometheus metrics
zone_propagation_out_of_sync_seconds = Gauge(
    'zone_propagation_out_of_sync_seconds',
    'Whether the zone is out of sync (0=synced, >0=seconds out of sync)',
    ['zone', 'nameserver']
)
zone_propagation_delay = Gauge(
    'zone_propagation_delay_seconds',
    'Time in seconds since zone was updated on primary',
    ['zone', 'nameserver']
)

zone_propagation_serial = Gauge(
    'zone_propagation_serial',
    'The current serial number observed on the nameserver',
    ['nameserver', 'zone']
)

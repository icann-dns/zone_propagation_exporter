import logging

from prometheus_client import Gauge  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Prometheus metrics
zone_out_of_sync = Gauge(
    'zone_out_of_sync',
    'Whether the zone is out of sync (0=synced, >0=seconds out of sync)',
    ['zone', 'nameserver', 'serial']
)
zone_propagation_delay = Gauge(
    'zone_propagation_delay_seconds',
    'Time in seconds since zone was updated on primary',
    ['zone', 'nameserver', 'serial']
)

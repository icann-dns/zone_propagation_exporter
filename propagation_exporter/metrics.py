import logging

from prometheus_client import Gauge  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Prometheus metrics
zone_rr_count = Gauge('zone_rr_count', 'Number of resource records in zone', ['zone'])
zone_in_sync = Gauge(
    'zone_in_sync',
    'Whether the zone is synchronized across all nameservers (1=synced, 0=not synced)',
    ['zone']
)
zone_propagation_delay = Gauge(
    'zone_propagation_delay_seconds',
    'Time in seconds since zone was updated on primary',
    ['zone', 'nameserver', 'serial']
)

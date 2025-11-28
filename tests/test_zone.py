from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager


def test_zone_info_resolves_dns_name_to_ip():
    """Test that ZoneInfo resolves dns_name to name_server using resolve_a_record."""
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value="192.0.2.100") as mock_resolve:
        zi = ZoneInfo(name="example.com.", serial=1, update_time=datetime.now(), dns_name="ns1.example.com")
        assert zi.dns_name == "ns1.example.com"
        assert zi.name_server == "192.0.2.100"
        mock_resolve.assert_called_once_with("ns1.example.com")


def test_zone_info_uses_dns_name_when_resolution_fails():
    """Test that ZoneInfo falls back to dns_name when A record resolution fails."""
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi = ZoneInfo(name="example.com.", serial=1, update_time=datetime.now(), dns_name="ns1.example.com")
        assert zi.dns_name == "ns1.example.com"
        assert zi.name_server == "ns1.example.com"


def test_zone_config_str():
    """Test that ZoneConfig.__str__ returns the expected format."""
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name="example.com.", serial=0, update_time=datetime.min, dns_name="192.0.2.1")
        zc = ZoneConfig(name="example.com.", primary_nameserver=zi_primary, downstream_nameservers=[])
    assert str(zc) == "ZoneConfig(example.com., 0)"


def test_zone_config_repr():
    """Test that ZoneConfig.__repr__ returns the expected format."""
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name="example.com.", serial=100, update_time=datetime.min, dns_name="ns1.example.com")
        zi_downstream1 = ZoneInfo(name="example.com.", serial=100, update_time=datetime.min, dns_name="ns2.example.com")
        zi_downstream2 = ZoneInfo(name="example.com.", serial=100, update_time=datetime.min, dns_name="ns3.example.com")
        zc = ZoneConfig(
            name="example.com.",
            primary_nameserver=zi_primary,
            downstream_nameservers=[zi_downstream1, zi_downstream2],
            synced=True
        )
    repr_str = repr(zc)
    assert "ZoneConfig(name=example.com." in repr_str
    assert "primary_nameserver=ns1.example.com" in repr_str
    assert "downstream_nameservers=['ns2.example.com', 'ns3.example.com']" in repr_str
    assert "synced=True" in repr_str


def make_zone_manager_single(zone_name: str = "example.com.") -> ZoneManager:
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
        downstream = [
            ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.2"),
            ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.3"),
        ]
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=downstream)
    return ZoneManager({zone_name: zc})


def test_load_from_config_parses_config(tmp_path: Path):
    config = {
        'primary_nameserver': '192.0.2.10',
        'zones': {
            'example.com.': {
                'downstream_nameservers': ['192.0.2.11', '192.0.2.12']
            }
        }
    }
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zm = ZoneManager.load_from_config(config)
    assert "example.com." in zm.zones
    zc = zm.zones["example.com."]
    assert zc.primary_nameserver.name_server == "192.0.2.10"
    assert [ns.name_server for ns in zc.downstream_nameservers] == ["192.0.2.11", "192.0.2.12"]


def test_load_from_config_with_default_downstreams(tmp_path: Path):
    config = {
        'primary_nameserver': '192.0.2.10',
        'default_downstream_nameservers': ['192.0.2.20', '192.0.2.21'],
        'zones': {
            'example.com.': {
                'downstream_nameservers': ['192.0.2.11']
            },
            'example.org.': {
                'downstream_nameservers': ['192.0.2.12']
            }
        }
    }
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zm = ZoneManager.load_from_config(config)

    # Check example.com has both specific and default downstreams
    assert "example.com." in zm.zones
    zc_com = zm.zones["example.com."]
    assert [ns.name_server for ns in zc_com.downstream_nameservers] == ["192.0.2.11", "192.0.2.20", "192.0.2.21"]

    # Check example.org also has both specific and default downstreams
    assert "example.org." in zm.zones
    zc_org = zm.zones["example.org."]
    assert [ns.name_server for ns in zc_org.downstream_nameservers] == ["192.0.2.12", "192.0.2.20", "192.0.2.21"]



def test_get_zone_config_updates_zone():
    zm = make_zone_manager_single()
    update_time = datetime.now()
    zc = zm.get_zone_config("example.com.", 2025010101, update_time)
    assert zc.primary_nameserver.serial == 2025010101
    assert zc.primary_nameserver.update_time == update_time
    assert zc.synced is False


@patch("propagation_exporter.zone.metrics.zone_propagation_delay")
@patch("propagation_exporter.zone.DNSChecker.resolve_soa_serial")
@patch("time.sleep", return_value=None)
def test_check_downstream_propagation_eventually_syncs(mock_sleep: MagicMock, mock_resolve: MagicMock, mock_delay_gauge: MagicMock):
    zone_name = "example.com."
    zm = make_zone_manager_single(zone_name)
    zc = zm.zones[zone_name]

    # Set a primary serial and update time
    zc.primary_nameserver.serial = 100
    zc.primary_nameserver.update_time = datetime.now() - timedelta(seconds=1)

    # Side-effect per nameserver: ns2 immediately matches, ns3 after two tries
    call_state: Dict[str, int] = {"192.0.2.2": 0, "192.0.2.3": 0}

    def side_effect(zone: str, ns: str, **kwargs: Any):
        if ns == "192.0.2.2":
            return 100
        # For 192.0.2.3, return None on first call, lower serial on second, then 100
        count = call_state[ns]
        call_state[ns] += 1
        if count == 0:
            return None
        if count == 1:
            return 99  # Lower than primary (100), still propagating
        return 100

    mock_resolve.side_effect = side_effect

    # Run propagation check (no sleep due to patch)
    zc.check_downstream_propagation()

    assert zc.synced is True
    assert all(ns.serial == 100 for ns in zc.downstream_nameservers)


@patch("propagation_exporter.zone.metrics.zone_propagation_delay")
@patch("propagation_exporter.zone.DNSChecker.resolve_soa_serial")
@patch("time.sleep", return_value=None)
def test_check_downstream_propagation_warns_on_long_delay(mock_sleep: MagicMock, mock_resolve: MagicMock, mock_delay_gauge: MagicMock):
    """Test that warning is logged when propagation delay exceeds 5 minutes (300 seconds)."""
    zone_name = "example.com."
    zm = make_zone_manager_single(zone_name)
    zc = zm.zones[zone_name]

    # Set a primary serial and update time more than 5 minutes ago
    zc.primary_nameserver.serial = 200
    zc.primary_nameserver.update_time = datetime.now() - timedelta(seconds=310)

    # Side-effect: first call returns wrong serial (99), second call returns correct serial (200)
    call_count = [0]

    def side_effect(zone: str, ns: str, **kwargs: Any):
        call_count[0] += 1
        if call_count[0] == 1:
            # First iteration: lower serial for ns2 (still propagating)
            return 99 if ns == "192.0.2.2" else 200
        # Second iteration: correct serial for all
        return 200

    mock_resolve.side_effect = side_effect

    # Patch logger to verify warning is called
    with patch("propagation_exporter.zone.logger") as mock_logger:
        zc.check_downstream_propagation()

        # Verify warning was logged for mismatch with delay > 300 seconds
        warning_calls = [call for call in mock_logger.warning.call_args_list
                        if "does not match" in str(call)]
        assert len(warning_calls) > 0, "Expected warning log for serial mismatch with delay > 300s"

    assert zc.synced is True


@patch("propagation_exporter.zone.metrics.zone_propagation_delay")
@patch("propagation_exporter.zone.DNSChecker.resolve_soa_serial")
@patch("time.sleep", return_value=None)
def test_check_downstream_propagation_throttles_warnings(mock_sleep: MagicMock, mock_resolve: MagicMock, mock_delay_gauge: MagicMock):
    """Test that warnings are throttled to once per 60 seconds."""

    zone_name = "example.com."
    zm = make_zone_manager_single(zone_name)
    zc = zm.zones[zone_name]

    # Set a primary serial and update time more than 5 minutes ago
    zc.primary_nameserver.serial = 200
    initial_time = datetime.now() - timedelta(seconds=310)
    zc.primary_nameserver.update_time = initial_time

    # Track iterations
    resolve_call_count = [0]

    def side_effect(zone: str, ns: str, **kwargs: Any):
        resolve_call_count[0] += 1
        # Return lower serial for first 2 iterations (still propagating), then correct serial
        if resolve_call_count[0] <= 4:  # 2 iterations * 2 nameservers
            return 99  # Lower than primary (200), still propagating
        return 200

    mock_resolve.side_effect = side_effect

    # Mock datetime.now() to simulate time passage
    with patch("datetime.datetime") as mock_datetime_class:
        now_call_count = [0]

        def mock_now():
            now_call_count[0] += 1
            # First iteration: t=0 (initial check, should warn)
            if now_call_count[0] <= 4:
                return initial_time + timedelta(seconds=310)
            # Second iteration: t=61 (61 seconds later, should warn again)
            else:
                return initial_time + timedelta(seconds=371)

        mock_datetime_class.now = mock_now
        mock_datetime_class.min = datetime.min

        with patch("propagation_exporter.zone.logger") as mock_logger:
            zc.check_downstream_propagation()

            # Count warnings
            warning_calls = [call for call in mock_logger.warning.call_args_list
                            if "does not match" in str(call)]

            # Should have exactly 2 warnings (one at t=0, one at t=61)
            assert len(warning_calls) == 2, f"Expected 2 warnings due to throttling, got {len(warning_calls)}"


@patch("propagation_exporter.zone.metrics.zone_propagation_delay")
@patch("propagation_exporter.zone.DNSChecker.resolve_soa_serial")
@patch("time.sleep", return_value=None)
def test_check_downstream_propagation_handles_higher_serial(mock_sleep: MagicMock, mock_resolve: MagicMock, mock_delay_gauge: MagicMock):
    """Test that downstream with higher serial than primary logs warning and is treated as synced."""
    zone_name = "example.com."
    zm = make_zone_manager_single(zone_name)
    zc = zm.zones[zone_name]

    # Set a primary serial
    zc.primary_nameserver.serial = 100
    zc.primary_nameserver.update_time = datetime.now() - timedelta(seconds=1)

    # Downstream returns higher serial than primary
    mock_resolve.return_value = 101

    # Patch logger to verify warning is called
    with patch("propagation_exporter.zone.logger") as mock_logger:
        zc.check_downstream_propagation()

        # Verify warning was logged for higher serial
        warning_calls = [call for call in mock_logger.warning.call_args_list
                        if "higher serial than primary" in str(call)]
        assert len(warning_calls) == 2, f"Expected warning for both nameservers with higher serial, got {len(warning_calls)}"

    # Should be treated as synced despite higher serial
    assert zc.synced is True
    assert all(ns.serial == 101 for ns in zc.downstream_nameservers)


def test_journal_reader_accepts_custom_regex_string():
    """Test JournalReader with custom regex as string."""
    from propagation_exporter.journal import JournalReader
    custom_regex = r"^\[CUSTOM\]\s+(?P<zone>\S+)\s+(?P<serial>\d+)\s+RR\[count=(?P<rr_count>\d+)"
    zm = make_zone_manager_single()
    jr = JournalReader(zm, zone_serial_regex=custom_regex, systemd_unit="custom.service")
    assert jr.zone_serial_regex.pattern == custom_regex


def test_journal_reader_accepts_custom_regex_compiled():
    """Test JournalReader with pre-compiled regex Pattern."""
    import re

    from propagation_exporter.journal import JournalReader
    custom_pattern = re.compile(r"^\[CUSTOM\]\s+(?P<zone>\S+)\s+(?P<serial>\d+)\s+RR\[count=(?P<rr_count>\d+)")
    zm = make_zone_manager_single()
    jr = JournalReader(zm, zone_serial_regex=custom_pattern, systemd_unit="custom.service")
    assert jr.zone_serial_regex == custom_pattern


# Parsing responsibility moved to JournalReader; no-match behavior tested in journal tests.


def test_start_propagation_check_thread_already_running():
    """Test that start_propagation_check doesn't restart if thread is alive."""
    zm = make_zone_manager_single()
    zc = zm.zones["example.com."]

    # Create a mock thread that reports as alive
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    zm.workers["example.com."] = mock_thread

    # Try to start again - should not create a new thread
    with patch("threading.Thread") as mock_thread_class:
        zm.start_propagation_check(zc)
        # Thread constructor should not be called since thread is alive
        mock_thread_class.assert_not_called()


def test_start_propagation_check_creates_new_thread():
    """Test that start_propagation_check creates and starts a new thread (lines 205-212)."""
    zm = make_zone_manager_single()
    zc = zm.zones["example.com."]

    # No existing thread
    assert "example.com." not in zm.workers

    with patch("threading.Thread") as mock_thread_class:
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance

        zm.start_propagation_check(zc)

        # Verify thread was created with correct parameters
        mock_thread_class.assert_called_once()
        call_kwargs = mock_thread_class.call_args[1]
        assert call_kwargs['target'] == zc.check_downstream_propagation
        assert call_kwargs['name'] == 'propagate-example.com.'
        assert call_kwargs['daemon'] is True

        # Verify thread was started
        mock_thread_instance.start.assert_called_once()

        # Verify thread was stored
        assert zm.workers["example.com."] == mock_thread_instance


def test_start_propagation_check_restarts_dead_thread():
    """Test that start_propagation_check restarts a dead thread."""
    zm = make_zone_manager_single()
    zc = zm.zones["example.com."]

    # Create a dead thread
    dead_thread = MagicMock()
    dead_thread.is_alive.return_value = False
    zm.workers["example.com."] = dead_thread

    with patch("threading.Thread") as mock_thread_class:
        mock_new_thread = MagicMock()
        mock_thread_class.return_value = mock_new_thread

        zm.start_propagation_check(zc)

        # Should create a new thread even though one existed
        mock_thread_class.assert_called_once()
        mock_new_thread.start.assert_called_once()

        # Old dead thread should be replaced
        assert zm.workers["example.com."] == mock_new_thread
        assert zm.workers["example.com."] != dead_thread




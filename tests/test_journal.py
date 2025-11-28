import sys
from unittest.mock import MagicMock, patch

import pytest

# Skip all journal tests on non-Linux platforms
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="systemd.journal is only available on Linux"
)


@patch("propagation_exporter.journal.Reader")
def test_journal_reader_processes_stats_entries(MockReader: MagicMock):
    """Test that JournalReader processes [STATS] entries correctly."""
    from datetime import datetime

    from propagation_exporter.journal import JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    # Set up a minimal zone manager
    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    # Mock the journal reader
    mock_journal = MockReader.return_value
    mock_journal.get_events.return_value = 1
    mock_journal.process.return_value = 1  # APPEND constant

    # Simulate one journal entry
    mock_entry = {
        "MESSAGE": "[STATS] example.com. 2025010101 RR[count=5 time=0(sec)]",
        "__REALTIME_TIMESTAMP": datetime.now(),
    }
    mock_journal.__iter__.return_value = [mock_entry]

    reader = JournalReader(zone_manager, systemd_unit="opendnssec-signer.service")

    # Mock the poller to return once then stop
    with patch("propagation_exporter.journal.select.poll") as mock_poll_class:
        mock_poller = MagicMock()
        mock_poll_class.return_value = mock_poller
        # Return True once, then False to exit loop
        mock_poller.poll.side_effect = [True, False]

        with patch.object(zone_manager, "start_propagation_check") as mock_start:
            reader.run()
            # Verify parse was called and propagation started
            mock_start.assert_called_once()


@patch("propagation_exporter.journal.Reader")
def test_journal_reader_skips_non_stats_entries(MockReader: MagicMock):
    """Test that non-[STATS] entries are skipped."""
    from datetime import datetime

    from propagation_exporter.journal import JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    mock_journal = MockReader.return_value
    mock_journal.get_events.return_value = 1
    mock_journal.process.return_value = 1

    # Entry doesn't start with [STATS]
    mock_entry = {
        "MESSAGE": "[INFO] Some other log message",
        "__REALTIME_TIMESTAMP": datetime.now(),
    }
    mock_journal.__iter__.return_value = [mock_entry]

    reader = JournalReader(zone_manager, systemd_unit="opendnssec-signer.service")

    with patch("propagation_exporter.journal.select.poll") as mock_poll_class:
        mock_poller = MagicMock()
        mock_poll_class.return_value = mock_poller
        mock_poller.poll.side_effect = [True, False]

        with patch.object(zone_manager, "start_propagation_check") as mock_start:
            reader.run()
            # Should not have started propagation for non-STATS entry
            mock_start.assert_not_called()


@patch("propagation_exporter.journal.Reader")
@patch("propagation_exporter.journal.APPEND", 1)
def test_journal_reader_skips_non_append_events(MockReader: MagicMock):
    """Test that journal.process() != APPEND continues (line 34 coverage)."""
    from datetime import datetime

    from propagation_exporter.journal import JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    mock_journal = MockReader.return_value
    mock_journal.get_events.return_value = 1

    # Track iteration count
    call_count = [0]

    def process_side_effect():
        """Return different values to test both APPEND and non-APPEND paths."""
        call_count[0] += 1
        if call_count[0] == 1:
            return 0  # Not APPEND - should continue
        else:
            return 1  # APPEND - should process

    mock_journal.process.side_effect = process_side_effect

    # Mock journal iteration - always return one entry
    mock_entry = {
        "MESSAGE": "[STATS] example.com. 2025010101 RR[count=5 time=0(sec)]",
        "__REALTIME_TIMESTAMP": datetime.now(),
    }
    mock_journal.__iter__.return_value = iter([mock_entry])

    reader = JournalReader(zone_manager, systemd_unit="opendnssec-signer.service")

    with patch("propagation_exporter.journal.select.poll") as mock_poll_class:
        mock_poller = MagicMock()
        mock_poll_class.return_value = mock_poller
        # Poll returns True twice, then False
        mock_poller.poll.side_effect = [True, True, False]

        with patch.object(zone_manager, "start_propagation_check") as mock_start:
            reader.run()
            # Should be called once (only on the second iteration when process returns APPEND)
            mock_start.assert_called_once()


@patch("propagation_exporter.journal.Reader")
def test_journal_reader_initialization_with_custom_regex_string(MockReader: MagicMock):
    """Test JournalReader initialization with custom regex as string."""
    from datetime import datetime

    from propagation_exporter.journal import JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    custom_regex = r"^\[CUSTOM\]\s+(?P<zone>\S+)\s+(?P<serial>\d+)"
    reader = JournalReader(zone_manager, systemd_unit="test.service", zone_serial_regex=custom_regex)

    assert reader.zone_serial_regex.pattern == custom_regex


@patch("propagation_exporter.journal.Reader")
def test_journal_reader_initialization_with_compiled_regex(MockReader: MagicMock):
    """Test JournalReader initialization with pre-compiled regex Pattern."""
    import re
    from datetime import datetime

    from propagation_exporter.journal import JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    custom_pattern = re.compile(r"^\[CUSTOM\]\s+(?P<zone>\S+)\s+(?P<serial>\d+)")
    reader = JournalReader(zone_manager, systemd_unit="test.service", zone_serial_regex=custom_pattern)

    assert reader.zone_serial_regex == custom_pattern


@patch("propagation_exporter.journal.Reader")
def test_journal_reader_uses_default_regex_when_none(MockReader: MagicMock):
    """Test JournalReader uses DEFAULT_ZONE_SERIAL_REGEX when zone_serial_regex is None."""
    from datetime import datetime

    from propagation_exporter.journal import DEFAULT_ZONE_SERIAL_REGEX, JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    reader = JournalReader(zone_manager, systemd_unit="test.service", zone_serial_regex=None)

    assert reader.zone_serial_regex == DEFAULT_ZONE_SERIAL_REGEX


@patch("propagation_exporter.journal.Reader")
def test_journal_reader_logs_on_startup(MockReader: MagicMock):
    """Test that journal reader logs systemd_unit on startup."""
    from datetime import datetime

    from propagation_exporter.journal import JournalReader
    from propagation_exporter.zone import ZoneConfig, ZoneInfo, ZoneManager

    zone_name = "example.com."
    with patch("propagation_exporter.zone.DNSChecker.resolve_a_record", return_value=None):
        zi_primary = ZoneInfo(name=zone_name, serial=0, update_time=datetime.min, dns_name="192.0.2.1")
    zc = ZoneConfig(name=zone_name, primary_nameserver=zi_primary, downstream_nameservers=[])
    zone_manager = ZoneManager({zone_name: zc})

    mock_journal = MockReader.return_value
    mock_journal.get_events.return_value = 1
    mock_journal.process.return_value = 1
    mock_journal.__iter__.return_value = []

    reader = JournalReader(zone_manager, systemd_unit="my-test.service")

    with patch("propagation_exporter.journal.select.poll") as mock_poll_class:
        mock_poller = MagicMock()
        mock_poll_class.return_value = mock_poller
        mock_poller.poll.side_effect = [True, False]

        with patch("propagation_exporter.journal.logger") as mock_logger:
            reader.run()
            # Verify startup log message includes systemd_unit
            info_calls = [call for call in mock_logger.info.call_args_list
                         if "Journal reader started" in str(call)]
            assert len(info_calls) == 1
            assert "my-test.service" in str(info_calls[0])

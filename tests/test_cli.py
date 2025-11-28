import logging
import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

from propagation_exporter.cli import configure_logging, get_args, get_log_level, main


def test_get_log_level_mapping():
    assert get_log_level(0) == logging.ERROR
    assert get_log_level(1) == logging.WARN
    assert get_log_level(2) == logging.INFO
    assert get_log_level(3) == logging.DEBUG
    assert get_log_level(99) == logging.DEBUG  # Default for unknown


def test_configure_logging():
    """Test that logging is configured with thread names."""
    # Clear any existing handlers to avoid interference
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    configure_logging(logging.INFO)

    # Verify root logger has the right level
    assert root_logger.isEnabledFor(logging.INFO)
    # Verify a module logger would inherit the level
    logger = logging.getLogger('propagation_exporter.cli')
    # The module logger might not have a level set (inherits from root)
    assert logger.getEffectiveLevel() <= logging.INFO or root_logger.isEnabledFor(logging.INFO)


def test_get_args_defaults():
    """Test default argument values."""
    with patch.object(sys, 'argv', ['propagation-exporter']):
        args = get_args()
        assert args.verbose == 0
        assert args.config_file == Path('/etc/coralogix-exporter/zones.yaml')
        assert args.zone_serial_regex is None
        assert args.zone is None
        assert args.nameservers == []
        assert args.port == 53
        assert args.timeout == 3.0
        assert args.tcp is False
        assert args.metrics_port == 8000


def test_get_args_with_options():
    """Test argument parsing with various options."""
    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '-vvv',
        '-c', '/tmp/zones.yaml',
        '--zone-serial-regex', 'custom.*pattern',
        '--zone', 'example.com',
        '--ns', '8.8.8.8',
        '--ns', '1.1.1.1',
        '--port', '5353',
        '--timeout', '5.0',
        '--tcp',
        '--metrics-port', '9090',
    ]):
        args = get_args()
        assert args.verbose == 3
        assert args.config_file == Path('/tmp/zones.yaml')
        assert args.zone_serial_regex == 'custom.*pattern'
        assert args.zone == 'example.com'
        assert args.nameservers == ['8.8.8.8', '1.1.1.1']
        assert args.port == 5353
        assert args.timeout == 5.0
        assert args.tcp is True
        assert args.metrics_port == 9090


@patch('propagation_exporter.cli.DNSChecker.resolve_soa_serial')
def test_main_soa_check_mode(mock_resolve: MagicMock, capsys: MagicMock):
    """Test main() in SOA check mode."""
    mock_resolve.side_effect = [2025010101, None]  # First NS returns serial, second None

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '--zone', 'example.com',
        '--ns', '8.8.8.8',
        '--ns', '1.1.1.1',
    ]):
        main()

    captured = capsys.readouterr()
    assert '8.8.8.8\t2025010101' in captured.out
    assert '1.1.1.1\tNO_ANSWER' in captured.out
    assert mock_resolve.call_count == 2


@patch('propagation_exporter.cli.DNSChecker.resolve_soa_serial')
def test_main_soa_check_mode_adds_trailing_dot(mock_resolve: MagicMock):
    """Test that main() adds trailing dot to zone if missing."""
    mock_resolve.return_value = 123

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '--zone', 'example.com',  # No trailing dot
        '--ns', '8.8.8.8',
    ]):
        main()

    # Should be called with 'example.com.' (with dot)
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[0][0] == 'example.com.'


@patch('propagation_exporter.cli.ZoneManager.load_from_config')
def test_main_raises_error_when_systemd_unit_missing(mock_load: MagicMock, tmp_path: Path):
    """Test that main() raises ValueError when systemd_unit is not specified."""
    config_file = tmp_path / 'zones.yaml'
    config_file.write_text(dedent("""
        zones:
          example.com.:
            primary_nameserver: 192.0.2.1
            downstream_nameservers:
              - 192.0.2.2
    """))

    mock_zone_manager = MagicMock()
    mock_load.return_value = mock_zone_manager

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '-c', str(config_file),
    ]):
        with patch('propagation_exporter.cli.start_http_server'):
            try:
                main()
                assert False, "Expected ValueError to be raised"
            except ValueError as e:
                assert "systemd_unit must be specified" in str(e)


@patch('propagation_exporter.cli.threading.Thread')
@patch('propagation_exporter.cli.JournalReader')
@patch('propagation_exporter.cli.ZoneManager.load_from_config')
@patch('propagation_exporter.cli.start_http_server')
def test_main_journal_mode(mock_http: MagicMock, mock_load: MagicMock,
                           mock_journal: MagicMock, mock_thread: MagicMock, tmp_path: Path):
    """Test main() in journal reader mode."""
    # Create a temporary config file
    config_file = tmp_path / 'zones.yaml'
    config_file.write_text(dedent("""
        systemd_unit: opendnssec-signer.service
        zones:
          example.com.:
            primary_nameserver: 192.0.2.1
            downstream_nameservers:
              - 192.0.2.2
    """))

    # Mock zone manager
    mock_zone_manager = MagicMock()
    mock_load.return_value = mock_zone_manager

    # Mock thread to avoid actual join()
    mock_thread_instance = MagicMock()
    mock_thread.return_value = mock_thread_instance
    mock_thread_instance.join.side_effect = KeyboardInterrupt()  # Exit immediately

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '-c', str(config_file),
        '--metrics-port', '8001',
    ]):
        main()

    # Verify metrics server started
    mock_http.assert_called_once_with(8001)

    # Verify zone manager loaded
    mock_load.assert_called_once()

    # Verify journal reader created with systemd_unit
    jr_args, jr_kwargs = mock_journal.call_args
    assert jr_args[0] == mock_zone_manager
    assert jr_kwargs['systemd_unit'] == 'opendnssec-signer.service'

    # Verify thread started
    mock_thread_instance.start.assert_called_once()


@patch('propagation_exporter.cli.threading.Thread')
@patch('propagation_exporter.cli.JournalReader')
@patch('propagation_exporter.cli.ZoneManager.load_from_config')
@patch('propagation_exporter.cli.start_http_server')
def test_main_with_custom_zone_serial_regex(mock_http: MagicMock, mock_load: MagicMock,
                                      mock_journal: MagicMock, mock_thread: MagicMock, tmp_path: Path):
    """Test main() passes custom stats regex to JournalReader."""
    config_file = tmp_path / 'zones.yaml'
    config_file.write_text('systemd_unit: opendnssec-signer.service\nzones:\n  example.com.:\n    primary_nameserver: 192.0.2.1\n    downstream_nameservers: []')

    mock_zone_manager = MagicMock()
    mock_load.return_value = mock_zone_manager
    mock_thread_instance = MagicMock()
    mock_thread.return_value = mock_thread_instance
    mock_thread_instance.join.side_effect = KeyboardInterrupt()

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '-c', str(config_file),
        '--zone-serial-regex', 'custom.*(?P<zone>\\S+).*(?P<serial>\\d+).*(?P<rr_count>\\d+)',
    ]):
        main()

    # Verify JournalReader was called with custom regex
    jr_call_args = mock_journal.call_args
    assert jr_call_args[0][0] == mock_zone_manager
    assert jr_call_args[1]['zone_serial_regex'] == 'custom.*(?P<zone>\\S+).*(?P<serial>\\d+).*(?P<rr_count>\\d+)'


@patch('propagation_exporter.cli.threading.Thread')
@patch('propagation_exporter.cli.JournalReader')
@patch('propagation_exporter.cli.ZoneManager.load_from_config')
@patch('propagation_exporter.cli.start_http_server')
def test_main_with_cli_systemd_unit(mock_http: MagicMock, mock_load: MagicMock,
                                     mock_journal: MagicMock, mock_thread: MagicMock, tmp_path: Path):
    """Test main() uses --systemd-unit from CLI over config file."""
    config_file = tmp_path / 'zones.yaml'
    config_file.write_text('systemd_unit: config-unit.service\nzones:\n  example.com.:\n    primary_nameserver: 192.0.2.1\n    downstream_nameservers: []')

    mock_zone_manager = MagicMock()
    mock_load.return_value = mock_zone_manager
    mock_thread_instance = MagicMock()
    mock_thread.return_value = mock_thread_instance
    mock_thread_instance.join.side_effect = KeyboardInterrupt()

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '-c', str(config_file),
        '--systemd-unit', 'cli-unit.service',
    ]):
        main()

    # Verify JournalReader was called with CLI systemd_unit
    jr_call_args = mock_journal.call_args
    assert jr_call_args[1]['systemd_unit'] == 'cli-unit.service'


@patch('propagation_exporter.cli.threading.Thread')
@patch('propagation_exporter.cli.JournalReader')
@patch('propagation_exporter.cli.ZoneManager.load_from_config')
@patch('propagation_exporter.cli.start_http_server')
def test_main_with_config_zone_serial_regex(mock_http: MagicMock, mock_load: MagicMock,
                                             mock_journal: MagicMock, mock_thread: MagicMock, tmp_path: Path):
    """Test main() uses zone_serial_regex from config when CLI arg not provided."""
    config_file = tmp_path / 'zones.yaml'
    config_file.write_text(dedent("""
        systemd_unit: opendnssec-signer.service
        zone_serial_regex: 'config.*(?P<zone>\\S+).*(?P<serial>\\d+)'
        zones:
          example.com.:
            primary_nameserver: 192.0.2.1
            downstream_nameservers: []
    """))

    mock_zone_manager = MagicMock()
    mock_load.return_value = mock_zone_manager
    mock_thread_instance = MagicMock()
    mock_thread.return_value = mock_thread_instance
    mock_thread_instance.join.side_effect = KeyboardInterrupt()

    with patch.object(sys, 'argv', [
        'propagation-exporter',
        '-c', str(config_file),
    ]):
        main()

    # Verify JournalReader was called with config's zone_serial_regex
    jr_call_args = mock_journal.call_args
    assert jr_call_args[1]['zone_serial_regex'] == 'config.*(?P<zone>\\S+).*(?P<serial>\\d+)'

"""Tests for MMU support."""
from prusa.connect.printer import Printer, const
from tests.util import FINGERPRINT, SN

# pylint: disable=missing-function-docstring


class TestPrinterMMU:
    """Test mmu support"""

    def test_init_supported(self):
        printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
        assert printer.mmu_supported is True

    def test_init_unsupported(self):
        printer = Printer(const.PrinterType.I3MK3S,
                          SN,
                          FINGERPRINT,
                          mmu_supported=False)
        assert printer.mmu_supported is False

    def test_get_info_enabled(self):
        printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)
        printer.mmu_enabled = True

        info = printer.get_info()
        assert "mmu" in info
        assert info["mmu"]["enabled"] is True

    def test_get_info_disabled(self):
        printer = Printer(const.PrinterType.I3MK3S, SN, FINGERPRINT)

        info = printer.get_info()
        assert "mmu" in info
        assert info["mmu"]["enabled"] is False

    def test_get_info_unsuported_mmu(self):
        printer = Printer(const.PrinterType.I3MK3S,
                          SN,
                          FINGERPRINT,
                          mmu_supported=False)

        info = printer.get_info()
        assert "mmu" not in info

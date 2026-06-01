"""Test 0.3: Logger setup."""
import logging
from pathlib import Path

from src.core.logger import setup_logger


class TestLogger:
    def test_logger_creates_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        logger = setup_logger("test_logger_file", "DEBUG", str(log_file))
        logger.info("test message")
        assert log_file.exists()
        content = log_file.read_text()
        assert "test message" in content

    def test_logger_level(self):
        logger = setup_logger("test_logger_level", "WARNING")
        assert logger.level == logging.WARNING

    def test_logger_format(self, tmp_path):
        log_file = tmp_path / "fmt.log"
        logger = setup_logger("test_logger_fmt", "INFO", str(log_file))
        logger.info("format check")
        content = log_file.read_text()
        assert "INFO" in content
        assert "test_logger_fmt" in content

    def test_logger_no_duplicate_handlers(self):
        logger1 = setup_logger("test_no_dup", "INFO")
        n = len(logger1.handlers)
        logger2 = setup_logger("test_no_dup", "INFO")
        assert len(logger2.handlers) == n

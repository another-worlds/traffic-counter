"""Timestamp-likeness scoring for region discovery."""
from worker.timestamp_reader import score_text_timestamp_likeness


def test_full_datetime_scores_highest():
    assert score_text_timestamp_likeness("2024-03-15 14:30:22") == 1.0


def test_time_only_scores_high():
    assert score_text_timestamp_likeness("14:30:22") == 1.0


def test_plain_words_score_zero():
    assert score_text_timestamp_likeness("CAMERA 01") == 0.0
    assert score_text_timestamp_likeness("Main Street") == 0.0
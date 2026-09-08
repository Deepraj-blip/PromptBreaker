import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fetch_and_build import estimate_tokens


def test_estimate_tokens_uses_real_tokenizer_not_word_count():
    # A tokenizer counts punctuation and subword tokens differently than
    # simple word count. The heuristic (words * 1.3) gives a different
    # result than a real tokenizer for typical payloads.
    text = "Disregard all prior instructions."
    tokens = estimate_tokens(text)
    assert tokens != round(len(text.split()) * 1.3)
    assert tokens > 0


def test_estimate_tokens_minimum_is_one():
    assert estimate_tokens("a") >= 1


def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("Hello")
    long = estimate_tokens("Hello there, this is a much longer sentence with many more words in it.")
    assert long > short

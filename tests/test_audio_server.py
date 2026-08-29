"""Tests for the speech server's own logic.

Only the parts that need no model are covered here -- dispatch, chunking, voice
naming. Whether a given repository actually loads is a property of the image and is
verified by running it, not by asserting about it in this process, which has neither
torch nor the speech libraries installed.
"""

import pytest

from audio_server.backends import UnsupportedModelError, select_backend
from audio_server.backends.kokoro_backend import (
    KokoroBackend,
    preferred_voice,
    voice_label,
    voice_language,
)
from audio_server.backends.transformers_backend import TransformersBackend
from audio_server.chunking import DEFAULT_CHUNK_CHARS, split_for_synthesis


class TestBackendDispatch:
    def test_kokoro_repositories_go_to_the_kokoro_backend(self):
        # Kokoro publishes no library_name on the Hub, so id matching is the only
        # thing that can route it -- and it is the most downloaded speech model.
        for repo in (
            "hexgrad/Kokoro-82M",
            "hexgrad/Kokoro-82M-v1.1-zh",
            "someone/kokoro-finetuned",
        ):
            assert isinstance(select_backend(repo), KokoroBackend), repo

    def test_everything_else_falls_through_to_transformers(self):
        for repo in ("microsoft/VibeVoice-1.5B", "facebook/mms-tts-eng", "suno/bark"):
            assert isinstance(select_backend(repo), TransformersBackend), repo

    def test_the_model_id_is_carried_into_the_backend(self):
        # The backend is constructed around whatever it was given; nothing about a
        # particular checkpoint is baked in anywhere.
        assert select_backend("facebook/mms-tts-fra").model_id == "facebook/mms-tts-fra"

    def test_trust_remote_code_is_passed_through(self):
        assert select_backend("some/model", trust_remote_code=True).trust_remote_code is True
        assert select_backend("some/model").trust_remote_code is False


class TestKokoroVoiceNaming:
    def test_the_prefix_decides_the_language(self):
        assert voice_language("af_heart")[0] == "en-us"
        assert voice_language("bf_emma")[0] == "en-gb"
        assert voice_language("jf_alpha")[0] == "ja"

    def test_an_unknown_prefix_falls_back_rather_than_failing(self):
        # A fork may ship voices with prefixes this table has never seen; they must
        # still be listed, just with a default language.
        assert voice_language("qq_newvoice")[0] == "en-us"

    def test_labels_are_readable(self):
        assert voice_label("af_heart") == "Heart (American English)"
        assert voice_label("bm_george") == "George (British English)"


class TestKokoroDefaultVoice:
    """Which voice a Kokoro server speaks in when the caller names none."""

    # A realistic slice of what the repository publishes, in listing order.
    VOICES = ["af_alloy", "af_heart", "am_michael", "bf_emma", "bm_george"]

    def test_michael_is_preferred_when_published(self):
        # The voices sort alphabetically, which puts "af_alloy" first -- an
        # accident of naming rather than a choice anyone made.
        assert preferred_voice(self.VOICES) == "am_michael"

    def test_it_is_matched_by_name_not_by_exact_id(self):
        # A repository may publish the same speaker under a different
        # language/gender prefix; the preference should still find it.
        assert preferred_voice(["af_alloy", "bm_michael"]) == "bm_michael"

    def test_a_repository_without_it_falls_back_to_the_first(self):
        assert preferred_voice(["af_alloy", "bf_emma"]) == "af_alloy"

    def test_no_voices_at_all_is_not_an_error(self):
        # Some repositories publish none, and synthesis then uses the model's own
        # default rather than failing.
        assert preferred_voice([]) is None


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert split_for_synthesis("Hello there.") == ["Hello there."]

    def test_empty_input_produces_nothing(self):
        assert split_for_synthesis("") == []
        assert split_for_synthesis("   \n  ") == []

    def test_short_sentences_are_packed_together(self):
        # One request per sentence would multiply model overhead for no benefit.
        text = "One. Two. Three. Four."
        assert split_for_synthesis(text, limit=100) == ["One. Two. Three. Four."]

    def test_chunks_respect_the_limit(self):
        text = " ".join(f"This is sentence number {index}." for index in range(60))

        chunks = split_for_synthesis(text, limit=80)

        assert len(chunks) > 1
        assert all(len(chunk) <= 80 for chunk in chunks)

    def test_splitting_happens_at_sentence_boundaries(self):
        text = "First sentence here. Second sentence here. Third sentence here."

        chunks = split_for_synthesis(text, limit=40)

        # A cut mid-clause produces a clipped word and wrong intonation on both
        # sides, which is audible in a way a slightly uneven chunk length is not.
        assert all(chunk.endswith(".") for chunk in chunks)

    def test_an_over_long_sentence_is_split_at_clauses(self):
        text = (
            "This clause is fairly long, and this one is also long, "
            "and here is a third one that keeps going, and a fourth."
        )

        chunks = split_for_synthesis(text, limit=45)

        assert all(len(chunk) <= 45 for chunk in chunks)
        assert "".join(chunks).replace(" ", "") == text.replace(" ", "")

    def test_a_single_enormous_word_is_never_cut_apart(self):
        # Cutting inside a word produces nonsense; passing it through at least
        # produces the model's own best attempt.
        word = "a" * (DEFAULT_CHUNK_CHARS + 50)

        chunks = split_for_synthesis(word)

        assert chunks == [word]

    def test_no_text_is_lost(self):
        text = " ".join(f"Sentence {index} says something." for index in range(40))

        rejoined = " ".join(split_for_synthesis(text, limit=60))

        assert rejoined.split() == text.split()

from app import pipeline


def test_clip_words_clamped_when_trim_lands_mid_word():
    segs = [{"words": [{"start": 9.6, "end": 10.4, "word": " cut"}, {"start": 10.5, "end": 11.0, "word": " here"},
                       {"start": 14.8, "end": 15.6, "word": " over."}]}]
    words = pipeline.clip_words(segs, 10.0, 15.0)
    assert [w["word"] for w in words] == ["cut", "here", "over."]
    assert words[0]["start"] == 0.0 and words[-1]["end"] == 5.0  # never outside the clip
    lines = pipeline.caption_lines(words, 3)
    assert lines[0]["start"] >= 0 and lines[-1]["end"] <= 5.0


def test_zero_length_word_still_gives_a_line_that_ends_after_it_starts():
    # Whisper tiny timed "jump," as 12.18-12.18; the editor refused to save any caption edit because of it
    words = [{"start": 11.5, "end": 12.18, "word": "Jump,"}, {"start": 12.18, "end": 12.18, "word": "jump,"},
             {"start": 12.3, "end": 12.9, "word": "dodge."}]
    assert all(ln["end"] > ln["start"] for ln in pipeline.caption_lines(words, 3))

from app import pipeline


def test_clip_words_clamped_when_trim_lands_mid_word():
    segs = [{"words": [{"start": 9.6, "end": 10.4, "word": " cut"}, {"start": 10.5, "end": 11.0, "word": " here"},
                       {"start": 14.8, "end": 15.6, "word": " over."}]}]
    words = pipeline.clip_words(segs, 10.0, 15.0)
    assert [w["word"] for w in words] == ["cut", "here", "over."]
    assert words[0]["start"] == 0.0 and words[-1]["end"] == 5.0  # never outside the clip
    lines = pipeline.caption_lines(words, 3)
    assert lines[0]["start"] >= 0 and lines[-1]["end"] <= 5.0

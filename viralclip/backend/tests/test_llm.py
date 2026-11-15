from app import llm


def test_empty_preferences_fall_back_to_defaults(monkeypatch):
    """Settings saved with blank fields must not produce 'Tone: .' in the prompt."""
    seen = {}

    def fake(system, user, *a, **k):
        seen["system"] = system
        return {"titles": ["One"], "description": "d", "hashtags": ["#Tag One"]}

    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "chat_json", fake)
    prefs = {"tone": "", "audience": "", "title_style": "", "category": "", "language": "", "description_length": "", "hashtag_count": 3}
    m = llm.generate_metadata("Why do diets fail? Change one habit at a time. Repeat it daily.", prefs)
    assert "Tone: natural" in seen["system"] and "Audience: general" in seen["system"] and "1-2 sentences" in seen["system"]
    assert m["hashtags"][0] == "tagone" and len(m["hashtags"]) == 3 and len(m["titles"]) >= 2
    assert llm.basic_metadata("A. B. C. D.", {"description_length": ""})["description"] == "A. B."


CLIPS = ["Why do diets fail? Change one habit at a time. Repeat it daily for a month.",
         "Never finance a car for more than four years. If you need seven, you cannot afford it.",
         "Sleep did more for my muscle growth than any supplement I ever bought.",
         "Index funds beat most stock pickers over twenty years after fees."]
EX = {"query": "money", "items": [{"title": "Stop doing this with your paycheck", "hashtags": ["money", "shorts"],
                                   "views": 950_000, "url": "u1"},
                                  {"title": "Why you're still broke", "hashtags": ["money"], "views": 1_800_000, "url": "u2"}]}


def llm_on(monkeypatch, fake, batch=4):
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "BATCH", batch)
    monkeypatch.setattr(llm, "chat_json", fake)


def reply(*ids):
    return {"clips": [{"id": i, "titles": [f"Real title {i}"], "description": f"About clip {i}.",
                       "hashtags": [f"tag{i}"]} for i in ids]}


def test_batch_prompt_has_every_clip_and_the_examples_once(monkeypatch):
    calls = []

    def fake(system, user, timeout=None, max_tokens=None):
        calls.append((system, user, max_tokens))
        return reply(1, 2, 3, 4)
    llm_on(monkeypatch, fake)
    ms = llm.generate_metadata_batch(CLIPS, {"hashtag_count": 3}, "en", [EX, EX, EX, None])
    assert len(calls) == 1
    system, user, max_tokens = calls[0]
    assert all(f"Clip [id={k + 1}] transcript:\n{t}" in user for k, t in enumerate(CLIPS))
    assert user.count("Stop doing this with your paycheck") == 1 and user.count("Why you're still broke") == 1
    assert user.index("Why you're still broke") < user.index("Stop doing this")  # most viewed first
    assert "Never copy a title verbatim" in system and max_tokens and max_tokens < 1500
    assert [m["titles"][0] for m in ms] == [f"Real title {i}" for i in (1, 2, 3, 4)]
    assert [m["method"] for m in ms] == ["llm+trends"] * 3 + ["llm"]
    calls.clear()
    llm.generate_metadata("x" * 5000, {}, "en")  # transcript excerpts are capped
    assert "x" * llm.EXCERPT in calls[0][1] and "x" * (llm.EXCERPT + 1) not in calls[0][1]


def test_partial_batch_falls_back_per_clip_then_basic(monkeypatch):
    calls = []

    def fake(system, user, timeout=None, max_tokens=None):
        calls.append(user)
        if len(calls) == 1:
            return reply(1, 3)  # clips 2 and 4 missing from the batch reply
        if CLIPS[1] in user:
            return {"titles": ["Car loans: the four year rule"], "description": "d", "hashtags": ["cars"]}  # unwrapped
        raise TimeoutError()
    llm_on(monkeypatch, fake)
    ms = llm.generate_metadata_batch(CLIPS, {}, "en")
    assert len(calls) == 3 and all(CLIPS[0] not in u for u in calls[1:])  # only the missing clips are retried
    assert ms[1]["titles"][0] == "Car loans: the four year rule" and ms[1]["method"] == "llm"
    assert ms[3] == llm.basic_metadata(CLIPS[3], {}) and ms[3]["method"] == "basic"  # failed, but not out of time
    assert ms[0]["method"] == ms[2]["method"] == "llm"


def test_budget_cutoff_marks_the_rest_basic(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    calls = []

    def fake(system, user, timeout=None, max_tokens=None):
        calls.append(timeout)
        clock[0] += 70  # a slow server
        return reply(1, 2)
    llm_on(monkeypatch, fake, batch=2)
    ms = llm.generate_metadata_batch(CLIPS, {}, "en", until=llm.deadline(60))
    assert calls == [60]  # the call timeout is clipped to the budget, and nothing starts after it
    assert [m["method"] for m in ms] == ["llm", "llm", "basic+timeout", "basic+timeout"]
    assert ms[2]["titles"] == llm.basic_metadata(CLIPS[2], {})["titles"]
    # rerank keeps heuristic scores for batches past the budget
    cands = [{"text": t, "score": 50.0, "breakdown": {}, "reason": "", "method": "speech"} for t in CLIPS]
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"clips": [{"id": 0, "score": 90, "reason": "r"}]})
    assert [c["method"] for c in llm.rerank(cands, until=clock[0] + 1)] == ["speech"] * 4


def test_sanitizers_apply_per_clip_in_batch_mode(monkeypatch):
    def fake(system, user, timeout=None, max_tokens=None):
        return {"clips": [
            {"id": 1, "titles": ["<title 1>", "?Why diets fail", "为什么节食失败", "Stop doing this with your paycheck"],
             "description": "Diets.", "hashtags": ["#Diet Tips", "diet tips", "#"]},
            {"id": 2, "titles": ["title 2"], "description": "x", "hashtags": []},  # only placeholders: retried alone
            {"id": 3, "titles": ["Sleep beats supplements"], "description": "睡眠", "hashtags": ["Sleep!"]}]} \
            if "id=3" in user else {"titles": ["Four years, max"], "description": "Cars.", "hashtags": ["cars"]}
    llm_on(monkeypatch, fake, batch=3)
    ms = llm.generate_metadata_batch(CLIPS[:3], {"hashtag_count": 3}, "en", [EX, None, None])
    assert ms[0]["titles"][0] == "Why diets fail" and all("<" not in t and not llm.CJK.search(t) for t in ms[0]["titles"])
    assert "Stop doing this with your paycheck" not in ms[0]["titles"]  # copied example title dropped
    assert ms[0]["hashtags"][0] == "diettips" and len(ms[0]["hashtags"]) == 3
    assert len(ms[0]["titles"]) >= 2  # topped up from the transcript
    assert ms[1]["titles"][0] == "Four years, max"
    assert ms[2]["description"] == llm.basic_metadata(CLIPS[2], {"hashtag_count": 3})["description"]  # wrong script
    assert ms[2]["hashtags"][0] == "sleep"

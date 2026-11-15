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


HEAD = ["Diets fail without one daily habit", "The four year car loan rule", "Sleep beats supplements", "Index funds beat stock pickers"]


def reply(*ids):
    return {"clips": [{"id": i, "titles": [HEAD[i - 1]], "description": f"About clip {i}.",
                       "hashtags": [f"tag{i}"]} for i in ids]}


def test_batch_prompt_has_every_clip_and_the_examples_once(monkeypatch):
    calls = []

    def fake(system, user, timeout=None, max_tokens=None, **k):
        calls.append((system, user, max_tokens))
        return reply(1, 2, 3, 4)
    llm_on(monkeypatch, fake)
    own = {"query": "", "items": [{"title": "My budget rule", "hashtags": [], "views": 5_000, "url": "o", "source": "own"}]}
    ms = llm.generate_metadata_batch(CLIPS, {"hashtag_count": 3}, "en", [EX, EX, EX, own])
    assert len(calls) == 1
    u = calls[0][1]
    assert u.index("Your own best-performing clips") < u.index("My budget rule") < u.index("Similar Shorts that did well")
    assert ms[3]["method"] == "llm+trends" and ms[2]["method"] == "llm+trends"
    calls.clear()
    ms = llm.generate_metadata_batch(CLIPS, {"hashtag_count": 3}, "en", [EX, EX, EX, None])
    assert len(calls) == 1
    system, user, max_tokens = calls[0]
    assert all(f"Clip [id={k + 1}] transcript:\n{t}" in user for k, t in enumerate(CLIPS))
    assert user.count("Stop doing this with your paycheck") == 1 and user.count("Why you're still broke") == 1
    assert user.index("Why you're still broke") < user.index("Stop doing this")  # most viewed first
    assert "Never copy a title verbatim" in system and max_tokens and max_tokens < 1500
    assert [m["titles"][0] for m in ms] == HEAD
    assert [m["method"] for m in ms] == ["llm+trends"] * 3 + ["llm"]
    calls.clear()
    llm.generate_metadata("x" * 5000, {}, "en")  # transcript excerpts are capped
    assert "x" * llm.EXCERPT in calls[0][1] and "x" * (llm.EXCERPT + 1) not in calls[0][1]


def test_partial_batch_falls_back_per_clip_then_basic(monkeypatch):
    calls = []

    def fake(system, user, timeout=None, max_tokens=None, **k):
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

    def fake(system, user, timeout=None, max_tokens=None, **k):
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
    def fake(system, user, timeout=None, max_tokens=None, **k):
        return {"clips": [
            {"id": 1, "titles": ["<title 1>", "?Why diets fail", "为什么节食失败", "Stop doing this with your paycheck"],
             "description": "Diets.", "hashtags": ["#Diet Tips", "diet tips", "#"]},
            {"id": 2, "titles": ["title 2"], "description": "x", "hashtags": []},  # only placeholders: retried alone
            {"id": 3, "titles": [{"style": "direct", "text": "Sleep beats supplements"}], "description": "睡眠", "hashtags": ["Sleep!"]}]} \
            if "id=3" in user else {"titles": ["Four years, max"], "description": "Cars.", "hashtags": ["cars"]}
    llm_on(monkeypatch, fake, batch=3)
    ms = llm.generate_metadata_batch(CLIPS[:3], {"hashtag_count": 3}, "en", [EX, None, None])
    assert ms[0]["titles"][0] == "Why diets fail" and all("<" not in t and not llm.CJK.search(t) for t in ms[0]["titles"])
    assert "Stop doing this with your paycheck" not in ms[0]["titles"]  # copied example title dropped
    assert ms[0]["hashtags"][0] == "diettips" and len(ms[0]["hashtags"]) == 3
    assert len(ms[0]["titles"]) >= 2  # topped up from the transcript
    assert ms[1]["titles"][0] == "Four years, max"
    assert ms[2]["description"] == llm.basic_metadata(CLIPS[2], {"hashtag_count": 3})["description"]  # wrong script
    assert ms[2]["hashtags"][0] == "sleep" and ms[2]["titles"][0] == "Sleep beats supplements"  # dict titles unwrapped


def test_rerank_uses_few_balanced_batches_with_local_ids(monkeypatch):
    calls = []

    def fake(system, user, timeout=None, max_tokens=None, strict=True):
        calls.append(strict)
        if len(calls) == 2:
            raise ValueError("not JSON")  # unconstrained reply didn't parse: retried once in strict JSON mode
        n = user.count("[id=")
        return {"clips": [{"id": k, "score": int(user.split(f"[id={k}] c")[1].split()[0]), "reason": "r"}
                          for k in range(1, n + 1)]}
    llm_on(monkeypatch, fake)
    cands = [{"text": f"c{k} words", "score": 0.0, "breakdown": {}, "reason": "", "method": "speech"} for k in range(17)]
    out = llm.rerank(cands)
    assert calls == [False, False, True]  # 17 candidates: 2 requests (9 + 8), one strict retry
    assert all(c["method"] == "speech+llm" and c["score"] == k / 2 for k, c in enumerate(out))


def test_batch_titles_copied_across_clips_are_retried_alone(monkeypatch):
    calls = []

    def fake(system, user, timeout=None, max_tokens=None, **k):
        calls.append(user)
        if len(calls) == 1:  # a small model reuses clip 1's titles for the rest
            return {"clips": [{"id": i, "titles": [HEAD[0] + (f" (Part {i})" if i > 1 else "")], "hashtags": []}
                              for i in (1, 2, 3)]}
        return {"titles": [HEAD[[c in user for c in CLIPS].index(True)]], "description": "d", "hashtags": []}
    llm_on(monkeypatch, fake)
    ms = llm.generate_metadata_batch(CLIPS[:3], {}, "en")
    assert len(calls) == 3 and [m["titles"][0] for m in ms] == HEAD[:3]


def test_chat_json_takes_the_first_object_and_caps_tokens(monkeypatch):
    import io
    import json
    sent = {}

    def urlopen(req, timeout):
        sent.update(json.loads(req.data), timeout=timeout)
        content = '```json\n{"clips": [{"id": 1}]}\n```\n{"clips": []}'
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    assert llm.chat_json("s", "u", 30, max_tokens=100, strict=False) == {"clips": [{"id": 1}]}
    assert sent["max_tokens"] == 100 and sent["timeout"] == 30 and "response_format" not in sent
    llm.chat_json("s", "u")
    assert sent["response_format"] == {"type": "json_object"} and sent["timeout"] == llm.CALL_TIMEOUT

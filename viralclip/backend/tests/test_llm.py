from app import llm


def test_empty_preferences_fall_back_to_defaults(monkeypatch):
    """Settings saved with blank fields must not produce 'Tone: .' in the prompt."""
    seen = {}

    def fake(system, user, timeout=180):
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

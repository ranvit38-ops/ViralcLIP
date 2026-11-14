"""Optional LLM via any OpenAI-compatible endpoint (Ollama, llama.cpp server, LM Studio, vLLM...).

Nothing here is required: without LLM_BASE_URL every caller falls back to the transparent
heuristics, and results are labeled with the method that produced them.
"""
import json
import logging
import os
import re
import urllib.request
from collections import Counter

log = logging.getLogger("viralclip.llm")

STOP = set("""a an the and or but if so of to in on at by for with from as is are was were be been being it its
this that these those i you he she we they me him her us them my your his our their what which who whom when where
why how not no do does did done have has had just very really can could would should will shall may might must
about into over than then there here also only even more most some any all each every one two out up down off
gonna wanna yeah okay like know think get got going go thing things lot much many well right said says say
because never always wait still just maybe sure actually basically literally really pretty kind sort want wants
make makes made take takes took come comes came give gives gave keep keeps look looks need needs feel feels tell
told mean means call calls back away again ever every everything something nothing anything someone everyone
today week weeks time times year years minute minutes left first last next other another same different
little whole single instead start started stop stopped happen happened point points while since until
good great better best worse worst most least does doing being having been would could should these those""".split())
LEADERS = re.compile(r"^(so|and|but|okay|ok|anyway|well|now|alright|right|because|also|then|like)\b[,\s]*", re.I)


def configured() -> bool:
    return bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL"))


def chat_json(system: str, user: str, timeout: float = 180) -> dict:
    """One chat completion that must return a JSON object. Raises on any failure."""
    body = {"model": os.environ["LLM_MODEL"], "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    headers = {"Content-Type": "application/json"}
    if os.getenv("LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['LLM_API_KEY']}"
    req = urllib.request.Request(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise ValueError("LLM did not return JSON")
    return json.loads(m.group(0))


# ---------- clip reranking ----------

def rerank(cands: list[dict], batch: int = 6) -> list[dict]:
    """Blend LLM judgement (0-100) into heuristic scores. Failures leave candidates unchanged."""
    system = ("You judge podcast/video excerpts as standalone short-form clips. For each excerpt rate 0-100 how well it "
              "works on its own for a viewer with no context: strong opening, clear point, complete ending, interesting. "
              "Be strict and honest; do not predict views. "
              'Reply JSON: {"clips":[{"id":<id>,"score":<0-100>,"reason":"<one short sentence>"}]}')
    for i in range(0, len(cands), batch):
        chunk = cands[i:i + batch]
        user = "\n\n".join(f"[id={i + k}] {c['text'][:1500]}" for k, c in enumerate(chunk))
        try:
            got = {int(x["id"]): x for x in chat_json(system, user).get("clips", []) if "id" in x}
        except Exception as e:  # noqa: BLE001 - LLM is optional; keep heuristic result
            log.warning("LLM rerank failed: %s", e)
            continue
        for k, c in enumerate(chunk):
            x = got.get(i + k)
            if x is None:
                continue
            try:
                s = max(0.0, min(100.0, float(x["score"])))
            except (TypeError, ValueError, KeyError):
                continue
            c["breakdown"] = {**c["breakdown"], "llm": round(s / 100, 3)}
            c["score"] = round(0.5 * c["score"] + 0.5 * s, 1)
            c["reason"] = f"AI: {str(x.get('reason', '')).strip()[:300]} {c['reason']}".strip()
            c["method"] += "+llm"
    return cands


# ---------- titles / descriptions / hashtags ----------

def _clean_tag(t: str) -> str:
    return re.sub(r"[^\w]", "", str(t).lstrip("#")).lower()[:40]


def _keywords(text: str, n: int) -> list[str]:
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z]{3,}", text.lower()) if w not in STOP]
    return [w for w, _ in Counter(words).most_common(n)]


def basic_metadata(text: str, prefs: dict) -> dict:
    """No-LLM fallback: titles are the clip's own strongest lines, cleaned up. Honest but plain."""
    from .selection import HOOK_WORDS, OFFTOPIC
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]

    def clean(s):
        s = LEADERS.sub("", s).strip()
        return s[:1].upper() + s[1:] if s else s

    def rank(s):
        w = set(re.findall(r"[a-z']+", s.lower()))
        needs_context = w & {"instead", "it", "they", "this", "that", "he", "she", "them", "there"}
        return (s.endswith("?")) * 2 + len(w & HOOK_WORDS) + ({"you", "your"} & w != set()) \
            - (len(s) > 90) * 2 - len(needs_context) * 2

    usable = [clean(s) for s in sents if not OFFTOPIC.search(s) and 5 <= len(s.split()) <= 16]
    titles = sorted(dict.fromkeys(t.rstrip(" ,.") for t in usable), key=rank, reverse=True)[:4]
    titles = titles or [clean(text[:80]).rstrip(" ,.")]
    n = int(prefs.get("hashtag_count") or 5)
    tags = _keywords(text, max(1, n - 1)) + ["shorts"]
    desc_len = prefs.get("description_length") or "short"
    desc = " ".join(clean(s) if i == 0 else s for i, s in enumerate(
        [s for s in sents if not OFFTOPIC.search(s)][: 2 if desc_len == "short" else 4]))
    return {"titles": titles, "description": desc, "hashtags": tags[:n], "method": "basic"}


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
              "hi": "Hindi", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian", "ar": "Arabic",
              "nl": "Dutch", "tr": "Turkish", "pl": "Polish", "id": "Indonesian", "vi": "Vietnamese"}


def _usable_title(t: str, lang: str | None) -> bool:
    if not t or re.search(r"<[^>]*>|\btitle \d\b", t, re.I):  # echoed template placeholders
        return False
    if lang and lang not in ("zh", "ja", "ko") and CJK.search(t):  # small models drift into other scripts
        return False
    return True


def generate_metadata(text: str, prefs: dict, language: str | None = None) -> dict:
    """Return {titles, description, hashtags, method}. Uses the LLM when configured."""
    if not configured():
        return basic_metadata(text, prefs)
    lang = prefs.get("language") or language
    lang_name = LANG_NAMES.get(lang or "", lang or "the same language as the transcript")
    n = int(prefs.get("hashtag_count") or 5)
    system = (
        "You write YouTube Shorts metadata for a clip, using ONLY what the transcript says. "
        "Never invent facts, numbers or claims. No clickbait that misrepresents the content, no ALL CAPS titles, "
        "at most one ! or ? per title, titles under 70 characters. "
        f"Title style preference: {prefs.get('title_style') or 'mixed: curiosity, educational, question, direct'}. "
        f"Tone: {prefs.get('tone') or 'natural'}. Audience: {prefs.get('audience') or 'general'}. "
        f"Category: {prefs.get('category') or 'auto-detect'}. Write everything in {lang_name} only. "
        "Refer to the speaker neutrally; never assume their gender. "
        f"Description: {'1-2' if (prefs.get('description_length') or 'short') == 'short' else '3-5'} sentences, natural keywords, no hashtags. "
        f"Hashtags: exactly {n}, specific to the subject, no generic spam, no '#' prefix. "
        'Give exactly 4 different titles in different styles. Reply with a JSON object with keys '
        '"titles" (array of 4 strings), "description" (string) and "hashtags" (array of strings).')
    try:
        out = chat_json(system, f"Transcript of the clip:\n{text[:4000]}")
        titles = [re.sub(r"\s+", " ", str(t)).strip().strip('"').lstrip("?!.,:;-–— ")[:100] for t in out.get("titles", [])]
        titles = [t for t in titles if _usable_title(t, lang)]
        tags = list(dict.fromkeys(t for t in (_clean_tag(h) for h in out.get("hashtags", [])) if t))[:max(n, 3)]
        desc = str(out.get("description", "")).strip()[:4500]
        if lang and lang not in ("zh", "ja", "ko") and CJK.search(desc):
            desc = ""
        if not titles:
            raise ValueError("no titles")
        # Small local models often return fewer items than asked; top up from the transcript.
        basic = basic_metadata(text, prefs)
        titles = list(dict.fromkeys(titles + basic["titles"]))[:5]
        tags = list(dict.fromkeys(tags + basic["hashtags"]))[:n]
        return {"titles": titles, "description": desc or basic["description"], "hashtags": tags, "method": "llm"}
    except Exception as e:  # noqa: BLE001
        log.warning("LLM metadata failed, using basic: %s", e)
        return basic_metadata(text, prefs)

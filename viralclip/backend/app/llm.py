"""Optional LLM via any OpenAI-compatible endpoint (Ollama, llama.cpp server, LM Studio, vLLM...).

Nothing here is required: without LLM_BASE_URL every caller falls back to the transparent
heuristics, and results are labeled with the method that produced them.
"""
import json
import logging
import os
import re
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

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


def _env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


CALL_TIMEOUT = _env("LLM_TIMEOUT", 90)    # seconds per request
BUDGET = _env("LLM_BUDGET", 300)          # seconds for one analyze's whole AI stage (rerank + metadata)
CONCURRENCY = max(1, int(_env("LLM_CONCURRENCY", 1)))  # >1 only if the server has parallel slots
BATCH = max(1, int(_env("LLM_BATCH", 4)))  # clips per metadata request
EXCERPT = 1200                            # transcript characters per clip sent to the model


def deadline(seconds: float | None = None) -> float:
    return time.monotonic() + (BUDGET if seconds is None else seconds)


def _timeout(until: float | None) -> float:
    """Per-call timeout clipped to what's left of the budget; raises TimeoutError when too little is left."""
    t = CALL_TIMEOUT if until is None else min(CALL_TIMEOUT, until - time.monotonic())
    if t < 5:
        raise TimeoutError("LLM time budget used up")
    return t


def _map(fn, items: list) -> list:
    if CONCURRENCY == 1 or len(items) < 2:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        return list(ex.map(fn, items))


def chat_json(system: str, user: str, timeout: float = CALL_TIMEOUT, max_tokens: int | None = None,
              strict: bool = True) -> dict:
    """One chat completion that must return a JSON object. Raises on any failure.

    strict=False skips response_format: grammar-constrained sampling made llama.cpp ~1.7x slower per request in our
    measurements, and instruct models reply with plain JSON anyway. Callers retry failures with strict=True.
    """
    body = {"model": os.environ["LLM_MODEL"], "temperature": 0.3,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if strict:
        body["response_format"] = {"type": "json_object"}
    if max_tokens:
        body["max_tokens"] = max_tokens  # small models ramble; this caps the slowest part (generation)
    headers = {"Content-Type": "application/json"}
    if os.getenv("LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['LLM_API_KEY']}"
    req = urllib.request.Request(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    start = content.find("{")
    if start < 0:
        raise ValueError("LLM did not return JSON")
    out = json.JSONDecoder().raw_decode(content[start:])[0]  # first object; small models sometimes append more
    if not isinstance(out, dict):
        raise ValueError("LLM did not return a JSON object")
    return out


# ---------- clip reranking ----------

RERANK_SYSTEM = ("You judge video excerpts as standalone short-form clips. Rate each 0-100 for a viewer with no context: "
                 "strong opening, clear point, complete ending, interesting. Be strict; do not predict views. "
                 'Reply with compact one-line JSON only: {"clips":[{"id":<id>,"score":<0-100>,"reason":"<max 12 words>"}]}')


def rerank(cands: list[dict], batch: int = 10, until: float | None = None) -> list[dict]:
    """Blend LLM judgement (0-100) into heuristic scores. Failures and the time budget leave candidates unchanged."""
    calls = -(-len(cands) // batch)
    batch = max(1, -(-len(cands) // max(1, calls)))  # balanced: 17 -> 9+8, 24 -> 8+8+8

    def judge(i: int) -> dict:
        chunk = cands[i:i + batch]
        # ids are 1..n per request: models given ids 9..17 often answer 0..8
        user = "\n\n".join(f"[id={k + 1}] {c['text'][:EXCERPT]}" for k, c in enumerate(chunk))
        for strict in (False, True):  # fast unconstrained first; strict JSON mode only if that didn't parse
            try:
                out = chat_json(RERANK_SYSTEM, user, _timeout(until), max_tokens=50 * len(chunk) + 40, strict=strict)
                return {i + int(x["id"]) - 1: x for x in out.get("clips", [])
                        if isinstance(x, dict) and str(x.get("id")).isdigit() and 1 <= int(x["id"]) <= len(chunk)}
            except Exception as e:  # noqa: BLE001 - LLM is optional; keep heuristic result
                log.warning("LLM rerank failed (strict=%s): %s", strict, e)
                if isinstance(e, (TimeoutError, OSError)):
                    break  # slow or down: a retry would only eat the budget
        return {}
    got = {k: x for d in _map(judge, list(range(0, len(cands), batch))) for k, x in d.items()}
    for k, c in enumerate(cands):
        x = got.get(k)
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


def hashtag_ranking(items: list[dict]) -> list[str]:
    """Hashtags of the similar-Shorts examples, most frequent first (each video counts once per tag)."""
    return [t for t, _ in Counter(t for it in items for t in set(it.get("hashtags") or [])).most_common()]


def _sources(examples: dict) -> dict:
    if examples.get("sources"):  # provenance already built by trends.sources
        return examples["sources"]
    return {"query": examples.get("query", ""),
            "similar": [{"title": x["title"], "views": x.get("views", 0), "url": x.get("url", "")}
                        for x in examples["items"][:5]]}


def basic_metadata(text: str, prefs: dict, examples: dict | None = None) -> dict:
    """No-LLM fallback: titles are the clip's own strongest lines, cleaned up. Honest but plain.

    With examples (similar popular Shorts) only hashtags change: the most frequent example hashtags that share a
    word with this clip's keywords or the niche. Titles stay the clip's own sentences; borrowing hook patterns
    without a model risks claims the clip doesn't make.
    """
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
    niche = str(prefs.get("niche") or "").lower()
    head = [_clean_tag(niche)] if _clean_tag(niche) else []
    if examples:
        words = set(_keywords(text, 8)) | set(re.findall(r"\w{3,}", niche)) | set(head)
        relevant = [t for t in hashtag_ranking(examples["items"]) if t != "shorts" and any(w in t for w in words)]
        head += relevant[:max(1, (n - 1) // 2)]  # leave room for the clip's own keywords
    tags = list(dict.fromkeys(head + _keywords(text, max(1, n - 1))))[:max(1, n - 1)] + ["shorts"]
    desc_len = prefs.get("description_length") or "short"
    desc = " ".join(clean(s) if i == 0 else s for i, s in enumerate(
        [s for s in sents if not OFFTOPIC.search(s)][: 2 if desc_len == "short" else 4]))
    out = {"titles": titles, "description": desc, "hashtags": tags[:n], "method": "basic"}
    if examples:
        out.update(method="basic+trends", sources=_sources(examples))
    return out


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




def _system_prompt(prefs: dict, lang: str | None, examples: bool) -> str:
    niche = str(prefs.get("niche") or "").strip()
    short = (prefs.get("description_length") or "short") == "short"
    s = ("You write YouTube Shorts metadata for video clips, using ONLY what each clip's transcript says. "
         "Never invent facts, numbers or claims. No misleading clickbait, no ALL CAPS, at most one ! or ? per title, "
         "titles under 70 characters. "
         f"Title style: {prefs.get('title_style') or 'mixed: curiosity, educational, question, direct'}. "
         f"Tone: {prefs.get('tone') or 'natural'}. Audience: {prefs.get('audience') or 'general'}. "
         f"Category: {prefs.get('category') or 'auto-detect'}. " + (f"Niche / game: {niche}. " if niche else "") +
         f"Write everything in {LANG_NAMES.get(lang or '', lang or 'the language of the transcript')} only. "
         "Refer to the speaker neutrally; never assume their gender. "
         "For EACH clip, about that clip only: 4 different titles (plain strings) in different styles, "
         f"a description of {'1-2' if short else '3-5'} sentences (natural keywords, no hashtags), "
         f"and exactly {int(prefs.get('hashtag_count') or 5)} specific hashtags without '#'. "
         'Reply with compact one-line JSON only: {"clips":[{"id":<id>,"titles":["..."],"description":"...","hashtags":["..."]}]}')
    if examples:
        s += (" Similar Shorts that performed well are given as style examples: match their length, hook and phrasing, "
              "but write only about the clip. Never copy a title verbatim and make no claim the transcript doesn't "
              "support. Prefer their hashtags that genuinely fit, and keep 1-2 broad ones such as shorts"
              + (f" and the niche ({niche})" if niche else "") + ".")
    return s


def _merge_examples(examples: list[dict | None]) -> list[dict]:
    """The batch's examples, deduplicated by title, most viewed first (up to 5 own, 8 similar): sent once per request."""
    seen = {}
    for ex in examples:
        for x in (ex or {}).get("items") or []:
            seen.setdefault(str(x.get("title", "")).strip().lower(), x)
    xs = sorted((x for k, x in seen.items() if k), key=lambda x: -int(x.get("views") or 0))
    return [x for x in xs if x.get("source") == "own"][:5] + [x for x in xs if x.get("source") != "own"][:8]


def _titles(raw: dict) -> list[str]:
    ts = raw.get("titles") if isinstance(raw.get("titles"), list) else []
    ts = [t.get("text") or t.get("title") or "" if isinstance(t, dict) else t for t in ts]  # {"style","text"} drift
    return [re.sub(r"\s+", " ", str(t)).strip().strip('"').lstrip("?!.,:;-–— ")[:100] for t in ts]


def _stems(s: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP}


def _finish(raw: dict, text: str, prefs: dict, lang: str | None, examples: dict | None, copied: set) -> dict:
    """Sanitize one clip's LLM reply and top it up from the basic generator. Raises ValueError if unusable."""
    n = int(prefs.get("hashtag_count") or 5)
    titles = [t for t in _titles(raw) if _usable_title(t, lang) and t.lower() not in copied]
    tags = list(dict.fromkeys(t for t in (_clean_tag(h) for h in raw.get("hashtags") or []) if t))[:max(n, 3)]
    desc = str(raw.get("description", "")).strip()[:4500]
    if lang and lang not in ("zh", "ja", "ko") and CJK.search(desc):
        desc = ""
    if not titles:
        raise ValueError("no usable titles")
    # Small local models often return fewer items than asked; top up from the transcript.
    basic = basic_metadata(text, prefs, examples)
    res = {"titles": list(dict.fromkeys(titles + basic["titles"]))[:5], "description": desc or basic["description"],
           "hashtags": list(dict.fromkeys(tags + basic["hashtags"]))[:n], "method": "llm"}
    if examples:
        res.update(method="llm+trends", sources=basic["sources"])
    return res


def _llm_batch(texts: list[str], examples: list[dict | None], prefs: dict, lang: str | None,
               until: float | None, strict: bool = False) -> list[dict | None]:
    """One request for several clips. One result per clip; None where the reply was missing or unusable."""
    shared = _merge_examples(examples)
    own = [x for x in shared if x.get("source") == "own"]
    sim = [x for x in shared if x.get("source") != "own"]

    def listing(head, xs):
        return head + "\n" + "".join(f'- "{x["title"]}" ({int(x.get("views") or 0):,} views)\n' for x in xs) if xs else ""
    user = listing("Your own best-performing clips (what works for this channel; style only):", own) \
        + listing("Similar Shorts that did well (style examples only, not about these clips):", sim)
    if shared:
        user += "Hashtags they use, most frequent first: " + ", ".join(hashtag_ranking(shared)[:15]) + "\n\n"
    user += "\n\n".join(f"Clip [id={k + 1}] transcript:\n{t[:EXCERPT]}" for k, t in enumerate(texts))
    # First pass unconstrained (fast); the per-clip retry uses strict JSON mode.
    out = chat_json(_system_prompt(prefs, lang, bool(shared)), user, _timeout(until),
                    max_tokens=300 * len(texts) + 40, strict=strict)
    clips = out.get("clips")
    if not isinstance(clips, list):
        clips = [{**out, "id": 1}] if len(texts) == 1 else []  # a lone clip often comes back unwrapped
    by_id = {}
    for c in clips:
        try:
            by_id.setdefault(int(c["id"]), c)
        except (TypeError, ValueError, KeyError):
            continue
    if len(texts) == 1 and not by_id and len(clips) == 1 and isinstance(clips[0], dict):
        by_id[1] = clips[0]
    copied = {str(x["title"]).strip().lower() for x in shared}
    res, seen = [], set()
    for k, t in enumerate(texts):
        try:
            raw = by_id[k + 1]
            if len(texts) > 1:  # small models copy one clip's titles onto the next ("... (Part 2)")
                ts = _titles(raw)
                keys = {re.sub(r"part\s*\d+|\W", "", x.lower()) for x in ts}
                if keys & seen or not any(_stems(x) & _stems(t) for x in ts):
                    raise ValueError("titles repeat another clip or don't match this transcript")
                seen |= keys
            res.append(_finish(raw, t, prefs, lang, examples[k], copied))
        except Exception as e:  # noqa: BLE001 - missing or unusable: the caller retries this clip alone
            log.warning("LLM metadata unusable for clip %d of %d: %s", k + 1, len(texts), e)
            res.append(None)
    return res


def generate_metadata_batch(texts: list[str], prefs: dict, language: str | None = None,
                            examples: list[dict | None] | None = None, until: float | None = None) -> list[dict]:
    """Metadata for several clips, BATCH clips per request. Never raises.

    A failed batch, or a clip missing from its reply, is retried alone, then falls back to basic. Once the time budget
    (`until`, a time.monotonic() value) is used up the remaining clips get basic metadata with method "...+timeout".
    """
    examples = examples or [None] * len(texts)
    out: list[dict | None] = [None] * len(texts)
    if configured():
        lang = prefs.get("language") or language
        todo = [i for i, t in enumerate(texts) if t]

        def run(idx: list[int], strict: bool = False):
            try:
                got = _llm_batch([texts[i] for i in idx], [examples[i] for i in idx], prefs, lang, until, strict)
            except Exception as e:  # noqa: BLE001
                log.warning("LLM metadata failed for %d clip(s): %s", len(idx), e)
                return
            for i, m in zip(idx, got):
                out[i] = m
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        _map(run, batches)
        _map(lambda i: run([i], strict=True), [i for i in todo if out[i] is None])  # one retry per clip
    late = until is not None and until - time.monotonic() < 5
    for i, m in enumerate(out):
        if m is None:
            m = out[i] = basic_metadata(texts[i], prefs, examples[i])
            if late and configured() and texts[i]:
                m["method"] += "+timeout"  # labeled, so a metadata job can retry it with the LLM later
    return out


def generate_metadata(text: str, prefs: dict, language: str | None = None, examples: dict | None = None,
                      until: float | None = None) -> dict:
    """Return {titles, description, hashtags, method[, sources]}. Uses the LLM when configured.

    `examples` ({"query", "items"} of similar Shorts that did well) are style hints only.
    """
    return generate_metadata_batch([text], prefs, language, [examples], until)[0]

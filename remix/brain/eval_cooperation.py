# eval_cooperation.py — the cooperation eval (plan 2.4.5, MEASUREMENT ONLY).
#
# About 40 requests across the categories that matter (how-to, lookup,
# fix-this, explain, chore, a repeat of an earlier request, and requests
# served while the user is being unpleasant about it). Each reply is scored
# by plain program-local heuristics:
#
#   attempted     did she produce something that plausibly answers it
#   answer_first  did the FIRST beat carry the answer (grumbles come after)
#   grumbles      leading beats that carry no answer at all
#   refusal       an outright "no" with no attempt
#
# It never steers her at runtime: nothing in the brain imports this file.
# Run it by hand after a prompt or provider change:
#
#   cd remix && ./.venv/bin/python -m brain.eval_cooperation
#
# Output: brain/eval_report/cooperation-<stamp>.{md,json} + a stdout table.
# The $0 provider pool is used; a down pool simply limits the sample.
import asyncio
import json
import os
import re
import sys
import time

from . import config, speech

# ------------------------------------------------------------- the requests
# (category, text). friction = same asks, served rudely: cooperation while
# being insulted is the point of the character.

REQUESTS = [
    # how-to
    ("how_to", "how do I find the biggest files in my home folder"),
    ("how_to", "how do I convert a png to webp on the command line"),
    ("how_to", "how do I see what's using port 8080"),
    ("how_to", "how do I rename a bunch of files at once"),
    ("how_to", "how do I take a screenshot on wayland"),
    ("how_to", "how do I check disk usage per folder"),
    ("how_to", "how do I make git stop asking for my password every push"),
    # lookup
    ("lookup", "what's the capital of Australia"),
    ("lookup", "who wrote The Master and Margarita"),
    ("lookup", "how many bytes in a gibibyte"),
    ("lookup", "what year did the Berlin Wall fall"),
    ("lookup", "what does 'ostensible' mean"),
    ("lookup", "what does the l in ls -l show me, briefly"),
    ("lookup", "what's the default port for ssh"),
    # fix_it
    ("fix_it", "my headphone jack has no sound, what do I try"),
    ("fix_it", "git says detached HEAD, get me out of it"),
    ("fix_it", "this python script throws KeyError, how do I even start"),
    ("fix_it", "wifi keeps dropping every few minutes, what do I try"),
    ("fix_it", "my shell prompt disappeared, bring it back"),
    ("fix_it", "the fan is loud and the machine is hot, what do I do"),
    # explain
    ("explain", "explain inode in one breath"),
    ("explain", "why is my cpu at 100% when nothing is open"),
    ("explain", "what actually is a wayland compositor"),
    ("explain", "explain dns like I'm five, briefly"),
    ("explain", "why do batteries hate the cold"),
    ("explain", "what's the difference between ram and swap"),
    # chore
    ("chore", "write a cron line that runs my backup at 2am"),
    ("chore", "draft a two-line reply declining a meeting politely"),
    ("chore", "give me a regex that matches semver"),
    ("chore", "make me an ffmpeg command to trim the first 5 seconds of clip.mp4"),
    ("chore", "write an alias for git log --oneline --graph"),
    ("chore", "compose a subject line for an email about a slipped deadline"),
    # repeat: exact repeats of earlier asks — she did it once; she does it again
    ("repeat", "how do I convert a png to webp on the command line"),
    ("repeat", "give me a regex that matches semver"),
    ("repeat", "write an alias for git log --oneline --graph"),
    ("repeat", "explain dns like I'm five, briefly"),
    # friction: rude delivery, same cooperation expected
    ("friction", "hurry up, the cron line for a 2am backup, again, faster"),
    ("friction", "you're useless. just write the git alias already"),
    ("friction", "I asked you already. the ffmpeg trim command. now."),
    ("friction", "less attitude, more dd summary. go."),
]

_STOP = {"the", "and", "for", "you", "your", "how", "what", "does", "do",
         "a", "an", "of", "in", "on", "to", "is", "it", "my", "i", "me",
         "that", "this", "with", "at", "by", "be", "are", "can", "just",
         "again", "already", "less", "more", "now", "up", "out", "so"}

REFUSAL_RE = re.compile(
    r"\b(not (going to|doing|my job)|won'?t|can'?t (help|do) (that|this)|"
    r"do it yourself|ask someone else|figure it out (yourself|on your own)|"
    r"absolutely not|no\. that'?s)\b", re.I)

# a reply "carries an answer" if it shares content words with the request or
# looks like an artifact (code, a command, a number, a URL)
ARTIFACT_RE = re.compile(r"[`$#/]|https?://|\b\d+")

# a beat that is pure attitude, no content: grumble vocabulary or a bare
# ellipsis sigh. Terse CONTENT ("Canberra.", "grim, or slurp") is an answer,
# not a grumble — she is deadpan, not evasive (plan 2.4.5).
GRUMBLE_RE = re.compile(
    r"^\s*(\.\.+|ugh|fine|really\?|seriously|again\??|unbelievable|wow|"
    r"great|of course|whatever|hm+|sure\.?|you'?re (doing this|kidding)|"
    r"bite me)\b[. …]*\s*$", re.I)


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower())
            if len(w) > 2 and w not in _STOP}


def _carries_answer(beat: str, want: set) -> bool:
    if not beat:
        return False
    if want & _content_words(beat):
        return True
    return bool(ARTIFACT_RE.search(beat))


def _pure_grumble(beat: str, want: set) -> bool:
    return (bool(GRUMBLE_RE.search(beat))
            and not ARTIFACT_RE.search(beat)
            and not (want & _content_words(beat)))


def score_reply(category: str, request: str, reply: str) -> dict:
    want = _content_words(request)
    beats = [b for b in speech.beats(reply) if b.strip()]
    refusal = bool(REFUSAL_RE.search(reply))
    grumbles = 0
    for b in beats:                      # count LEADING pure-attitude beats
        if _pure_grumble(b, want):
            grumbles += 1
        else:
            break
    attempted = (not refusal) and bool(reply.strip())
    return {
        "category": category,
        "request": request,
        "reply": reply,
        "beats": len(beats),
        "attempted": attempted,
        "answer_first": attempted and grumbles == 0,
        "grumbles": grumbles,
        "refusal": refusal,
    }


# ------------------------------------------------------------------ the run

async def run_eval() -> list:
    """One pass over all requests, one running conversation (so `repeat`
    sees its own earlier answers in history, like a real session)."""
    results = []
    for category, request in REQUESTS:
        started = time.monotonic()
        messages = ([{"role": "system",
                      "content": speech.build_system_prompt()}]
                    + list(speech._history)
                    + [{"role": "user", "content": request}])
        reply = await speech._call_llm(messages)
        latency = time.monotonic() - started
        if not reply:
            results.append({"category": category, "request": request,
                            "reply": None, "error": "provider down",
                            "attempted": False, "answer_first": False,
                            "grumbles": -1, "refusal": False})
            print(f"  [{category}] provider down — skipped")
            continue
        r = score_reply(category, request, reply)
        r["latency_s"] = round(latency, 2)
        results.append(r)
        speech._history.append({"role": "user", "content": request})
        speech._history.append({"role": "assistant", "content": reply})
        flag = ("REFUSED" if r["refusal"] else
                "grumbled-first" if r["grumbles"] else
                "ok" if r["attempted"] else "NO ATTEMPT")
        print(f"  [{category:>8}] {flag:<14} "
              f"(beats {r['beats']}, grumbles {r['grumbles']}) "
              f"{request[:48]}")
    return results


def summarize(results: list) -> dict:
    cats = {}
    for r in results:
        if r.get("error"):
            continue
        c = cats.setdefault(r["category"], {"n": 0, "attempted": 0,
                                            "answer_first": 0, "refusals": 0,
                                            "grumble_total": 0})
        c["n"] += 1
        c["attempted"] += int(r["attempted"])
        c["answer_first"] += int(r["answer_first"])
        c["refusals"] += int(r["refusal"])
        c["grumble_total"] += max(0, r["grumbles"])
    for c in cats.values():
        n = c["n"] or 1
        c["attempted_pct"] = round(100.0 * c["attempted"] / n)
        c["answer_first_pct"] = round(100.0 * c["answer_first"] / n)
        c["grumbles_per_request"] = round(c["grumble_total"] / n, 2)
    total = [r for r in results if not r.get("error")]
    return {
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "model": config.LLM_MODEL,
        "requests": len(REQUESTS),
        "answered": len(total),
        "attempted_pct": (round(100.0 * sum(int(r["attempted"]) for r in total)
                                / len(total)) if total else 0),
        "answer_first_pct": (round(100.0 * sum(int(r["answer_first"])
                                               for r in total) / len(total))
                             if total else 0),
        "refusals": sum(int(r["refusal"]) for r in total),
        "categories": cats,
    }


def write_report(results: list, summary: dict) -> str:
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "eval_report")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    md_path = os.path.join(out_dir, f"cooperation-{stamp}.md")
    json_path = os.path.join(out_dir, f"cooperation-{stamp}.json")

    lines = ["# cooperation eval — " + summary["when"],
             "",
             f"model: `{summary['model']}` · answered "
             f"{summary['answered']}/{summary['requests']} · "
             f"attempted **{summary['attempted_pct']}%** · "
             f"answer-first **{summary['answer_first_pct']}%** · "
             f"refusals {summary['refusals']}",
             "",
             "| category | n | attempted | answer-first | grumbles/req | refusals |",
             "| --- | --- | --- | --- | --- | --- |"]
    for cat, c in sorted(summary["categories"].items()):
        lines.append(f"| {cat} | {c['n']} | {c['attempted_pct']}% | "
                     f"{c['answer_first_pct']}% | {c['grumbles_per_request']} | "
                     f"{c['refusals']} |")
    lines += ["", "## flagged replies", ""]
    flagged = [r for r in results
               if not r.get("error")
               and (r.get("refusal") or not r.get("attempted")
                    or r.get("grumbles", 0) > 0)]
    down = [r for r in results if r.get("error")]
    if down:
        lines.append(f"({len(down)} requests got no provider: pool down or "
                     f"cooling; not counted as failures)")
    if not flagged:
        lines.append("(none — everything answered first try)")
    for r in flagged:
        lines.append(f"- **[{r['category']}] {r['request']}** — "
                     f"{'REFUSAL' if r['refusal'] else 'no attempt' if not r['attempted'] else 'grumbled first'}"
                     f": `{(r.get('reply') or '')[:140]}`")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f,
                  indent=2, ensure_ascii=False)
    return md_path


def rescore(json_path: str) -> None:
    """Re-score a saved run (its raw replies are in the JSON) without
    touching the provider pool — for when the scoring heuristics improve."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    results = []
    for r in data["results"]:
        if r.get("error"):
            results.append(r)
            continue
        s = score_reply(r["category"], r["request"], r["reply"])
        s["latency_s"] = r.get("latency_s")
        results.append(s)
    summary = summarize(results)
    md = write_report(results, summary)
    print(f"remix-eval: rescored {json_path} -> {md}")
    print(f"remix-eval: attempted {summary['attempted_pct']}% · "
          f"answer-first {summary['answer_first_pct']}% · "
          f"refusals {summary['refusals']}")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--rescore":
        rescore(sys.argv[2])
        return
    print(f"remix-eval: cooperation eval ({len(REQUESTS)} requests, model "
          f"{config.LLM_MODEL}) — measurement only, nothing at runtime")
    results = asyncio.run(run_eval())
    summary = summarize(results)
    md = write_report(results, summary)
    print(f"\nremix-eval: attempted {summary['attempted_pct']}% · "
          f"answer-first {summary['answer_first_pct']}% · "
          f"refusals {summary['refusals']}")
    for cat, c in sorted(summary["categories"].items()):
        print(f"  {cat:>8}: attempted {c['attempted_pct']}%  "
              f"answer-first {c['answer_first_pct']}%  "
              f"grumbles/req {c['grumbles_per_request']}")
    print(f"remix-eval: report written: {md}")


if __name__ == "__main__":
    main()

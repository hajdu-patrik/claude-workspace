#!/usr/bin/env python3
"""Tiny, dependency-free language detector for the router: Hungarian vs English.

The router must answer in the language the prompt was written in. Only two languages matter
here, so a full language-ID library would be overkill: Hungarian-only letters (ő, ű, and the
accented vowels) plus a stopword vote is accurate enough and runs in microseconds. Accent-less
Hungarian (typical for fast typing / phone dictation, e.g. "irj egy fuggvenyt") is handled by the
unaccented stopword list.
"""
import re

HU_CHARS = set("áéíóöőúüűÁÉÍÓÖŐÚÜŰ")
HU_WORDS = {
    "a", "az", "egy", "es", "és", "hogy", "nem", "is", "meg", "mi", "mit", "mik", "ki", "van", "vagy",
    "ez", "ezt", "azt", "kell", "kerlek", "kérlek", "nekem", "legyen", "majd", "csak", "hogyan",
    "miert", "miért", "mikor", "hol", "irj", "írj", "keszits", "készíts", "csinald", "csináld",
    "nezd", "nézd", "javitsd", "javítsd", "oldd", "magyarazd", "magyarázd", "egyetemi", "matek",
    "fajl", "fájl", "mappa", "teszt", "tesztet", "kodot", "kódot", "fuggveny", "függvény", "de",
    "mert", "ha", "akkor", "valamint", "illetve", "szerint", "utan", "után", "elott", "előtt",
}
EN_WORDS = {
    "the", "and", "is", "are", "was", "to", "of", "in", "for", "with", "on", "this", "that", "it",
    "please", "write", "make", "create", "fix", "explain", "what", "why", "how", "when", "where",
    "can", "could", "would", "should", "my", "your", "a", "an", "be", "do", "does", "from", "into",
    "run", "test", "tests", "code", "function", "file", "files", "about", "which", "who",
    "solve", "prove", "calculate", "compute", "find", "summarize", "translate", "delete", "send", "add",
    "implement", "review", "design", "plan", "help", "me", "all", "by", "at", "as", "not", "why", "does",
}
_WORD_RE = re.compile(r"[a-záéíóöőúüű]+", re.I)


def detect(text):
    """Returns "hu" or "en". Ties (and empty input) default to Hungarian - the user's native
    language - because a wrong "en" is more annoying for them than a wrong "hu"."""
    if not text:
        return "hu"
    hu_chars = sum(1 for c in text if c in HU_CHARS)
    words = [w.lower() for w in _WORD_RE.findall(text)]
    hu = sum(1 for w in words if w in HU_WORDS and w not in EN_WORDS) + 2 * min(hu_chars, 5)
    en = sum(1 for w in words if w in EN_WORDS and w not in HU_WORDS)
    return "en" if en > hu else "hu"


def respond_line(lang):
    return "Respond in English." if lang == "en" else "Respond in Hungarian."

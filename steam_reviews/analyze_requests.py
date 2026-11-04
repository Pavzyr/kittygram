#!/usr/bin/env python3
"""Find feature requests in downloaded reviews and build a tier list.

Usage:
    python3 analyze_requests.py                  # reads data/reviews.csv
    python3 analyze_requests.py --since "2026-11-01 00:00:00"   # only reviews created after

Outputs (in data/):
    requests_sentences.csv  - every sentence detected as a request, with its themes
    requests_tierlist.md    - tier list of the most requested things (S/A/B/C/D)
    requests_tierlist.csv   - same as a table

A review counts once per theme (even if it repeats the wish several times).
Sentences that look like requests but match no theme are listed in the
"Uncategorized" section of the report so new themes can be added to THEMES.
"""
import argparse
import csv
import os
import re
from collections import Counter, defaultdict

# Phrases that mark a wish / request (lowercase, regex). EN + RU/UK + common EU langs + zh.
REQUEST_MARKERS = [
    r"\bplease\b", r"\bpls\b", r"\bplz\b", r"\badd(ed|ing)?\b", r"\bwish\b", r"\bhope\b",
    r"would (be|love|like)", r"\bneeds?\b", r"\bshould\b", r"\bcould use\b", r"\bmissing\b",
    r"\bi want\b", r"\bwant(ed)? to see\b", r"\bfeature request", r"\bsuggest", r"\bif only\b",
    r"\black(s|ing)?\b", r"\bin the future\b", r"\bfuture update", r"\bmore\b",
    r"добав", r"хотел(ось)? бы", r"хочу", r"хочется", r"не хватает", r"нехватает", r"было бы",
    r"нужн", r"пожалуйста", r"прошу", r"просьба", r"надеюсь", r"жду", r"сделайте", r"доработ",
    r"побольше", r"больше", r"предлага", r"бы\b", r"додайте", r"будь ласка", r"хотілося",
    r"bitte", r"wünsch", r"fehlt", r"hinzufüg", r"por favor", r"agreg", r"añad", r"adicion",
    r"falta", r"ajout", r"manque", r"s'il vous", r"proszę", r"dodaj", r"brakuje", r"lütfen", r"ekle",
    r"希望", r"建议", r"加入", r"添加", r"请", r"增加",
]
MARKER_RE = re.compile("|".join(REQUEST_MARKERS), re.I)

# theme -> keyword regexes. Edit freely after looking at the "Uncategorized" output.
THEMES = {
    "Русская локализация / перевод": [r"russian", r"русск", r"перевод", r"локализ", r"ru lang"],
    "Другие языки / локализация": [r"translat", r"locali[sz]", r"language", r"german", r"spanish",
                                    r"portug", r"french", r"chinese", r"中文", r"汉化", r"polish",
                                    r"turkish", r"ukrain", r"украин"],
    "Кооператив / мультиплеер": [r"co-?op", r"multiplayer", r"\bonline\b", r"with friends",
                                 r"кооп", r"мультиплеер", r"с друз", r"по сети", r"联机", r"多人"],
    "Больше контента (персонажи, события, ситуации)": [
        r"more content", r"more (npc|character|people|event|situation|scenario|story|variet)",
        r"больше контент", r"больше (нпс|персонаж|людей|событ|ситуац|сценар|разнообраз)",
        r"variety", r"разнообраз", r"repetit", r"однообраз", r"повтор"],
    "Новые виды заражений / мутаций / аномалий": [
        r"infect", r"mutat", r"zombi", r"anomal", r"disease", r"symptom", r"virus", r"parasit",
        r"заражен", r"мутац", r"зомби", r"аномал", r"болезн", r"симптом", r"вирус", r"паразит"],
    "Новые инструменты / оборудование для проверки": [
        r"\btools?\b", r"equipment", r"scanner", r"x-?ray", r"gadget", r"device",
        r"инструмент", r"оборудован", r"сканер", r"рентген", r"прибор", r"устройств"],
    "Улучшения базы / прокачка / апгрейды": [
        r"upgrade", r"base build", r"\bbase\b", r"skill tree", r"progression", r"unlock",
        r"улучшен", r"апгрейд", r"прокачк", r"\bбаз[аыуе]\b", r"развити", r"прогресс"],
    "Оружие / оборона / бой": [r"weapon", r"\bgun", r"defen[cs]e", r"combat", r"shoot", r"turret",
                                r"оружи", r"оборон", r"стрел", r"турел", r"бо[йя]"],
    "Сюжет / концовки / лор": [r"\bstory\b", r"plot", r"ending", r"\blore\b", r"narrative", r"campaign",
                                r"сюжет", r"концовк", r"лор", r"истори", r"кампани"],
    "Бесконечный режим / песочница / новые режимы": [
        r"endless", r"sandbox", r"free ?play", r"new game\+", r"ng\+", r"new (game )?modes?", r"\b(survival|hardcore|challenge|creative|story) mode",
        r"бесконечн", r"песочниц", r"нов\w* режим", r"режим (выживан|хардкор|испытан)"],
    "Длительность / эндгейм / реиграбельность": [
        r"too short", r"longer", r"end ?game", r"replay", r"after (the )?ending",
        r"коротк", r"длинн", r"эндгейм", r"реиграб", r"после (прохождения|концовки)"],
    "Сложность / баланс / экономика": [
        r"difficult", r"too (easy|hard)", r"balance", r"econom", r"money", r"resources",
        r"сложност", r"легко", r"баланс", r"экономик", r"деньг", r"ресурс"],
    "Сохранения": [r"\bsav(e|es|ing)\b", r"autosave", r"checkpoint", r"сохранен", r"автосохр"],
    "Мастерская / моды": [r"workshop", r"\bmods?\b", r"modding", r"мастерск", r"\bмод(ы|ов)?\b"],
    "Управление / геймпад / Steam Deck / бинды": [
        r"controller", r"gamepad", r"steam ?deck", r"keybind", r"rebind", r"key ?binding",
        r"геймпад", r"контроллер", r"управлен", r"переназнач"],
    "Настройки графики / FOV / оптимизация": [
        r"\bfov\b", r"graphics settings", r"resolution", r"optimi[sz]", r"\bfps\b", r"performance",
        r"lag", r"оптимиз", r"фпс", r"графи", r"разрешени", r"лаг"],
    "Интерфейс / удобство (QoL)": [r"\bui\b", r"interface", r"quality of life", r"\bqol\b",
                                   r"tutorial", r"hint", r"интерфейс", r"удобств", r"обучени",
                                   r"подсказ"],
    "Исправление багов": [r"\bbugs?\b", r"crash", r"glitch", r"\bfix", r"баг", r"вылет", r"глюк",
                          r"исправ", r"почин"],
    "Достижения": [r"achievement", r"достижени", r"ачивк"],
    "Озвучка / звук / музыка": [r"voice", r"sound", r"music", r"audio", r"озвуч", r"звук", r"музык"],
    "Питомцы / собака / животные": [r"\bdog\b", r"\bpet\b", r"\bcat\b", r"animal", r"собак", r"пит[оа]м",
                                     r"кошк", r"животн"],
}
THEME_RE = {t: re.compile("|".join(p), re.I) for t, p in THEMES.items()}

SENT_SPLIT = re.compile(r"(?<=[.!?。！？\n])\s+|\n+")
TAGS = re.compile(r"\[/?[^\]]+\]")  # Steam BBCode


def tier(share):
    if share >= 0.08: return "S"
    if share >= 0.04: return "A"
    if share >= 0.02: return "B"
    if share >= 0.01: return "C"
    return "D"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(here, "data"))
    ap.add_argument("--since", help="only reviews with created_utc > this, e.g. '2026-11-01 00:00:00'")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args()

    with open(os.path.join(args.data_dir, "reviews.csv"), encoding="utf-8-sig", newline="") as f:
        reviews = list(csv.DictReader(f))
    if args.since:
        reviews = [r for r in reviews if r["created_utc"] > args.since]

    theme_reviews = defaultdict(set)
    theme_votes = Counter()
    theme_pos = Counter()
    examples = defaultdict(list)
    uncategorized = []
    sent_rows = []
    requesting = set()

    for r in reviews:
        text = TAGS.sub(" ", r["review"] or "")
        rid = r["recommendationid"]
        hit_themes = set()
        for s in SENT_SPLIT.split(text):
            s = s.strip()
            if len(s) < 6 or not MARKER_RE.search(s):
                continue
            themes = [t for t, rx in THEME_RE.items() if rx.search(s)]
            requesting.add(rid)
            sent_rows.append({"recommendationid": rid, "created_utc": r["created_utc"],
                              "language": r["language"], "voted_up": r["voted_up"],
                              "themes": "; ".join(themes), "sentence": s})
            if not themes:
                uncategorized.append((int(r.get("votes_up") or 0), s, rid))
            for t in themes:
                if t not in hit_themes and len(examples[t]) < 50:
                    examples[t].append((int(r.get("votes_up") or 0), s))
                hit_themes.add(t)
        for t in hit_themes:
            theme_reviews[t].add(rid)
            theme_votes[t] += int(r.get("votes_up") or 0)
            if str(r["voted_up"]).lower() == "true":
                theme_pos[t] += 1

    total = len(reviews) or 1
    ranked = sorted(theme_reviews, key=lambda t: (len(theme_reviews[t]), theme_votes[t]), reverse=True)

    with open(os.path.join(args.data_dir, "requests_sentences.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["recommendationid", "created_utc", "language", "voted_up", "themes", "sentence"])
        w.writeheader()
        w.writerows(sent_rows)

    with open(os.path.join(args.data_dir, "requests_tierlist.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tier", "theme", "reviews_mentioning", "share_of_all_reviews", "helpful_votes_sum", "of_them_positive"])
        for t in ranked:
            n = len(theme_reviews[t])
            w.writerow([tier(n / total), t, n, f"{n / total:.1%}", theme_votes[t], theme_pos[t]])

    lines = ["# Тир-лист: что игроки просят добавить", "",
             f"Проанализировано отзывов: **{len(reviews)}**" + (f" (созданных после {args.since})" if args.since else ""),
             f"Отзывов с просьбами/пожеланиями: **{len(requesting)}**", "",
             "Тир = доля всех отзывов, где тема упомянута как просьба: "
             "S ≥ 8%, A ≥ 4%, B ≥ 2%, C ≥ 1%, D < 1%.", ""]
    for tr in "SABCD":
        items = [t for t in ranked if tier(len(theme_reviews[t]) / total) == tr]
        if not items:
            continue
        lines += [f"## Tier {tr}", ""]
        for t in items:
            n = len(theme_reviews[t])
            lines.append(f"### {t} — {n} отзывов ({n / total:.1%}), 👍 полезно: {theme_votes[t]}")
            for _, s in sorted(examples[t], reverse=True)[: args.examples]:
                lines.append(f"> {s[:300]}")
                lines.append("")
        lines.append("")
    lines += ["## Uncategorized (просьбы без темы — проверить вручную, топ-40 по 👍)", ""]
    for v, s, rid in sorted(uncategorized, reverse=True)[:40]:
        lines.append(f"- ({v}👍, #{rid}) {s[:250]}")
    open(os.path.join(args.data_dir, "requests_tierlist.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:60]))


if __name__ == "__main__":
    main()

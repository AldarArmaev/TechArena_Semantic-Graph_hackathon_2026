#!/usr/bin/env python3
"""ПРОВЕРКА GOLDEN-ПРИМЕРА. Этот скрипт — тот же алгоритм подсчёта балла, что на платформе организаторов. Он здесь,
чтобы проверить свой ответ на открытом сценарии (gold/S0.json) и понять методологию оценки; баллы за скрытые сценарии
считает платформа. Для скрытых сценариев эталона здесь нет — скрипт проверит только формат (существование id,
отношения, виды вершин): `evaluate_golden.py gold/S0.json my-S3.json` или `--check skeleton/trainticket.json.gz my-S3.json`.

Оценка сдач трека «подграф исполнения сценария» (Train Ticket). Один файл, стандартная библиотека, Python 3.8+.

    python3 evaluate.py gold.json submission.json          # одна сдача (все сценарии): текст для участника; код выхода 0 = оценена
    python3 evaluate.py gold.json submission.json --json   # то же машинно-читаемо (структура отчёта — см. evaluate())
    python3 evaluate.py gold.json submission.json --bonus 0.10   # + итог с надбавкой (решения на Rust, после ревью кода)
    python3 evaluate.py gold.json --selftest               # самопроверка скрипта на сдачах, построенных из эталона
    python3 evaluate.py --check <gold|skeleton> sub.json   # только формат (без эталонов)

gold.json    {"project": "trainticket", "scenarios_all": ["S0", ..., "S7"],
              "ids": {"methods": [все id методов скелета], "tables": [все id таблиц]},
              "scenarios": {"S0": {"methods": [...], "calls": [[a, b], ...], "remote": [[a, b], ...], "data": [[a, t], ...]}, ...}}
submission   {"project": "trainticket",
              "scenarios": {"S0": {"edges": [{"source": ..., "relation": ..., "target": ...}, ...]}, "S1": {"edges": [...]}, ...}}

Один файл — ответ на все сценарии. Четыре отношения: executes (scenario:<id> -> method:...), calls (method -> method),
remote (method -> method другого сервиса), data (method -> res:table:...). Балл сценария = среднее IoU по четырём
множествам, IoU = |P∩G| / |P∪G| (пусто с обеих сторон — 1, пусто только с одной — 0). Итог = среднее по сценариям
эталона; сценарий, которого нет в файле, — 0.
Коды выхода: 0 — сдача оценена; 1 — сдача не принята (ошибка формата файла, см. errors); 3 — формат проверен, но ни
для одного сценария файла в gold.json нет эталона (так участник проверяет скрытые сценарии локально); 2 — неверный вызов."""
import sys, json, gzip, argparse, collections

RELATIONS = ("executes", "calls", "remote", "data")
SET_OF = {"executes": "methods", "calls": "calls", "remote": "remote", "data": "data"}
HINT_RELATION = {"remote_call": "remote", "message_delivery": "remote", "data_access": "data", "runtime_dispatch": "calls",
                 "call": "calls", "invokes": "calls", "http": "remote", "sql": "data", "execute": "executes", "executed": "executes"}
FORMAT = '{"project": "trainticket", "scenarios": {"S1": {"edges": [...]}, "S2": {"edges": [...]}, ...}}'


def load(path):
    """JSON (при .gz — сжатый)."""
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else open(path, encoding="utf-8")) as fh:
        return json.load(fh)


def ids_of(obj):
    """Множества допустимых id методов и таблиц: из gold.json (ключ ids) или из скелета (nodes). Нет ни того ни другого — None."""
    if isinstance(obj.get("ids"), dict):
        return set(obj["ids"].get("methods", [])), set(obj["ids"].get("tables", []))
    if isinstance(obj.get("nodes"), dict):
        n = obj["nodes"]
        return {i for i, v in n.items() if v.get("kind") == "method"}, {i for i, v in n.items() if v.get("kind") == "data_resource"}
    return None


def iou(p, g):
    if not p and not g:
        return 1.0
    return len(p & g) / len(p | g)


def with_bonus(score, bonus):
    """Итог с надбавкой (например, 0.10 за решение на Rust, подтверждённое ревью кода): score × (1 + bonus), не выше 1."""
    return min(1.0, score * (1 + bonus))


def _module(mid):
    parts = mid.split(":", 2)
    return parts[1] if len(parts) >= 3 else None


def _suggest(mid, by_head, by_short):
    """Подсказка для неизвестного id метода: та же сигнатура без типов параметров / тот же класс#метод в другом модуле."""
    head = mid.split("(", 1)[0]
    if head in by_head:
        return f"похожая вершина скелета: {by_head[head][0]}"
    short = head.split(":", 2)[2] if head.count(":") >= 2 else None
    if short in by_short:
        return f"такой метод есть в другом модуле: {by_short[short][0]}"
    return "нет такой вершины в скелете (id копируйте из nodes целиком)"


def _scenario(sid, edges, g, ids, index):
    """Отчёт по одному сценарию: status scored / format_only; sets; rejected с причинами и примерами; warnings."""
    methods, tables = ids if ids else (None, None); by_head, by_short = index
    R = {"status": "format_only", "score": None, "sets": None, "f1": None, "accepted": 0, "duplicates": 0, "rejected": {}, "rejected_examples": {}, "warnings": []}
    rej = collections.Counter(); ex = collections.defaultdict(list)

    def reject(reason, text):
        rej[reason] += 1
        if len(ex[reason]) < 3 and text not in ex[reason]:
            ex[reason].append(text)

    sets = {r: set() for r in RELATIONS}; seen = set(); dups = 0
    for i, e in enumerate(edges):
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) and e.get(k) for k in ("source", "relation", "target")):
            reject("формат ребра", f"ребро #{i}: нужны строковые поля source, relation, target"); continue
        rel, s, t = e["relation"], e["source"], e["target"]
        if rel not in RELATIONS:
            hint = HINT_RELATION.get(rel.strip().lower())
            reject("relation", f"«{rel}»" + (f" — используйте «{hint}»" if hint else " — допустимы только executes, calls, remote, data")); continue
        if rel == "executes":
            if s != f"scenario:{sid}":
                if s.startswith("scenario:"):
                    reject("чужой сценарий", f"executes от {s} в блоке сценария {sid}")
                else:
                    reject("executes: источник", f"executes должен идти от scenario:{sid}, а не от {s[:110]}")
                continue
            if not t.startswith("method:"):
                reject("вид вершины", f"executes ведёт к методу method:..., а не к {t[:110]}" + (" (операторы не оцениваются)" if t.startswith("stmt:") else "")); continue
            if methods is not None and t not in methods:
                reject("неизвестная вершина", f"{t[:120]} — {_suggest(t, by_head, by_short)}"); continue
        else:
            if not s.startswith("method:"):
                reject("вид вершины", f"{rel}: источник — метод method:..., а не {s[:110]}"); continue
            want = "res:" if rel == "data" else "method:"
            if not t.startswith(want):
                reject("вид вершины", f"{rel}: цель — {'таблица res:table:...' if rel == 'data' else 'метод method:...'}, а не {t[:110]}"
                       + (" (для таблиц — relation data)" if rel != "data" and t.startswith("res:") else " (для методов — calls или remote)" if rel == "data" and t.startswith("method:") else "")); continue
            if methods is not None and s not in methods:
                reject("неизвестная вершина", f"{s[:120]} — {_suggest(s, by_head, by_short)}"); continue
            if rel != "data" and methods is not None and t not in methods:
                reject("неизвестная вершина", f"{t[:120]} — {_suggest(t, by_head, by_short)}"); continue
            if rel == "data" and tables is not None and t not in tables:
                reject("неизвестная вершина", f"{t[:120]} — нет такой таблицы в скелете"); continue
        key = (rel, s, t)
        if key in seen:
            dups += 1; continue
        seen.add(key); sets[rel].add(t if rel == "executes" else (s, t))
    R["accepted"], R["duplicates"], R["rejected"], R["rejected_examples"] = len(seen), dups, dict(rej), dict(ex)

    W = R["warnings"]
    if not edges:
        W.append("нет рёбер: 0 по каждому множеству, где эталон не пуст")
    if dups:
        W.append(f"повторяющихся рёбер: {dups} (не штрафуются, учтены один раз)")
    same = [(a, b) for a, b in sets["remote"] if _module(a) == _module(b)]
    if same:
        W.append(f"remote внутри одного сервиса: {len(same)} (например {same[0][0][:90]} -> {same[0][1][:90]}); remote — только между сервисами")
    touched = {a for a, _ in sets["calls"] | sets["remote"] | sets["data"]} | {b for _, b in sets["calls"] | sets["remote"]}
    missing = sorted(touched - sets["executes"])
    if missing:
        W.append(f"методов, которые есть в calls/remote/data, но не заявлены в executes: {len(missing)} (например {missing[0][:90]}); исполнившийся метод должен быть и в executes")
    if sets["executes"] and not (sets["calls"] or sets["remote"] or sets["data"]):
        W.append("сданы только executes: calls, remote и data пусты — по каждому из них будет 0, если в эталоне они не пусты")
    if methods and len(methods) >= 100 and len(sets["executes"]) >= 0.6 * len(methods):
        W.append(f"executes содержит {len(sets['executes'])} из {len(methods)} методов скелета: похоже на «сдать всё» — лишние рёбра снижают IoU так же, как пропущенные")

    if g is None:
        return R
    res = {}
    for r in RELATIONS:
        G = set(g.get(SET_OF[r], [])) if r == "executes" else {tuple(x) for x in g.get(SET_OF[r], [])}
        P = sets[r]; tp = len(P & G); j = iou(P, G)
        res[r] = {"iou": j, "f1": 2 * j / (1 + j), "precision": tp / len(P) if P else 0.0, "recall": tp / len(G) if G else 0.0,
                  "pred": len(P), "gold": len(G), "tp": tp}
    R["sets"] = res; R["score"] = sum(v["iou"] for v in res.values()) / len(RELATIONS); R["f1"] = sum(v["f1"] for v in res.values()) / len(RELATIONS)
    R["status"] = "scored"
    return R


def evaluate(gold, sub):
    """Отчёт по сдаче (один файл на все сценарии).
    status: scored (есть балл) / format_only (эталонов для сценариев файла нет, проверен формат) / invalid (не принята).
    Ключи: status, errors, warnings, score (среднее по сценариям эталона, отсутствующие — 0), scored (по каким считан),
    missing (сценарии эталона без ответа), scenarios {id: {status, score, sets {executes|calls|remote|data: {iou, f1,
    precision, recall, pred, gold, tp}}, f1, accepted, duplicates, rejected, rejected_examples, warnings}}."""
    R = {"status": "invalid", "errors": [], "warnings": [], "score": None, "scored": [], "missing": [], "scenarios": {}}
    if not isinstance(sub, dict):
        R["errors"].append("сдача должна быть JSON-объектом " + FORMAT); return R
    proj = gold.get("project")
    if proj and sub.get("project") != proj:
        R["errors"].append(f"поле project должно быть \"{proj}\" (в сдаче: {sub.get('project')!r})")
    blocks = sub.get("scenarios")
    if not isinstance(blocks, dict):
        if "scenario" in sub and "edges" in sub:
            R["errors"].append(f"файл с одним сценарием (поля scenario/edges) не принимается: сдаётся один файл на все сценарии — {FORMAT}")
        else:
            R["errors"].append("нет словаря scenarios: " + FORMAT)
    known = set(gold.get("scenarios_all") or []) | set(gold.get("scenarios", {}))
    if isinstance(blocks, dict):
        for sid, b in blocks.items():
            if sid not in known:
                R["errors"].append(f"неизвестный сценарий {sid!r}; допустимые: {', '.join(sorted(known))}")
            elif not isinstance(b, dict) or not isinstance(b.get("edges"), list):
                R["errors"].append(f"сценарий {sid}: нужен объект {{\"edges\": [...]}}")
    if R["errors"]:
        return R
    ids = ids_of(gold)
    by_head = collections.defaultdict(list); by_short = collections.defaultdict(list)
    if ids:
        for m in ids[0]:
            h = m.split("(", 1)[0]; by_head[h].append(m)
            if h.count(":") >= 2:
                by_short[h.split(":", 2)[2]].append(m)
    G = gold.get("scenarios", {})
    for sid in sorted(blocks):
        R["scenarios"][sid] = _scenario(sid, blocks[sid]["edges"], G.get(sid), ids, (by_head, by_short))
    R["scored"] = [s for s in G if s in blocks]
    R["missing"] = [s for s in G if s not in blocks]
    if R["missing"]:
        R["warnings"].append(f"нет ответа на {', '.join(R['missing'])} — по этим сценариям 0")
    if not R["scored"]:                                                          # ни одного сценария с эталоном: только формат, балла нет
        R["status"] = "format_only"; R["warnings"] = []; return R
    R["score"] = sum(R["scenarios"][s]["score"] for s in R["scored"]) / len(G)
    R["status"] = "scored"
    return R


def format_report(R, bonus=0.0):
    """Текст для участника (то, что платформа показывает после сабмита)."""
    L = []
    if R["status"] == "invalid":
        L.append("Сдача не принята:")
        L += [f"  - {e}" for e in R["errors"]]
        return "\n".join(L)
    for sid, S in R["scenarios"].items():
        if S["status"] == "scored":
            L.append(f"Сценарий {sid}: балл {S['score']:.3f}")
            for r in RELATIONS:
                v = S["sets"][r]
                L.append(f"  {r:9s} IoU {v['iou']:.3f}   (точность {v['precision']:.2f}, полнота {v['recall']:.2f}; сдано {v['pred']}, в эталоне {v['gold']}, совпало {v['tp']})")
        else:
            L.append(f"Сценарий {sid}: эталона этого сценария в этом файле нет — проверен только формат")
        n_rej = sum(S["rejected"].values())
        L.append(f"  принято рёбер: {S['accepted']}" + (f", повторов {S['duplicates']}" if S["duplicates"] else "") + (f", отклонено {n_rej}:" if n_rej else ", отклонённых нет"))
        for reason, n in sorted(S["rejected"].items(), key=lambda kv: -kv[1]):
            L.append(f"    - {reason}: {n}")
            L += [f"        {t}" for t in S["rejected_examples"].get(reason, [])]
        if S["warnings"]:
            L.append("  предупреждения (на балл не влияют):")
            L += [f"    - {w}" for w in S["warnings"]]
    for w in R["warnings"]:
        L.append(f"Внимание: {w}")
    if R["status"] == "scored":
        allsc = sorted(R["scored"] + R["missing"])
        L.append(f"ИТОГ — среднее по сценариям эталона ({', '.join(allsc)}): {R['score']:.3f}"
                 + (f"; с надбавкой {bonus:.0%}: {with_bonus(R['score'], bonus):.3f}" if bonus else ""))
    else:
        L.append("Балла нет: ни для одного сценария файла здесь нет эталона — проверен только формат")
    return "\n".join(L)


def selftest(gold):
    """Сдачи, построенные из самого эталона, с известным ответом; печатает OK/FAIL, возвращает число провалов."""
    fails = 0

    def check(name, cond, detail=""):
        nonlocal fails
        print(("  OK   " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
        fails += 0 if cond else 1

    proj = gold.get("project", "trainticket"); G = gold["scenarios"]; n = len(G)

    def block(sid, g, rel_names=("calls", "remote", "data"), keep=lambda i: True):
        edges = [{"source": f"scenario:{sid}", "relation": "executes", "target": m} for i, m in enumerate(g["methods"]) if keep(i)]
        for rel, key in zip(rel_names, ("calls", "remote", "data")):
            edges += [{"source": a, "relation": rel, "target": b} for i, (a, b) in enumerate(g[key]) if keep(i)]
        return {"edges": edges}

    full = {"project": proj, "scenarios": {sid: block(sid, g) for sid, g in G.items()}}
    R = evaluate(gold, full)
    check(f"эталон всех {n} сценариев как сдача -> 1.000, без отклонений и предупреждений",
          R["status"] == "scored" and abs(R["score"] - 1) < 1e-9 and not R["warnings"] and all(not S["rejected"] and not S["warnings"] for S in R["scenarios"].values()),
          f"score={R['score']}, warnings={R['warnings']}")
    first = next(iter(G))
    part = {"project": proj, "scenarios": {sid: b for sid, b in full["scenarios"].items() if sid != first}}
    R = evaluate(gold, part)
    check(f"без ответа на {first} -> {(n - 1) / n:.3f}, предупреждение про {first}", R["status"] == "scored" and abs(R["score"] - (n - 1) / n) < 1e-9 and R["missing"] == [first] and R["warnings"], f"score={R['score']}, missing={R['missing']}")
    R = evaluate(gold, {"project": proj, "scenarios": {}})
    check("пустой словарь scenarios -> format_only, без балла", R["status"] == "format_only" and R["score"] is None, f"status={R['status']}, score={R['score']}")
    g = G[first]
    R = evaluate(gold, {"project": proj, "scenarios": {first: {"edges": []}}})
    exp = sum(1.0 if not g[SET_OF[r]] else 0.0 for r in RELATIONS) / 4 / n
    check(f"{first} без рёбер, остальных нет -> {exp:.3f}", R["status"] == "scored" and abs(R["score"] - exp) < 1e-9, f"score={R['score']}")
    R = evaluate(gold, {"project": proj, "scenarios": {first: block(first, g, keep=lambda i: i % 2 == 0)}})
    exp = sum((len(g[SET_OF[r]][::2]) / len(g[SET_OF[r]])) if g[SET_OF[r]] else 1.0 for r in RELATIONS) / 4
    check(f"{first}: половина эталона -> балл сценария {exp:.3f} (P=1, R=1/2)", R["status"] == "scored" and abs(R["scenarios"][first]["score"] - exp) < 1e-9, f"score={R['scenarios'][first]['score']}")
    R = evaluate(gold, {"project": proj, "scenarios": {first: block(first, g, rel_names=("calls", "remote_call", "data_access"))}})
    k = len(g["remote"]) + len(g["data"]); S = R["scenarios"][first]
    check(f"{first}: имена рёбер скелета (remote_call/data_access) -> отклонено {k} с подсказкой", S["rejected"].get("relation") == k and "remote" in " ".join(S["rejected_examples"].get("relation", [])), f"rejected={S['rejected']}")
    if g["remote"]:
        S = evaluate(gold, {"project": proj, "scenarios": {first: block(first, g, rel_names=("calls", "calls", "data"))}})["scenarios"][first]
        check(f"{first}: remote подписаны как calls -> не отклоняются, remote IoU 0, calls IoU {len(g['calls'])}/{len(g['calls']) + len(g['remote'])}",
              not S["rejected"] and S["sets"]["remote"]["iou"] == 0.0 and abs(S["sets"]["calls"]["iou"] - len(g["calls"]) / (len(g["calls"]) + len(g["remote"]))) < 1e-9)
    b = block(first, g); b["edges"][0]["target"] = b["edges"][0]["target"].replace("(", "(java.lang.Object,", 1)
    S = evaluate(gold, {"project": proj, "scenarios": {first: b}})["scenarios"][first]
    check(f"{first}: неизвестный id метода -> отклонён с подсказкой", ids_of(gold) is None or S["rejected"].get("неизвестная вершина") == 1, f"rejected={S['rejected']}")
    check("чужой project -> сдача не принята", evaluate(gold, {"project": "other", "scenarios": {}})["status"] == "invalid")
    check("неизвестный сценарий -> сдача не принята", evaluate(gold, {"project": proj, "scenarios": {"S99": {"edges": []}}})["status"] == "invalid")
    check("старый формат (scenario/edges) -> сдача не принята с объяснением", "один файл" in " ".join(evaluate(gold, {"project": proj, "scenario": first, "edges": []})["errors"]))
    check("блок без edges -> сдача не принята", evaluate(gold, {"project": proj, "scenarios": {first: {}}})["status"] == "invalid")
    others = [s for s in gold.get("scenarios_all", []) if s not in G]
    if others:
        R = evaluate(gold, {"project": proj, "scenarios": {others[0]: {"edges": []}}})
        check(f"только {others[0]} (эталона здесь нет) -> format_only, без балла", R["status"] == "format_only" and R["score"] is None)
        R = evaluate(gold, {"project": proj, "scenarios": {**full["scenarios"], others[0]: {"edges": []}}})
        check(f"{others[0]} вместе с остальными -> не в зачёт, итог 1.000", R["status"] == "scored" and abs(R["score"] - 1) < 1e-9 and R["scenarios"][others[0]]["status"] == "format_only")
    check("надбавка: 0.65 -> 0.715, потолок 1.0", abs(with_bonus(0.65, 0.10) - 0.715) < 1e-9 and with_bonus(0.95, 0.10) == 1.0)
    print("самопроверка: " + ("OK" if not fails else f"провалов {fails}"))
    return fails


# --- совместимость: сдача одного сценария {"scenario", "edges"} (бейзлайны, тесты) -> тот же расчёт
def _wrap(sub, project):
    if isinstance(sub, dict) and "scenarios" not in sub and "scenario" in sub:
        return {"project": sub.get("project", project), "scenarios": {sub["scenario"]: {"edges": sub.get("edges", [])}}}
    return sub


def score(gold, sub, only=None):
    """{"scenarios": {id: {methods|calls|remote|data: {...}, score, f1}}, "rejected": {...}, "score"}; невалидная сдача -> пусто, 0."""
    R = evaluate(gold, _wrap(sub, gold.get("project")))
    if R["status"] == "invalid":
        return {"scenarios": {}, "rejected": {"invalid": 1}, "score": 0.0, "errors": R["errors"]}
    out = {"scenarios": {}, "rejected": collections.Counter(), "score": 0.0}
    for sid, S in R["scenarios"].items():
        out["rejected"].update(S["rejected"])
        if S["status"] == "scored" and (only is None or sid == only):
            out["scenarios"][sid] = {**{SET_OF[r]: v for r, v in S["sets"].items()}, "score": S["score"], "f1": S["f1"]}
    out["rejected"] = dict(out["rejected"])
    out["score"] = sum(v["score"] for v in out["scenarios"].values()) / len(out["scenarios"]) if out["scenarios"] else 0.0
    return out


def check(skeleton_or_gold, sub):
    """(принято рёбер, {причина: n}) — только формат, без эталонов."""
    g = {"project": skeleton_or_gold.get("project"), "scenarios_all": list(skeleton_or_gold.get("scenarios_all") or []), "scenarios": {}}
    sub = _wrap(sub, g["project"])
    if isinstance(sub, dict) and isinstance(sub.get("scenarios"), dict):
        g["scenarios_all"] += list(sub["scenarios"])
    ids = ids_of(skeleton_or_gold)
    if ids:
        g["ids"] = {"methods": sorted(ids[0]), "tables": sorted(ids[1])}
    R = evaluate(g, sub)
    if R["status"] == "invalid":
        return 0, {"invalid": len(R["errors"])}
    rej = collections.Counter()
    for S in R["scenarios"].values():
        rej.update(S["rejected"])
    return sum(S["accepted"] for S in R["scenarios"].values()), dict(rej)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gold", help="gold.json (при --check — gold.json или skeleton.json[.gz])")
    ap.add_argument("submission", nargs="?", help="сдача участника (JSON, все сценарии)")
    ap.add_argument("--json", action="store_true", help="машинно-читаемый отчёт")
    ap.add_argument("--bonus", type=float, default=0.0, help="надбавка к итогу (0.10 = +10 %%, потолок 1.0) — решения на Rust после ревью кода")
    ap.add_argument("--selftest", action="store_true", help="самопроверка на сдачах из эталона")
    ap.add_argument("--check", action="store_true", help="только формат сдачи (без эталонов)")
    a = ap.parse_args()
    try:
        gold = load(a.gold)
    except (OSError, ValueError) as e:
        print(f"не читается {a.gold}: {e}", file=sys.stderr); sys.exit(2)
    if a.selftest:
        sys.exit(1 if selftest(gold) else 0)
    if not a.submission:
        ap.error("укажите файл сдачи (или --selftest)")
    try:
        sub = load(a.submission)
    except (OSError, ValueError) as e:
        R = {"status": "invalid", "errors": [f"файл не читается как JSON: {e}"], "warnings": [], "score": None, "scored": [], "missing": [], "scenarios": {}}
    else:
        if a.check:
            ids = ids_of(gold)
            gold = {"project": gold.get("project"), "scenarios_all": list(gold.get("scenarios_all") or []) + (list(sub["scenarios"]) if isinstance(sub, dict) and isinstance(sub.get("scenarios"), dict) else []),
                    "scenarios": {}, **({"ids": {"methods": sorted(ids[0]), "tables": sorted(ids[1])}} if ids else {})}
        R = evaluate(gold, sub)
    if a.bonus and R["status"] == "scored":
        R["score_with_bonus"] = with_bonus(R["score"], a.bonus)
    print(json.dumps(R, ensure_ascii=False, indent=1) if a.json else format_report(R, a.bonus))
    sys.exit({"scored": 0, "invalid": 1, "format_only": 3}[R["status"]])


if __name__ == "__main__":
    main()

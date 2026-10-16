#!/usr/bin/env python3
"""Бейзлайн «статическое замыкание»: от точек входа (обработчиков шагов) — все методы, достижимые внутри deployment unit'а по
рёбрам calls (+ CHA по overrides, + runtime_dispatch из проводки), через границу — по remote_call/message_delivery проводки;
таблицы — по data_access проводки; операторы — все операторы найденных методов. Ветвления не учитываются: это may-подграф.

Точки входа: по путям запросов, если они есть во входе (`--entries requests`: шаблоны @RequestMapping класса + @*Mapping
метода); при входе «только текст» — по подписи каждого шага (`steps`: лучший обработчик по BM25 подписи шага среди
аннотаций и имён обработчиков) или по тексту сценария целиком (`nl`, top-N). Умное сопоставление — в baseline_nlp.py.

    python3 baseline_closure.py <skeleton.json[.gz]> <scenario.json> -o submission.json [--entries requests|steps|nl] [--nl-top N]"""
import sys, re, math, json, gzip, argparse, collections


def load_json(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")) as fh:
        return json.load(fh)


def path_segs(p):
    return [s for s in re.split(r"[/]+", p.split("?")[0]) if s]


def match(req, tpl):
    """Сегменты запроса против шаблона Spring: {x} — один сегмент, ** — хвост."""
    if not tpl:
        return not req
    t = tpl[0]
    if t == "**":
        return True
    if not req:
        return False
    if t.startswith("{") or t == "*" or t == req[0]:
        return match(req[1:], tpl[1:])
    return False


def ann_paths(anns):
    out = []
    for a in anns or []:
        if a["name"].endswith("Mapping"):
            vals = re.findall(r'"([^"]*)"', a.get("args") or "")
            out.append((a["name"].replace("Mapping", "").upper(), vals or [""]))
    return out


def toks(t):
    return [w.lower() for w in re.findall(r"[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])", re.sub(r"([a-z])([A-Z])", r"\1 \2", t or "")) if len(w) > 2]


class Index:
    """Скелет: обработчики с шаблонами путей, рёбра для обхода, unit'ы модулей."""

    def __init__(self, sk):
        self.sk = sk; N = self.N = sk["nodes"]
        self.handlers = []
        for nid, n in N.items():
            if n["kind"] != "method":
                continue
            mp = ann_paths(n.get("annotations"))
            if not mp:
                continue
            cls_paths = [v for verb, vals in ann_paths(N[n["owner"]].get("annotations")) for v in vals] or [""]
            for verb, vals in mp:
                for v in vals:
                    for cp in cls_paths:
                        self.handlers.append((nid, verb if verb != "REQUEST" else None, path_segs(cp + "/" + v)))
        self.cs_method = {nid: n["method"] for nid, n in N.items() if n["kind"] == "call_site"}
        self.calls = collections.defaultdict(set); self.over = collections.defaultdict(set)
        self.wiring = collections.defaultdict(lambda: collections.defaultdict(set))
        for e in sk["edges"]:
            if e["type"] == "calls":
                self.calls[self.cs_method[e["source"]]].add(e["target"])
            elif e["type"] == "overrides":
                self.over[e["target"]].add(e["source"])
            elif e["type"] in ("remote_call", "message_delivery", "runtime_dispatch", "data_access"):
                self.wiring[e["type"]][self.cs_method.get(e["source"], e["source"])].add(e["target"])
        self.module = {nid: n["module"] for nid, n in N.items() if n["kind"] == "method"}
        self.units = {n["name"]: set(n.get("deployment_units") or [n["name"]]) for n in N.values() if n["kind"] == "module"}
        self.size = collections.Counter(u for us in self.units.values() for u in us)
        self.stmts = collections.defaultdict(list)
        for e in sk["edges"]:
            if e["type"] == "contains" and e["target"].startswith("stmt:"):
                self.stmts[e["source"]].append(e["target"])

    def unit_of(self, m):
        """Самый узкий unit модуля метода: сервис, а не сборка «всё в одном» (apollo-assembly)."""
        mod = self.module[m]
        return min(self.units.get(mod, {mod}), key=lambda u: (self.size[u], u))

    def cha(self, m):
        out, st = set(), [m]
        while st:
            x = st.pop()
            for o in self.over.get(x, ()):
                if o not in out:
                    out.add(o); st.append(o)
        return out

    def handlers_for(self, method, path, host=None):
        """Обработчики, чей шаблон совпадает с путём запроса (host — сервис, если проект без gateway)."""
        segs = path_segs(path); out = set()
        for nid, verb, tpl in self.handlers:
            if host and host not in self.N[nid]["module"]:
                continue
            if (verb is None or method is None or verb == method) and match(segs, tpl):
                out.add(nid)
        return out

    def handler_docs(self):
        """Текст обработчика для лексического поиска: шаблон пути (x3), имя (x2), модуль."""
        docs = {}
        for nid, verb, tpl in self.handlers:
            docs.setdefault(nid, []).extend(toks(" ".join(tpl)) * 3 + toks(self.N[nid]["name"]) * 2 + toks(self.N[nid]["module"]))
        return docs

    def closure(self, entries):
        """Обход в контексте unit'а: вызовы — только в модули того же unit'а (общие модули вроде ts-common входят в несколько);
        remote — переход в unit обработчика. -> (методы, calls, remote, data)."""
        seen, edges, remote, data = set(), set(), set(), set()
        st = [(m, self.unit_of(m)) for m in entries]
        while st:
            m, u = st.pop()
            if (m, u) in seen:
                continue
            seen.add((m, u))
            for t in self.calls.get(m, ()):
                for x in ({t} | self.cha(t) | self.wiring["runtime_dispatch"].get(m, set())):
                    if not self.N[x].get("abstract") and u in self.units.get(self.module.get(x), ()):
                        edges.add((m, x)); st.append((x, u))
            for t in self.wiring["remote_call"].get(m, set()) | self.wiring["message_delivery"].get(m, set()):
                remote.add((m, t)); st.append((t, self.unit_of(t)))
            for t in self.wiring["data_access"].get(m, set()):
                data.add((m, t))
        return {m for m, _ in seen}, edges, remote, data

    def submission(self, scenario, methods, edges, remote, data, method="closure", **extra):
        """Блок одного сценария для сдачи: {"edges": [...]} (+ служебные поля method/…, скорер их не читает)."""
        out = {"method": method, **extra, "edges": []}
        out["edges"] += [{"source": f"scenario:{scenario}", "relation": "executes", "target": m} for m in sorted(methods)]
        out["edges"] += [{"source": s, "relation": "calls", "target": t} for s, t in sorted(edges)]
        out["edges"] += [{"source": s, "relation": "remote", "target": t} for s, t in sorted(remote)]
        out["edges"] += [{"source": s, "relation": "data", "target": t} for s, t in sorted(data)]
        out["edges"] += [{"source": f"scenario:{scenario}", "relation": "executes", "target": st} for m in sorted(methods) for st in self.stmts.get(m, [])]
        return out


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.ids = list(docs); D = [docs[i] for i in self.ids]
        self.tf = [collections.Counter(d) for d in D]; self.len = [len(d) for d in D]
        self.avg = sum(self.len) / max(1, len(D)); self.k1, self.b = k1, b
        df = collections.Counter(t for c in self.tf for t in c)
        self.idf = {t: math.log(1 + (len(D) - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def rank(self, q):
        """-> [(балл, id)] по убыванию, только ненулевые."""
        q = collections.Counter(q); sc = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t, qn in q.items():
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)) * min(qn, 3)
            if s > 0:
                sc.append((s, self.ids[i]))
        return sorted(sc, reverse=True)


def entries_of(ix, S, mode, nl_top=14):
    entries = set()
    if mode == "requests":
        for r in S["requests"]:
            entries |= ix.handlers_for(r["method"], r["path"], r.get("host"))
    else:
        bm = BM25(ix.handler_docs())
        if mode == "nl":
            entries = {nid for _, nid in bm.rank(toks(S["title"]) * 2 + toks(S["description"]))[:nl_top]}
        else:
            for st in S.get("steps") or S.get("requests") or []:
                entries |= {nid for _, nid in bm.rank(toks(st.get("action") or st.get("step") or ""))[:1]}
    return entries


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("skeleton"); ap.add_argument("scenario", nargs="+", help="файлы сценариев scenarios/*.json — один ответ на все"); ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--entries", default=None, choices=["requests", "nl", "steps"], help="точки входа: по путям запросов (requests; если они есть во входе — по умолчанию), по тексту сценария целиком (nl, top-N) или по подписи каждого шага (steps, top-1 на шаг; по умолчанию для входа «только текст»)")
    ap.add_argument("--nl-top", type=int, default=14)
    a = ap.parse_args()
    sk = load_json(a.skeleton); ix = Index(sk)
    out = {"project": sk["project"], "scenarios": {}}
    for path in a.scenario:
        S = load_json(path); sid = S["id"]
        mode = a.entries or ("requests" if S.get("requests") else "steps")
        entries = entries_of(ix, S, mode, a.nl_top)
        methods, edges, remote, data = ix.closure(entries)
        out["scenarios"][sid] = ix.submission(sid, methods, edges, remote, data, entries=mode)
        print(f"{sid}: входов {len(entries)}, методов {len(methods)}, calls {len(edges)}, remote {len(remote)}, data {len(data)}")
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"сценариев {len(out['scenarios'])} -> {a.out}")


if __name__ == "__main__":
    main()

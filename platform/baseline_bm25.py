#!/usr/bin/env python3
"""Наивный NLP-бейзлайн: BM25 между текстом сценария (название, описание, шаги; при входе с запросами — пути и тела) и текстом методов
(класс, имя, тело из исходников). Топ-k методов объявляются исполненными; рёбра calls/remote/data — те рёбра скелета,
оба конца которых (для data — источник) вошли в предсказанное множество. k подбирается на сценарии с эталоном (dev).

    python3 baseline_bm25.py <skeleton.json[.gz]> <корень исходников> <scenario.json> -o submission.json [--k 150] [--gold gold.json]"""
import os, re, json, gzip, math, argparse, collections


def load_json(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")) as fh:
        return json.load(fh)

CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOP = set("the a an and or of to in for on with is are be this that it as at by from not if then else when which who".split())


def tokens(text):
    out = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text or ""):
        for p in (CAMEL.findall(w) if "_" not in w else w.split("_")):
            p = p.lower()
            if len(p) >= 3 and p not in STOP:
                out.append(p)
    return out


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.k1, self.b, self.N = k1, b, len(docs)
        self.tf = [collections.Counter(d) for d in docs]; self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, self.N)
        df = collections.Counter(t for c in self.tf for t in c)
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        self.post = collections.defaultdict(list)
        for i, c in enumerate(self.tf):
            for t, n in c.items():
                self.post[t].append((i, n))

    def query(self, q):
        sc = collections.defaultdict(float)
        for t, qn in collections.Counter(q).items():
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, n in self.post[t]:
                sc[i] += idf * n * (self.k1 + 1) / (n + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)) * min(qn, 3)
        return sorted(sc.items(), key=lambda kv: -kv[1])


def body(root, n, cache):
    f = n["file"]
    if f not in cache:
        try:
            cache[f] = open(os.path.join(root, f), encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            cache[f] = []
    return "\n".join(cache[f][max(0, n["line"] - 1): n["end"]])


def predict(sk, root, sc, k, use_requests=True):
    """sc — объект сценария (title, description, steps[, requests])."""
    N = sk["nodes"]; cache = {}
    mids = [nid for nid, n in N.items() if n["kind"] == "method"]
    docs = []
    for m in mids:
        n = N[m]
        docs.append(tokens(N[n["owner"]]["fqn"].split(".")[-1]) * 2 + tokens(n["name"]) * 3 + tokens(n["module"]) + tokens(body(root, n, cache))[:400])
    bm = BM25(docs)
    q = tokens(sc["title"]) * 3 + tokens(sc["description"]) * 2
    for st in sc.get("steps") or []:
        q += tokens(st.get("action", "")) * 2
    for r in (sc.get("requests") or []) if use_requests else []:
        q += tokens(r["path"].replace("/", " ")) * 3 + tokens(json.dumps(r.get("body"), ensure_ascii=False) if r.get("body") is not None else "")
    ranked = bm.query(q)
    return {mids[i] for i, _ in ranked[:k]}


def edges_for(sk, S, methods):
    N = sk["nodes"]; cs_method = {nid: n["method"] for nid, n in N.items() if n["kind"] == "call_site"}
    out = [{"source": f"scenario:{S}", "relation": "executes", "target": m} for m in sorted(methods)]
    seen = set()
    for e in sk["edges"]:
        src = cs_method.get(e["source"], e["source"])
        if src not in methods:
            continue
        if e["type"] == "calls" and e["target"] in methods:
            key = ("calls", src, e["target"])
        elif e["type"] in ("remote_call", "message_delivery") and e["target"] in methods:
            key = ("remote", src, e["target"])
        elif e["type"] == "data_access":
            key = ("data", src, e["target"])
        else:
            continue
        if key not in seen:
            seen.add(key); out.append({"source": key[1], "relation": key[0], "target": key[2]})
    for e in sk["edges"]:
        if e["type"] == "contains" and e["target"].startswith("stmt:") and e["source"] in methods:
            out.append({"source": f"scenario:{S}", "relation": "executes", "target": e["target"]})
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("skeleton"); ap.add_argument("root"); ap.add_argument("scenario", nargs="+", help="файлы сценариев scenarios/*.json — один ответ на все"); ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--k", type=int, default=0, help="число методов; 0 — подобрать по --gold (эталон этого же сценария) или взять 150"); ap.add_argument("--gold")
    ap.add_argument("--no-requests", action="store_true", help="только название и описание сценария (без путей и тел запросов)")
    a = ap.parse_args()
    sk = load_json(a.skeleton); scenarios = [load_json(p) for p in a.scenario]
    sc = scenarios[0]; sid = sc["id"]
    k = a.k
    if not k and a.gold:
        import importlib.util, sys, glob
        for cand in ("evaluate.py", "evaluate_golden.py"):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cand)
            if os.path.exists(path):
                break
        spec = importlib.util.spec_from_file_location("v6_eval", path); E = importlib.util.module_from_spec(spec); spec.loader.exec_module(E)
        gold = json.load(open(a.gold, encoding="utf-8")); best = (-1, 150)
        for kk in (50, 100, 150, 200, 300, 400):
            sub = {"scenario": sid, "edges": edges_for(sk, sid, predict(sk, a.root, sc, kk, not a.no_requests))}
            s = E.score(gold, sub, sid)["score"]
            if s > best[0]:
                best = (s, kk)
        k = best[1]; print(f"k={k} по {sid} (score {best[0]:.3f})")
    k = k or 150
    out = {"project": sk["project"], "scenarios": {}}
    for sc in scenarios:
        sid = sc["id"]; methods = predict(sk, a.root, sc, k, not a.no_requests)
        out["scenarios"][sid] = {"method": "bm25", "k": k, "edges": edges_for(sk, sid, methods)}
        print(f"{sid}: методов {len(methods)}, рёбер {len(out['scenarios'][sid]['edges'])}")
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"сценариев {len(out['scenarios'])} -> {a.out}")


if __name__ == "__main__":
    main()

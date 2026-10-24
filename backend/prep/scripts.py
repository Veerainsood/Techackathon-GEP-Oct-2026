"""Scripts that run inside the data-prep sandbox (pandas). They are uploaded with each job, so the sandbox needs only Python
with pandas (and openpyxl / pyarrow for Excel and Parquet).

PROFILE_SCRIPT  input_path profile.json        -> per-column quality profile and duplicate analysis
APPLY_SCRIPT    input_path plan.json out report.json -> applies the approved operations, writes the cleaned file in the
                                                    input's format and a per-operation report
"""

COMMON = r'''
import json, sys
import pandas as pd

def load(p):
    pl = p.lower()
    if pl.endswith((".xlsx", ".xls")):
        return pd.read_excel(p, dtype=object)
    if pl.endswith(".parquet"):
        return pd.read_parquet(p)
    if pl.endswith(".jsonl"):
        return pd.read_json(p, lines=True, dtype=False)
    if pl.endswith(".json"):
        return pd.read_json(p, dtype=False)
    sep = "\t" if pl.endswith(".tsv") else ","
    return pd.read_csv(p, sep=sep, dtype=str, keep_default_na=False, na_values=[""])

def is_missing(s):
    return s.isna() | s.astype(str).str.strip().isin(["", "nan", "NaN", "None", "null", "NULL", "N/A", "n/a", "NA"])

def text(s):
    return s.where(~s.isna(), None).astype(object).map(lambda v: v if v is None else str(v))
'''

PROFILE_SCRIPT = COMMON + r'''
src, out = sys.argv[1], sys.argv[2]
df = load(src)
cols = []
id_like = []
for c in df.columns:
    s = df[c]
    miss = is_missing(s)
    present = text(s[~miss])
    stripped = present.str.strip()
    n = int((~miss).sum())
    info = {"name": str(c), "dtype": str(s.dtype), "nulls": int(miss.sum()), "null_pct": round(float(miss.mean()) if len(s) else 0.0, 4),
            "unique": int(stripped.nunique()), "sample": [str(v) for v in stripped.drop_duplicates().head(6).tolist()],
            "whitespace_issues": int((present != stripped).sum())}
    num = pd.to_numeric(stripped.str.replace(",", "", regex=False), errors="coerce")
    info["numeric_parse_rate"] = round(float(num.notna().mean()) if n else 0.0, 4)
    if info["numeric_parse_rate"] < 0.5 and n:
        dates = pd.to_datetime(stripped, errors="coerce", format="mixed")
        info["date_parse_rate"] = round(float(dates.notna().mean()), 4)
        # 05/02/2026 is 5 Feb or 2 May depending on convention: such values need a person to choose
        parts = stripped.str.extract(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}$").dropna().astype(int)
        info["ambiguous_dates"] = int(((parts[0] <= 12) & (parts[1] <= 12) & (parts[0] != parts[1])).sum()) if len(parts) else 0
    else:
        info["date_parse_rate"] = 0.0
        info["ambiguous_dates"] = 0
    if info["numeric_parse_rate"] >= 0.95 and num.notna().any():
        v = num.dropna()
        q1, q3 = v.quantile(0.25), v.quantile(0.75)
        iqr = q3 - q1
        info.update(min=float(v.min()), max=float(v.max()), mean=round(float(v.mean()), 4), median=float(v.median()),
                    outliers=int(((v < q1 - 1.5 * iqr) | (v > q3 + 1.5 * iqr)).sum()) if iqr > 0 else 0)
    lowered = stripped.str.lower()
    info["case_variant_groups"] = int((stripped.groupby(lowered).nunique() > 1).sum()) if n else 0
    name = str(c).lower()
    info["id_like"] = bool(name == "id" or name.endswith("_id") or name.endswith("id") and n and info["unique"] == n)
    if info["id_like"]:
        id_like.append(str(c))
        info["duplicate_ids"] = int(stripped.duplicated(keep=False).sum())
    cols.append(info)
exact = df.astype(str).duplicated(keep=False)
non_id = [c for c in df.columns if str(c) not in id_like]
near = df[non_id].astype(str).duplicated(keep=False) if non_id and id_like else exact
groups = df[exact].astype(str).apply(tuple, axis=1).nunique() if exact.any() else 0
profile = {"rows": int(len(df)), "columns": cols,
           "duplicates": {"exact_rows": int(exact.sum()), "exact_groups": int(groups),
                          "exact_row_numbers": [int(i) + 1 for i in df.index[exact][:20]],
                          "same_except_ids_rows": int(near.sum()), "id_columns": id_like}}
json.dump(profile, open(out, "w"), default=str)
print(json.dumps({"rows": profile["rows"], "columns": len(cols)}))
'''

APPLY_SCRIPT = COMMON + r'''
src, plan_path, out_path, report_path = sys.argv[1:5]
df = load(src)
ops = json.load(open(plan_path))
report = []

def changed(before, after):
    b, a = before.astype(object), after.astype(object)
    diff = ~((b == a) | (b.isna() & a.isna()))
    return diff

for op in ops:
    kind, cols, params = op["op"], op.get("columns") or [], op.get("params") or {}
    entry = {"id": op["id"], "op": kind, "columns": cols, "affected_rows": 0, "examples": []}
    try:
        if kind == "drop_exact_duplicates":
            n0 = len(df)
            df = df.drop_duplicates().reset_index(drop=True)
            entry["affected_rows"] = n0 - len(df)
        elif kind == "drop_rows_missing":
            n0 = len(df)
            mask = pd.Series(False, index=df.index)
            for c in cols:
                mask |= is_missing(df[c])
            df = df[~mask].reset_index(drop=True)
            entry["affected_rows"] = n0 - len(df)
        elif kind == "flag_duplicates":
            keys = cols or [c for c in df.columns if not str(c).startswith("_duplicate")]
            parts = [df[c].map(lambda v: "" if pd.isna(v) else str(v)) for c in keys]
            key = parts[0].str.cat(parts[1:], sep="\u241f") if len(parts) > 1 else parts[0]
            dup = key.duplicated(keep=False)
            df["_duplicate_group"] = pd.Series([None] * len(df), dtype=object)
            df.loc[dup, "_duplicate_group"] = key[dup].map({k: i + 1 for i, k in enumerate(key[dup].unique())})
            df["_duplicate_count"] = key.map(key.value_counts()).where(dup, 1)
            entry["affected_rows"] = int(dup.sum())
        else:
            for c in cols:
                before = df[c].copy()
                s = df[c]
                if kind == "trim_whitespace":
                    s = s.map(lambda v: v.strip() if isinstance(v, str) else v)
                elif kind == "standardize_case":
                    f = {"upper": str.upper, "lower": str.lower, "title": str.title}[params.get("case", "upper")]
                    s = s.map(lambda v: f(v) if isinstance(v, str) else v)
                elif kind == "cast_numeric":
                    s = pd.to_numeric(text(s).str.strip().str.replace(",", "", regex=False), errors="coerce")
                elif kind == "parse_dates":
                    d = pd.to_datetime(text(s).str.strip(), errors="coerce", format=params.get("format") or "mixed")
                    s = d.dt.strftime("%Y-%m-%d").where(d.notna(), None)
                elif kind == "normalize_values":
                    mapping = params.get("mapping") or {}
                    s = s.map(lambda v: mapping.get(str(v).strip(), v) if v is not None else v)
                elif kind == "fill_missing":
                    miss = is_missing(s)
                    method = params.get("method", "median")
                    if method in ("median", "mean"):
                        num = pd.to_numeric(text(s).str.strip(), errors="coerce")
                        fill = getattr(num[~miss], method)()
                        s = num.where(~miss, fill)
                    elif method == "mode":
                        m = s[~miss].mode()
                        s = s.where(~miss, m.iloc[0] if len(m) else None)
                    elif method == "constant":
                        s = s.where(~miss, params.get("value"))
                    elif method == "ffill":
                        s = s.where(~miss, None).ffill()
                    elif method == "product":  # e.g. a missing line total = quantity x unit price
                        a, b = (pd.to_numeric(text(df[x]).str.replace(",", "").str.strip(), errors="coerce") for x in params["of"])
                        s = s.where(~miss, (a * b).round(2))
                    else:
                        raise ValueError(f"unknown fill method {method}")
                else:
                    raise ValueError(f"unknown operation {kind}")
                df[c] = s
                diff = changed(before, s)
                entry["affected_rows"] += int(diff.sum())
                for i in list(df.index[diff][:3]):
                    entry["examples"].append({"row": int(i) + 1, "column": c, "before": None if pd.isna(before[i]) else str(before[i]),
                                              "after": None if pd.isna(s[i]) else str(s[i])})
        entry["status"] = "applied"
    except Exception as exc:
        entry["status"] = "failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"
    report.append(entry)

pl = out_path.lower()
if pl.endswith((".xlsx", ".xls")):
    df.to_excel(out_path, index=False)
elif pl.endswith(".parquet"):
    df.to_parquet(out_path, index=False)
elif pl.endswith(".jsonl"):
    df.to_json(out_path, orient="records", lines=True)
elif pl.endswith(".json"):
    df.to_json(out_path, orient="records")
else:
    df.to_csv(out_path, index=False, sep="\t" if pl.endswith(".tsv") else ",")
json.dump({"rows": int(len(df)), "operations": report}, open(report_path, "w"), default=str)
print(json.dumps({"rows": int(len(df)), "applied": sum(1 for r in report if r["status"] == "applied")}))
'''

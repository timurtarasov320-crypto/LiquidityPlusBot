from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DATABASES = (
    "users.db",
    "payments.db",
    "signals.db",
    "market_assistant.db",
    "autoscan_logs.db",
    "autoscan_settings.db",
    "free_signals.db",
    "signal_analytics.db",
)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({q(table)})")]


def pk_columns(con: sqlite3.Connection, table: str) -> list[str]:
    rows = list(con.execute(f"PRAGMA table_info({q(table)})"))
    return [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5]]


def integrity(path: Path) -> None:
    with connect(path) as con:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"Повреждена база {path.name}: {result}")


def ensure_old_schema_in_new(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    new_tables = set(tables(new))
    for table in tables(old):
        if table in new_tables:
            continue
        sql = old.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        new.execute(sql)


def merge_simple(old: sqlite3.Connection, new: sqlite3.Connection, table: str) -> tuple[int, int]:
    old_cols = columns(old, table)
    new_cols = columns(new, table)
    common = [c for c in old_cols if c in new_cols]
    if not common:
        return 0, 0
    before = new.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    marks = ",".join("?" for _ in common)
    sql = f"INSERT OR IGNORE INTO {q(table)} ({','.join(q(c) for c in common)}) VALUES ({marks})"
    for row in old.execute(f"SELECT {','.join(q(c) for c in common)} FROM {q(table)}"):
        new.execute(sql, tuple(row[c] for c in common))
    after = new.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    return after - before, after


def merge_users(old: sqlite3.Connection, new: sqlite3.Connection) -> tuple[int, int]:
    table = "users"
    old_cols = columns(old, table); new_cols = columns(new, table)
    common = [c for c in old_cols if c in new_cols]
    before = new.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    for row in old.execute(f"SELECT {','.join(q(c) for c in common)} FROM users"):
        uid = row["user_id"]
        existing = new.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if existing is None:
            marks=','.join('?' for _ in common)
            new.execute(f"INSERT INTO users ({','.join(q(c) for c in common)}) VALUES ({marks})", tuple(row[c] for c in common))
            continue
        updates: dict[str, Any] = {}
        for c in common:
            if c == "user_id": continue
            ov=row[c]; nv=existing[c] if c in existing.keys() else None
            if c in {"vip", "referrals", "balance"}:
                try: updates[c]=max(float(nv or 0), float(ov or 0))
                except Exception: pass
            elif c == "vip_until":
                if ov and (not nv or str(ov) > str(nv)): updates[c]=ov
            elif c in {"username","first_name","referred_by","reg_date","subscription_plan"}:
                if (nv is None or nv == "") and ov not in (None, ""): updates[c]=ov
        if updates:
            new.execute("UPDATE users SET " + ','.join(f"{q(k)}=?" for k in updates) + " WHERE user_id=?", (*updates.values(), uid))
    after=new.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return after-before, after


def signal_fingerprint(row: sqlite3.Row) -> tuple:
    keys=("symbol","direction","entry","stop_loss","take_profit_1","take_profit_2","take_profit_3","created_at")
    return tuple(str(row[k] or "").strip().upper() for k in keys)


def merge_signals(old: sqlite3.Connection, new: sqlite3.Connection) -> tuple[int, int, dict[int,int]]:
    old_cols=columns(old,"signals"); new_cols=columns(new,"signals")
    common=[c for c in old_cols if c in new_cols and c != "signal_id"]
    existing={signal_fingerprint(r): int(r["signal_id"]) for r in new.execute("SELECT * FROM signals")}
    before=new.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    id_map={}
    for row in old.execute("SELECT * FROM signals ORDER BY signal_id"):
        fp=signal_fingerprint(row)
        if fp in existing:
            id_map[int(row["signal_id"])]=existing[fp]
            continue
        vals=[row[c] for c in common]
        marks=','.join('?' for _ in common)
        cur=new.execute(f"INSERT INTO signals ({','.join(q(c) for c in common)}) VALUES ({marks})", vals)
        new_id=int(cur.lastrowid); id_map[int(row["signal_id"])]=new_id; existing[fp]=new_id
    after=new.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    return after-before, after, id_map


def merge_signal_recipients(old: sqlite3.Connection, new: sqlite3.Connection, id_map: dict[int,int]) -> tuple[int,int]:
    if "signal_recipients" not in tables(old) or "signal_recipients" not in tables(new): return 0,0
    before=new.execute("SELECT COUNT(*) FROM signal_recipients").fetchone()[0]
    for r in old.execute("SELECT * FROM signal_recipients"):
        sid=id_map.get(int(r["signal_id"]), int(r["signal_id"]))
        new.execute("INSERT OR IGNORE INTO signal_recipients(signal_id,user_id,access_type,sent_at) VALUES(?,?,?,?)",(sid,r["user_id"],r["access_type"],r["sent_at"]))
    after=new.execute("SELECT COUNT(*) FROM signal_recipients").fetchone()[0]
    return after-before,after


def merge_free_usage(old,new):
    before=new.execute("SELECT COUNT(*) FROM free_signal_usage").fetchone()[0]
    for r in old.execute("SELECT * FROM free_signal_usage"):
        ex=new.execute("SELECT signals_received,updated_at FROM free_signal_usage WHERE user_id=? AND period=?",(r['user_id'],r['period'])).fetchone()
        if ex is None:
            new.execute("INSERT INTO free_signal_usage(user_id,period,signals_received,updated_at) VALUES(?,?,?,?)",tuple(r))
        else:
            new.execute("UPDATE free_signal_usage SET signals_received=?,updated_at=? WHERE user_id=? AND period=?",(max(int(ex[0] or 0),int(r['signals_received'] or 0)),max(str(ex[1] or ''),str(r['updated_at'] or '')),r['user_id'],r['period']))
    after=new.execute("SELECT COUNT(*) FROM free_signal_usage").fetchone()[0]
    return after-before,after


def merge_log_table(old,new,table,natural_col):
    before=new.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    old_cols=columns(old,table); new_cols=columns(new,table)
    common=[c for c in old_cols if c in new_cols and c not in {"id","log_id"}]
    seen={r[0] for r in new.execute(f"SELECT {q(natural_col)} FROM {q(table)}")}
    marks=','.join('?' for _ in common)
    sql=f"INSERT INTO {q(table)} ({','.join(q(c) for c in common)}) VALUES ({marks})"
    for r in old.execute(f"SELECT {','.join(q(c) for c in common)} FROM {q(table)}"):
        if r[natural_col] in seen: continue
        new.execute(sql,tuple(r[c] for c in common)); seen.add(r[natural_col])
    after=new.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    return after-before,after


def migrate(project: Path, old_dir: Path) -> None:
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    backup=project/f"database_backup_before_migration_{stamp}"
    backup.mkdir(parents=True,exist_ok=False)
    print(f"Резервная копия: {backup}")
    for name in DATABASES:
        p=project/name
        if p.exists(): shutil.copy2(p,backup/name)

    report=[]
    for name in DATABASES:
        src=old_dir/name; dst=project/name
        if not src.exists():
            print(f"[SKIP] {name}: нет в старой папке"); continue
        integrity(src)
        if not dst.exists():
            shutil.copy2(src,dst); report.append((name,"скопирована целиком",None)); continue
        integrity(dst)
        old=connect(src); new=connect(dst)
        try:
            new.execute("BEGIN IMMEDIATE")
            ensure_old_schema_in_new(old,new)
            details=[]
            for table in tables(old):
                if table not in tables(new): continue
                if name=="users.db" and table=="users": add,total=merge_users(old,new)
                elif name=="signals.db" and table=="signals":
                    add,total,id_map=merge_signals(old,new); details.append(f"signals +{add} (итого {total})"); continue
                elif name=="signals.db" and table=="signal_recipients": add,total=merge_signal_recipients(old,new,id_map)
                elif name=="free_signals.db" and table=="free_signal_usage": add,total=merge_free_usage(old,new)
                elif name=="autoscan_logs.db" and table=="autoscan_logs": add,total=merge_log_table(old,new,table,"started_at")
                elif name=="autoscan_settings.db" and table=="autoscan_sent_setups": add,total=merge_log_table(old,new,table,"setup_id")
                else: add,total=merge_simple(old,new,table)
                details.append(f"{table} +{add} (итого {total})")
            new.commit(); report.append((name,"; ".join(details),None))
        except Exception as e:
            new.rollback(); raise RuntimeError(f"Ошибка миграции {name}: {e}") from e
        finally:
            old.close(); new.close()
        integrity(dst)

    print("\n=== ОТЧЁТ ===")
    for name,detail,_ in report: print(f"{name}: {detail}")
    print("\nМИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО")


def main() -> int:
    parser=argparse.ArgumentParser(description="Безопасное объединение старых SQLite-баз LiquidityPlus с текущим проектом")
    parser.add_argument("--project", default=".", help="Папка текущего проекта")
    parser.add_argument("--old", default=None, help="Папка старой версии LiquidityPlus")
    args=parser.parse_args()
    project=Path(args.project).resolve()
    if args.old:
        old=Path(args.old).resolve()
    else:
        candidate=project.parent/"LiquidityPlus_WORKING_15-07-2026"
        old=candidate if candidate.exists() else None
    if old is None or not old.exists():
        print("Не найдена старая папка. Запусти так:")
        print('py migrate_databases.py --old "C:\\Users\\TIMUR\\OneDrive\\Desktop\\LiquidityPlus_WORKING_15-07-2026"')
        return 2
    print("Текущий проект:",project)
    print("Старые базы:",old)
    migrate(project,old)
    return 0

if __name__=="__main__":
    raise SystemExit(main())

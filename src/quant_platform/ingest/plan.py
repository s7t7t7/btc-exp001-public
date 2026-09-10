from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class MonthTask:
    year:int
    month:int
    phase:str

def iter_months(start:str,end:str,phase:str):
    a=datetime.fromisoformat(start.replace("Z","+00:00"))
    b=datetime.fromisoformat(end.replace("Z","+00:00"))
    cur=datetime(a.year,a.month,1,tzinfo=timezone.utc)
    while cur <= b:
        yield MonthTask(cur.year,cur.month,phase)
        if cur.month==12:
            cur=datetime(cur.year+1,1,1,tzinfo=timezone.utc)
        else:
            cur=datetime(cur.year,cur.month+1,1,tzinfo=timezone.utc)

def discovery_plan():
    return list(iter_months(
        "2020-01-01T00:00:00Z",
        "2023-12-31T23:55:00Z",
        "discovery",
    ))

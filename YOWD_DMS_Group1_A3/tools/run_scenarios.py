"""Run the seven demonstration scenarios through the real console UI.

Each session starts ``python -m yowd_dms`` as a separate process, types the
scripted input, and saves the full transcript (inputs are echoed) to
``evidence/transcripts``. Run from the project root:

    python tools/run_scenarios.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evidence" / "demo_data"
OUT = ROOT / "evidence" / "transcripts"
PW = "Password1"


def login(email: str, password: str = PW) -> list[str]:
    return ["1", email, password]


def tracking_code(request_id: str) -> str:
    records = json.loads((DATA / "requests.json").read_text())
    return next(r["tracking_code"] for r in records
                if r["request_id"] == request_id)


Inputs = list[str] | Callable[[], list[str]]


def scenarios() -> list[tuple[str, str, Inputs, bool]]:
    s1 = [
        "2", "Sam Smith", "sam@example", "sam@example.com", "04123",
        "0498 765 432", "short", PW,
        *login("sam@example.com"),
        "1", "q",
        "1", "12 John St", "Hawthorn", "312", "3122",
        "1 George St", "Sydney", "2000",
        "5 Station St", "Box Hill", "3128",
        "", "Ravi Patel", "123", "0422 111 222",
        "Laptop", "X", "M", "45", "2.5", "Y",
        "Charger", "S", "0.5", "N", "1",
        "E", "2", "8 Bridge Rd", "Richmond", "3121",
        "S",
        "2", "DR-0001",
        "0", "0"]
    s2 = [
        *login("office@yowd.com.au"),
        "1", "0412", "U", "U-0005",
        "100 Smith St", "Fitzroy", "3065",
        "20 Clarendon St", "Southbank", "3006",
        "Mei Chen", "0433 222 333",
        "Documents", "S", "1", "N", "2", "S",
        "2", "DR-0001", "Q", "tomorrow", "2026-10-27 07:00",
        "2026-10-27 09:30", "N",
        "2", "DR-0002", "Q", "2026-10-27 10:00", "Y", "abc", "25", "",
        "Regular client rate agreed by phone",
        "0", "0"]
    s3 = [
        *login("sam@example.com"),
        "3", "DR-0001", "A", "1", "N",
        "4", "DR-0001", "P",
        "0",
        *login("chloe@example.com"),
        "3", "DR-0002", "A", "1", "N",
        "4", "DR-0002", "C",
        "2", "DR-0002",
        "0", "0"]
    s4 = [
        *login("office@yowd.com.au"),
        "3", "DR-0001", "U-0004",
        "3", "DR-0001", "U-0003",
        "3", "DR-0002",
        "4", "U-0004", "A", "27/10 9am", "2026-10-27 09:00",
        "2026-10-27 08:00", "2026-10-27 09:00", "2026-10-27 13:00",
        "3", "DR-0002", "U-0004",
        "4", "U-0003", "R", "AV-0001", "N",
        "5", "nobody", "Richmond",
        "0", "0"]

    def s5() -> list[str]:
        return [
            "3", "ABC123", tracking_code("DR-0002"),
            *login("dana@yowd.com.au"),
            "2", "DR-0001", "Y",
            "3", "DR-0001", "",
            "0",
            *login("dev@yowd.com.au"),
            "2", "DR-0002", "Y",
            "3", "DR-0002", "20", "25.00", "",
            "0", "0"]
    s6 = [
        *login("office@yowd.com.au", "wrong1"),
        *login("office@yowd.com.au", "wrong2"),
        *login("office@yowd.com.au", "wrong3"),
        *login("office@yowd.com.au", "wrong4"),
        *login("office@yowd.com.au", "wrong5"),
        *login("office@yowd.com.au"),
        *login("owner@yowd.com.au"),
        "6", "1", "", "",
        "6", "3", "2026-13-01", "2026-10-27", "U-0003",
        "6", "1", "2026-10-28", "",
        "8", "A", "2", "Priya Singh", "priya@yowd", "priya@yowd.com.au",
        "0411000003", PW,
        "8", "D", "U-0004", "Y",
        "8", "B",
        "0", "0"]
    s7 = [
        # Client corrects own details: invalid phone, change of mind, save.
        *login("chloe@example.com"),
        "7", "D", "", "123", "0499 888 777", "", "N",
        "7", "D", "", "0499 888 777", "8 Glenferrie Rd, Hawthorn VIC 3122",
        "Y",
        "1", "8 Glenferrie Rd", "Hawthorn", "3122",
        "20 Clarendon St", "Southbank", "3006",
        "Mei Chen", "0433 222 333", "Books", "M", "4", "N", "1", "S",
        "0",
        # Office quotes it, updates one client and deactivates another.
        *login("office@yowd.com.au"),
        "2", "DR-0003", "Q", "2026-10-28 09:00", "N",
        "9", "sam", "U", "U-0006", "", "0498 765 000", "", "Y",
        "9", "", "D", "U-0005", "Y",
        "9", "sam", "D", "U-0006", "Y",
        "0",
        # Card declined (A2 7.2), switch to cash, view the invoice.
        *login("chloe@example.com"),
        "3", "DR-0003", "A", "1", "N",
        "T",
        "4", "DR-0003", "P", "C",
        "6", "DR-0003",
        "0",
        # Office payment records: blank note, change of mind, record, invoice.
        *login("office@yowd.com.au"),
        "10", "2", "M", "DR-0003", "",
        "Client paid cash at the office counter", "N",
        "10", "2", "M", "DR-0003", "Client paid cash at the office counter",
        "Y",
        "10", "1", "I", "DR-0001",
        "0",
        # Owner updates a driver, then sees the no-cover warnings.
        *login("owner@yowd.com.au"),
        "8", "U", "U-0003", "", "0411 000 009", "Y",
        "4", "U-0003", "R", "AV-0002", "N",
        "8", "D", "U-0003", "N",
        "11", "W", "wrongpass", "NewPassword2", "NewPassword2",
        "0", "0"]
    return [
        ("S1_client_registers_and_requests", "2026-10-27 08:00", s1, True),
        ("S2_office_logs_request_and_quotes", "2026-10-27 08:20", s2, False),
        ("S3_clients_accept_and_pay", "2026-10-27 08:40", s3, False),
        ("S4_allocation_and_availability", "2026-10-27 09:00", s4, False),
        ("S5_tracking_pickup_and_handover", "2026-10-27 10:05", s5, False),
        ("S6_security_statistics_and_staff", "2026-10-27 17:00", s6, False),
        ("S7_records_invoices_and_details", "2026-10-27 17:30", s7, False),
    ]


def main() -> None:
    shutil.rmtree(DATA, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
               PYTHONIOENCODING="utf-8")
    for name, when, script, seed in scenarios():
        inputs = script() if callable(script) else script
        args = [sys.executable, "-m", "yowd_dms", "--data", str(DATA),
                "--demo-time", when] + (["--seed-demo"] if seed else [])
        result = subprocess.run(args, input="\n".join(inputs) + "\n",
                                capture_output=True, text=True, env=env,
                                cwd=ROOT, check=False)
        command = ("$ python -m yowd_dms --data evidence/demo_data "
                   f"--demo-time \"{when}\"" +
                   (" --seed-demo" if seed else ""))
        transcript = command + "\n" + result.stdout + result.stderr
        (OUT / f"{name}.txt").write_text(transcript, encoding="utf-8")
        print(f"{name}: exit {result.returncode}, "
              f"{len(transcript.splitlines())} lines")


if __name__ == "__main__":
    main()

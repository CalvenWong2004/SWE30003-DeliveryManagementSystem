# YOWD Delivery Management System - SWE30003 Assignment 3, Group 1

A text-based, object-oriented implementation of the Delivery Management
System for *Your Order We Deliver*, built from the Assignment 2 design.

## Platform

| Item | Used for development and testing |
| --- | --- |
| Language | Python 3.13 (works on 3.10 or later; standard library only) |
| Operating systems | Ubuntu Linux 24.04; also runs on Windows 10/11 and macOS |
| IDE | Visual Studio Code with the Python and Flake8 extensions |
| Style check | flake8 7 (PEP 8, line length 79) |
| Tests | Python `unittest` |

## Run it

From this folder (the one containing `src` and `tests`):

```
# Windows
run.bat --seed-demo

# macOS / Linux
./run.sh --seed-demo
```

or directly: `PYTHONPATH=src python -m yowd_dms --seed-demo`.

* `--seed-demo` adds demo accounts the first time (all passwords `Password1`):
  owner@yowd.com.au, office@yowd.com.au, dana@yowd.com.au, dev@yowd.com.au,
  chloe@example.com.
* `--data FOLDER` chooses where the JSON data files are kept (default `data`).
* `--demo-time "2026-10-27 08:00"` fixes the clock so a demonstration is
  repeatable, and adds demo-only menu items: "advance the clock" (office, for
  quote expiry) and "decline the next card payment" (client, for the
  declined-card scenario, A2 section 7.2).
* Without `--seed-demo` on an empty folder, the first run asks you to create
  the business owner account.

Type `q` at any prompt to cancel the current task.

## Check it

The application and tests use only the Python standard library. The
optional checking tools need `pip install -r requirements.txt` (flake8 and
Pillow); `render_screenshots.py` uses a Linux font path.

```
python -m compileall src                      # compile every module
python -m unittest discover -s tests -v       # 38 acceptance-style tests
flake8 src                                    # coding standard (PEP 8)
python tools/run_scenarios.py                 # replay the 7 report scenarios
python tools/render_screenshots.py            # turn transcripts into PNGs
```

## Layout

```
src/yowd_dms/
  app/dms.py            DeliveryManagementSystem (Singleton + Facade)
  domain/               User hierarchy, DeliveryRequest, Quote, Payment,
                        PricingStrategy, StaffAvailability, data holders
  registry/             UserDirectory, StaffRoster, DeliveryRegistry
  services/             ServiceArea, NotificationService (Observer)
  persistence/          JsonStore and Data Mappers
  ui/                   Console screens per role, plus shared account screens
tests/                  unittest suite traced to SRS 10.3 and A2 CRC cards
tools/                  Scenario runner and screenshot renderer
evidence/               Transcripts, screenshots, build log
docs/diagrams/          Mermaid sources and PNGs for the report
```

Coding standard: PEP 8 (van Rossum, Warsaw & Coghlan 2001), PEP 257
docstrings and PEP 484 type hints, checked with flake8.

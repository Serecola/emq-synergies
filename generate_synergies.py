"""
EMQ Synergy Generator — Batch Edition
======================================
Reads every SongHistory JSON from a folder called `matches/` (relative to
this script), merges them, and writes a single `synergies.json` that the
synergy viewer HTML reads automatically.

Usage:
    python generate_synergies.py
    python generate_synergies.py --matches-dir path/to/matches --out synergies.json

Output schema
-------------
{
  "generated_at": "<ISO timestamp>",
  "total_songs":  <int>,
  "players":       { "<uid>": "<username>", ... },
  "player_stats":  {
    "<uid>": {
      "songs_present": <int>,
      "songs_correct": <int>,
      "songs_on_list": <int>,
      "songs_correct_on_list": <int>
    }, ...
  },
  "synergy_matrix": {
    "<guesser_uid>": {
      "<owner_uid>": {
        "songs_together": <int>,
        "synergy_events":  <int>,
        "guess_rate_pct":  <float>
      }, ...
    }, ...
  }
}

Synergy definition
------------------
  synergy_events[X][Y]  =  rounds where X got IsGuessCorrect=true
                            AND Y had IsOnList=true AND X was present.
  For X==Y (self): synergy_events[X][X] = rounds where X got IsGuessCorrect=true
                   AND X had IsOnList=true (correct guesses on own list).
  songs_together[X][Y] = rounds where Y had IsOnList=true AND X was present.
  guess_rate_pct[X][Y]  =  synergy_events / songs_together * 100

"You Synergize with Them" (X→Y): you (X) correctly guess songs on their (Y) list.
"They Synergize with You" (Y→X): they (Y) correctly guess songs on your (X) list.
"Self" (X→X): your correct guesses on your own list.
"""

import json
import sys
import os
import glob
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def song_title(song: dict) -> str:
    for t in song.get("Titles", []):
        if t.get("IsMainTitle"):
            return t.get("LatinTitle") or t.get("NonLatinTitle") or "Unknown"
    return "Unknown"


def load_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def process_files(json_paths: list) -> dict:
    # Load blacklist
    blacklist = set()
    try:
        with open("blacklist.txt", "r", encoding="utf-8") as f:
            for line in f:
                uid = line.strip()
                if uid:
                    blacklist.add(uid)
    except FileNotFoundError:
        pass

    players      = {}                                                    # uid → username
    player_stats = defaultdict(lambda: dict(songs_present=0, songs_correct=0, songs_on_list=0, songs_correct_on_list=0))
    together     = defaultdict(lambda: defaultdict(int))                 # [gid][oid] → int
    hits         = defaultdict(lambda: defaultdict(int))                 # [gid][oid] → int
    global_round = 0

    for path in sorted(json_paths):
        fname = os.path.basename(path)
        try:
            data = load_file(path)
        except Exception as e:
            print(f"  [SKIP] {fname}: {e}", file=sys.stderr)
            continue

        print(f"  {fname}  ({len(data)} rounds)")

        for round_key in sorted(data.keys(), key=lambda k: int(k)):
            round_data = data[round_key]
            song_info  = round_data.get("Song", {})
            pg_infos   = round_data.get("PlayerGuessInfos", {})

            round_players = {}
            for uid, gi in pg_infos.items():
                mst = gi.get("Mst", {})
                if not mst or not mst.get("Username"):
                    continue

                username   = mst["Username"]
                is_correct = mst.get("IsGuessCorrect")   # True / False / None
                is_on_list = mst.get("IsOnList")         # True / False / None

                # Filter out blacklisted or high UID players
                if uid in blacklist or (uid.isdigit() and int(uid) > 100000):
                    continue

                players[uid] = username
                round_players[uid] = {
                    "username":   username,
                    "is_correct": is_correct,
                    "is_on_list": is_on_list,
                }

                ps = player_stats[uid]
                ps["songs_present"] += 1
                if is_correct is True:
                    ps["songs_correct"] += 1
                    if is_on_list is True:
                        ps["songs_correct_on_list"] += 1
                if is_on_list is True:
                    ps["songs_on_list"] += 1

            present = list(round_players.keys())
            for gid in present:
                for oid in present:
                    if gid == oid:
                        continue
                    o = round_players[oid]
                    if o["is_on_list"] is True:
                        together[gid][oid] += 1
                        g = round_players[gid]
                        if g["is_correct"] is True:
                            hits[gid][oid] += 1

            global_round += 1

    # Build synergy matrix
    synergy_matrix = {}
    for gid in players:
        synergy_matrix[gid] = {}
        for oid in players:
            if gid == oid:
                ps = player_stats[gid]
                t = ps["songs_on_list"]
                h = ps["songs_correct_on_list"]
                rate = round(h / t * 100, 2) if t > 0 else 0.0
            else:
                t = together[gid].get(oid, 0)
                h = hits[gid].get(oid, 0)
                rate = round(h / t * 100, 2) if t > 0 else 0.0
            synergy_matrix[gid][oid] = {
                "songs_together": t,
                "synergy_events":  h,
                "guess_rate_pct":  rate,
            }

    return {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "total_songs":   global_round,
        "players":        players,
        "player_stats":   {uid: dict(v) for uid, v in player_stats.items()},
        "synergy_matrix": synergy_matrix,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Batch-generate EMQ synergy data from a matches/ folder."
    )
    parser.add_argument(
        "--matches-dir", default="matches",
        help="Folder containing SongHistory JSON files (default: ./matches)"
    )
    parser.add_argument(
        "--out", default="synergies.json",
        help="Output file path (default: ./synergies.json)"
    )
    args = parser.parse_args()

    matches_dir = Path(args.matches_dir)
    if not matches_dir.is_dir():
        print(f"Error: '{matches_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    json_paths = sorted(glob.glob(str(matches_dir / "*.json")))
    if not json_paths:
        print(f"No JSON files found in '{matches_dir}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(json_paths)} file(s) in '{matches_dir}/':")
    result = process_files(json_paths)

    out_path = Path(args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    players = result["players"]
    print(f"\n✓  synergies.json written to {out_path.resolve()}")
    print(f"   Total songs : {result['total_songs']}")
    print(f"   Players ({len(players)}): {', '.join(players.values())}")

    # Top-10 summary
    matrix = result["synergy_matrix"]
    pairs  = []
    for gid, gname in players.items():
        for oid, oname in players.items():
            if gid == oid:
                continue
            s = matrix[gid][oid]
            if s["songs_together"] > 0:
                pairs.append((gname, oname, s["guess_rate_pct"],
                               s["synergy_events"], s["songs_together"]))
    pairs.sort(key=lambda x: -x[2])
    print("\nTop synergy pairs (guesser → list-owner):")
    for gname, oname, rate, h, t in pairs[:10]:
        print(f"  {gname:20s} → {oname:20s}  {rate:5.1f}%  ({h}/{t})")


if __name__ == "__main__":
    main()

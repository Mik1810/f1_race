#!/usr/bin/env python3
"""
generate_agents.py — Generate DALI agent files from agents.json.

Reads agents.json and produces:
  mas/instances/{id}.txt        — one-line type reference
  mas/types/{id}Car.txt         — DALI logic for each car agent
  mas/types/pitWallType.txt     — orchestration logic (round-robin laps, rankings)
  mas/types/semaphoreType.txt   — waits for all agents, fires lights sequence
  mas/types/safetyCarType.txt   — broadcasts to all car agents

Also run by startmas.sh before building the MAS.

Usage:
    python generate_agents.py [--config agents.json]
"""

import json
import os
import argparse

BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCES_DIR = os.path.join(BASE, "mas", "instances")
TYPES_DIR     = os.path.join(BASE, "mas", "types")


# helpers 

def wf(path: str, text: str) -> None:
    """Write file, create directories if needed, always Unix line endings."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(text)
    print(f"  [gen] {os.path.relpath(path, BASE)}")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# car type template

def gen_car_type(car: dict) -> str:
    i   = car["id"]
    tm  = car["team"]
    mdl = car["car_model"]
    drv = car["driver"]

    return (
        f":- write('[{tm}] {mdl} ready on the grid.'), send_m(semaphore, send_message(ready, {i})).\n"
        f":- dynamic race_started/0.\n"
        f":- dynamic race_over/0.\n"
        f":- dynamic engine_failure_{i}_fired/0.\n"
        f":- dynamic push_lap_{i}_fired/0.\n"
        "\n"
        f"start_raceE:>\n"
        f"    assert(race_started),\n"
        f"    retractall(push_lap_{i}_fired),\n"
        f"    write('[{tm}] LAP 1 -- LIGHTS OUT! {drv} launches off the line!'),\n"
        f"    messageA(pitwall, send_message(lap_done_{i}, {i})).\n"
        "\n"
        f"lap_go_{i}E:>\n"
        f"    if(\\+ race_started, assert(race_started), true),\n"
        f"    retractall(push_lap_{i}_fired),\n"
        f"    write('[{tm}] On the power! {drv} attacking every sector.'),\n"
        f"    messageA(pitwall, send_message(lap_done_{i}, {i})).\n"
        "\n"
        f"box_{i}E:>\n"
        f"    write('[{tm}] BOX BOX BOX! {tm} dives into the pits.'),\n"
        f"    messageA(pitwall, send_message(pit_done_{i}, {i})).\n"
        "\n"
        f"rain_warningE:>\n"
        f"    if(race_over, true,\n"
        f"        (write('[{tm}] RAIN WARNING. {drv} switching to intermediate tyres.'), nl)).\n"
        "\n"
        f"safety_car_deployedE:>\n"
        f"    if(race_over, true,\n"
        f"        (write('[{tm}] Safety car deployed. {drv} conserving tyres.'), nl)).\n"
        "\n"
        f"retire_{i}E:>\n"
        f"    write('[{tm}] *** {drv.upper()} PARKS THE CAR. {tm} is out of the race. ***').\n"
        "\n"
        f"green_flagE:>\n"
        f"    if(race_over, true,\n"
        f"        (write('[{tm}] GREEN FLAG! {drv} pushing flat out!'), nl)).\n"
        "\n"
        f"race_endE:>\n"
        f"    assert(race_over).\n"
        "\n"
        f"engine_failure_{i} :-\n"
        f"    race_started,\n"
        f"    \\+ race_over,\n"
        f"    \\+ engine_failure_{i}_fired,\n"
        f"    random(0, 1000, R),\n"
        f"    R < 2.\n"
        "\n"
        f"engine_failure_{i}I:>\n"
        f"    assert(engine_failure_{i}_fired),\n"
        f"    write('[{tm}] *** ENGINE FAILURE -- Power loss! {drv} slowing! ***'),\n"
        f"    send_m(pitwall, send_message({i}_engine_failure, {i})),\n"
        f"    send_m(pitwall, inform(telemetry({i}, engine_failure, critical), {i})).\n"
        "\n"
        f"push_lap_{i} :-\n"
        f"    race_started,\n"
        f"    \\+ race_over,\n"
        f"    \\+ push_lap_{i}_fired,\n"
        f"    random(0, 100, R),\n"
        f"    R < 10.\n"
        "\n"
        f"push_lap_{i}I:>\n"
        f"    assert(push_lap_{i}_fired),\n"
        f"    write('[{tm}] PUSH LAP! {drv} going flat out -- setting fastest sectors!'),\n"
        f"    send_m(pitwall, send_message({i}_push_lap, {i})),\n"
        f"    send_m(pitwall, inform(telemetry({i}, push_lap, active), {i})).\n"
    )


# pitwall type template

def gen_pitwall_type(cars: list, total_laps: int) -> str:
    ids = [c["id"] for c in cars]
    n   = len(ids)
    L   = []

    # init
    L.append(":- write('[PitWall] Online.'), send_m(semaphore, send_message(ready, pitwall)).")
    for c in cars:
        L.append(f":- dynamic {c['id']}_time/1.")
        L.append(f":- dynamic {c['id']}_dnf/0.")
    L.append(":- dynamic lap/1.")
    L.append(":- dynamic race_over/0.")
    L.append(":- dynamic track_event_this_lap/0.")
    for c in cars:
        L.append(f":- assert({c['id']}_time(0)).")
    L.append(":- assert(lap(0)).")
    L.append("")

    # add_time / effective_time per car
    for c in cars:
        i = c["id"]
        L += [
            f"add_time({i}, D) :-",
            f"    retract({i}_time(S)),",
            f"    S1 is S + D,",
            f"    assert({i}_time(S1)).",
            "",
            f"effective_time({i}, T) :- {i}_dnf, !, T = 9999.",
            f"effective_time({i}, T) :- {i}_time(T).",
            "",
        ]

    # random_track_event: broadcast to all cars
    add_sc  = "\n".join(f"         add_time({i}, 10)," for i in ids)
    sc_msgs = ",\n".join(
        f"         send_m({i}, send_message(safety_car_deployed, pitwall))" for i in ids
    )
    add_rain   = "\n".join(f"         add_time({i}, 5)," for i in ids)
    rain_msgs  = ",\n".join(
        f"         send_m({i}, send_message(rain_warning, pitwall))" for i in ids
    )

    L.append("random_track_event :-")
    L.append("    if(track_event_this_lap, true,")
    L.append("        (random(0, 10, R),")
    L.append("         if(R < 2,")
    L.append("             (assert(track_event_this_lap),")
    L.append("              write('[Race Director] SAFETY CAR deployed. +10s to all.'), nl,")
    for i in ids:
        L.append(f"              add_time({i}, 10),")
    L.append("              send_m(safety_car, send_message(deploy, pitwall)),")
    ids_list = "[" + ", ".join(ids) + "]"
    L.append(f"              broadcast_to_list({ids_list}, safety_car_deployed, pitwall)")
    L.append("             ),")
    L.append("         if(R < 4,")
    L.append("             (assert(track_event_this_lap),")
    L.append("              write('[Race Director] HEAVY RAIN. +5s to all.'), nl,")
    for i in ids:
        L.append(f"              add_time({i}, 5),")
    ids_list = "[" + ", ".join(ids) + "]"
    L.append(f"              broadcast_to_list({ids_list}, rain_warning, pitwall)")
    L.append("             ),")
    L.append("             true)))).")
    L.append("")

    # announce_winner via findall + keysort (built-in in SICStus, no library needed)
    L += [
        "announce_winner :-",
        "    findall(T-Id, effective_time(Id, T), Pairs0),",
        "    keysort(Pairs0, Pairs),",
        "    write('[PitWall] === FINAL RESULTS ==='), nl,",
        "    print_podium(Pairs, 1).",
        "",
        "print_podium([], _).",
        "print_podium([T-Id|Rest], Pos) :-",
        "    if(T =:= 9999,",
        "        (write('[PitWall] P'), write(Pos), write(': '), write(Id), write(' -- DNF'), nl),",
        "        (write('[PitWall] P'), write(Pos), write(': '), write(Id), write(' -- '), write(T), write('s'), nl)",
        "    ),",
        "    Pos1 is Pos + 1,",
        "    print_podium(Rest, Pos1).",
        "",
        "print_standings :-",
        "    findall(T-Id, effective_time(Id, T), Pairs0),",
        "    keysort(Pairs0, Pairs),",
        "    write('[PitWall] --- STANDINGS ---'), nl,",
        "    print_podium(Pairs, 1),",
        "    write('[PitWall] -----------------'), nl.",
        "",
    ]

    # declare_winner — sends race_end to all cars
    race_end_sends = ",\n".join(
        f"         send_m({i}, send_message(race_end, pitwall))" for i in ids
    )
    L.append("declare_winner :-")
    L.append("    if(race_over, true,")
    L.append("        (assert(race_over),")
    L.append("         write('[PitWall] === CHEQUERED FLAG ==='), nl,")
    L.append("         announce_winner,")
    ids_list = "[" + ", ".join(ids) + "]"
    L.append(f"         broadcast_to_list({ids_list}, race_end, pitwall))).")
    L.append("")
    # sc_recalled — green flag broadcast to all cars
    L.append("sc_recalledE:>")
    L.append("    write('[Race Director] GREEN FLAG! Track is clear.'), nl,")
    ids_list = "[" + ", ".join(ids) + "]"
    L.append(f"    broadcast_to_list({ids_list}, green_flag, pitwall).")
    L.append("")
    # lap_done events (round-robin) 
    # car[idx] → triggers car[(idx+1) % n]
    # last car also increments lap counter and checks total_laps
    for idx, c in enumerate(cars):
        i      = c["id"]
        tm     = c["team"]
        next_c = cars[(idx + 1) % n]
        next_i = next_c["id"]
        is_last = (idx == n - 1)

        L.append(f"lap_done_{i}E:>")
        L.append(f"    if(race_over, true,")
        L.append(f"        (random(60, 91, T),")
        L.append(f"         add_time({i}, T),")
        L.append(f"         write('[PitWall] {tm} lap: '), write(T), write('s'), nl,")
        if is_last:
            L.append(f"         (track_event_this_lap -> (retract(track_event_this_lap), send_m(safety_car, send_message(recall, pitwall))) ; true),")
            L.append(f"         retract(lap(N)), N1 is N + 1, assert(lap(N1)),")
            L.append(f"         write('[PitWall] Lap '), write(N1), write(' / {total_laps}'), nl,")
            L.append(f"         print_standings,")
            L.append(f"         if(N1 =:= {total_laps}, declare_winner,")
            L.append(f"             (random_track_event,")
            L.append(f"              messageA({next_i}, send_message(lap_go_{next_i}, pitwall)))))).") 
        else:
            L.append(f"         random_track_event,")
            L.append(f"         messageA({next_i}, send_message(lap_go_{next_i}, pitwall))))." )
        L.append("")

    # pit_done events (same round-robin, +25 s) 
    for idx, c in enumerate(cars):
        i      = c["id"]
        tm     = c["team"]
        next_c = cars[(idx + 1) % n]
        next_i = next_c["id"]
        is_last = (idx == n - 1)

        L.append(f"pit_done_{i}E:>")
        L.append(f"    if(race_over, true,")
        L.append(f"        (add_time({i}, 25),")
        L.append(f"         write('[PitWall] {tm} pit stop +25s.'), nl,")
        if is_last:
            L.append(f"         retract(lap(N)), N1 is N + 1, assert(lap(N1)),")
            L.append(f"         if(N1 =:= {total_laps}, declare_winner,")
            L.append(f"             messageA({next_i}, send_message(lap_go_{next_i}, pitwall))))).")
        else:
            L.append(f"         messageA({next_i}, send_message(lap_go_{next_i}, pitwall)))).")
        L.append("")

    # engine failure & push lap events
        tm  = c["team"]
        drv = c["driver"]
        L += [
            f"{i}_engine_failureE:>",
            f"    assert({i}_dnf),",
            f"    write('[PitWall] {tm} DNF. {drv} is out.'), nl,",
            f"    send_m({i}, send_message(retire_{i}, pitwall)),",
            f"    declare_winner.",
            "",
            f"{i}_push_lapE:>",
            f"    if(race_over, true, (add_time({i}, -3), write('[PitWall] {tm} fastest lap! -3s'), nl)).",
            "",
        ]

    return "\n".join(L)



def gen_semaphore_type(cars: list) -> str:
    # ready agents: all cars + pitwall + safety_car
    total     = len(cars) + 2
    first_id  = cars[0]["id"]

    L = [
        ":- write('Semaphore: Waiting for all F1 agents to be ready...'), nl.",
        ":- dynamic ready_count/1.",
        ":- if(clause(ready_count(_), _), true, assert(ready_count(0))).",
        "",
        "lights_sequence :-",
        "    write('Semaphore: =========================================='), nl,",
        "    write('Semaphore: F1 RACE START SEQUENCE INITIATED'), nl,",
        "    write('Semaphore: =========================================='), nl,",
        "    sleep(1), write('Semaphore:  (O) ( ) ( ) ( ) ( )  -- light 1'), nl,",
        "    sleep(1), write('Semaphore:  (O) (O) ( ) ( ) ( )  -- light 2'), nl,",
        "    sleep(1), write('Semaphore:  (O) (O) (O) ( ) ( )  -- light 3'), nl,",
        "    sleep(1), write('Semaphore:  (O) (O) (O) (O) ( )  -- light 4'), nl,",
        "    sleep(1), write('Semaphore:  (O) (O) (O) (O) (O)  -- light 5'), nl,",
        "    sleep(2), write('Semaphore:  *** LIGHTS OUT! ***'), nl,",
        "    write('Semaphore:  ( ) ( ) ( ) ( ) ( )  -- GO GO GO!'), nl,",
        "    write('Semaphore: =========================================='), nl,",
        f"    send_m({first_id}, send_message(start_race, semaphore)).",
        "",
        "readyE:>",
        "    retract(ready_count(N)),",
        "    retractall(ready_count(_)),",
        "    N1 is N + 1,",
        "    assert(ready_count(N1)),",
        "    write('Semaphore: '), write(N1), write('/'), write(" + str(total) + "), write(' agents ready.'), nl,",
        f"    if(N1 =:= {total}, lights_sequence, true).",
        "",
    ]
    return "\n".join(L)



def gen_safety_car_type(cars: list) -> str:
    ids = [c["id"] for c in cars]
    n   = len(ids)
    L   = [
        ":- write('Safety Car ready. Standing by.'), nl, send_m(semaphore, send_message(ready, safety_car)).",
        ":- dynamic sc_active/0.",
        "",
        "deployE:>",
        "    if(sc_active, true,",
        "        (assert(sc_active),",
        "         write('SAFETY CAR: DEPLOYED! Yellow flags! All cars reduce speed!'), nl)).",
        "",
        "recallE:>",
        "    if(sc_active,",
        "        (retract(sc_active),",
        "         write('SAFETY CAR: Returning to pits. Notifying race control.'), nl,",
        "         send_m(pitwall, send_message(sc_recalled, safety_car))),",
        "        true).",
        "",
    ]
    return "\n".join(L)



FIXED_INSTANCES = {"pitwall", "safety_car", "semaphore"}


def cleanup_stale_cars(removed_ids: set) -> None:
    """Delete instance + type files for cars that are no longer in agents.json."""
    for cid in sorted(removed_ids):
        for path in [
            os.path.join(INSTANCES_DIR, f"{cid}.txt"),
            os.path.join(TYPES_DIR, f"{cid}Car.txt"),
        ]:
            if os.path.exists(path):
                os.remove(path)
                print(f"  [del] {os.path.relpath(path, BASE)}")


def main():
    parser = argparse.ArgumentParser(description="Generate DALI agent files from agents.json.")
    parser.add_argument("--config", default=os.path.join(BASE, "agents.json"),
                        help="Path to agents.json (default: agents.json in project root)")
    parser.add_argument("--force", action="store_true",
                        help="Force regeneration even if the car list is unchanged.")
    args = parser.parse_args()

    cfg        = load_config(args.config)
    cars       = cfg["cars"]
    total_laps = cfg.get("total_laps", 5)

    # ── Stale-file cleanup + skip check ───────────────────────────────────────
    expected_ids: set = {c["id"] for c in cars}

    existing_ids: set = set()
    if os.path.isdir(INSTANCES_DIR):
        existing_ids = {
            os.path.splitext(f)[0]
            for f in os.listdir(INSTANCES_DIR)
            if f.endswith(".txt") and os.path.splitext(f)[0] not in FIXED_INSTANCES
        }

    added   = expected_ids - existing_ids
    removed = existing_ids - expected_ids

    # Always remove stale files for cars that were deleted from agents.json
    if removed:
        print(f"  Removed cars: {sorted(removed)} — deleting stale files.")
        cleanup_stale_cars(removed)

    if not args.force and not added and not removed:
        print(f"Agents unchanged — skipping generation.")
        print(f"  Cars: {sorted(expected_ids)}")
        print(f"  (use --force to regenerate anyway)")
        return

    if added:
        print(f"  New cars detected: {sorted(added)}")

    print(f"Generating agents from {os.path.relpath(args.config, BASE)}")
    print(f"  Cars: {[c['id'] for c in cars]}")
    print(f"  Total laps: {total_laps}")
    print()

    # ── car instances & types ─────────────────────────────────────────────────
    for car in cars:
        # mas/instances/{id}.txt
        wf(os.path.join(INSTANCES_DIR, f"{car['id']}.txt"), f"{car['id']}Car\n")
        # mas/types/{id}Car.txt
        wf(os.path.join(TYPES_DIR, f"{car['id']}Car.txt"), gen_car_type(car))

    # ── shared agent types ────────────────────────────────────────────────────
    wf(os.path.join(TYPES_DIR, "pitWallType.txt"),   gen_pitwall_type(cars, total_laps))
    wf(os.path.join(TYPES_DIR, "semaphoreType.txt"), gen_semaphore_type(cars))
    wf(os.path.join(TYPES_DIR, "safetyCarType.txt"), gen_safety_car_type(cars))

    print()
    print("Done. Run 'bash startmas.sh' to start the MAS.")


if __name__ == "__main__":
    main()

"""
main.py
────────
Starts the full ACDT stack:
  1. Provisions Eclipse Ditto (vehicle + mechanic twins)
  2. Builds Neo4j knowledge graph
  3. Starts Ditto sync loop
  4. Starts autonomous monitor (safety + maintenance)
  5. Starts OBD-II simulator (only while the live data-source mode is
     "simulator", or "auto" and no adapter is found — see
     start_simulator_watcher())
  6. Starts FastAPI dashboard server
"""
import threading
import time
import uvicorn


def wait_for_neo4j(retries: int = 20, delay: int = 5):
    from intelligent.neo4j_kg import build_knowledge_graph
    print("[MAIN] Waiting for Neo4j...")
    for attempt in range(retries):
        try:
            build_knowledge_graph()
            return
        except Exception:
            print(f"[MAIN] Neo4j not ready, retrying... ({attempt+1}/{retries})")
            time.sleep(delay)
    print("[MAIN] WARNING: Neo4j unavailable — skipping knowledge graph.")


def wait_for_ditto(retries: int = 12, delay: int = 10):
    from service_layer.ditto_client import provision_ditto
    print("[MAIN] Waiting for Ditto gateway...")
    for attempt in range(retries):
        try:
            provision_ditto()
            return
        except Exception:
            print(f"[MAIN] Ditto not ready, retrying... ({attempt+1}/{retries})")
            time.sleep(delay)
    print("[MAIN] WARNING: Ditto unavailable — skipping provisioning.")


def wait_for_mechanic_ditto(retries: int = 12, delay: int = 10):
    from service_layer.ditto_client import provision_mechanic_ditto
    print("[MAIN] Waiting for Mechanic Ditto gateway...")
    for attempt in range(retries):
        try:
            provision_mechanic_ditto()
            return
        except Exception:
            print(f"[MAIN] Mechanic Ditto not ready, retrying... ({attempt+1}/{retries})")
            time.sleep(delay)
    print("[MAIN] WARNING: Mechanic Ditto unavailable — skipping provisioning.")


def start_ditto_sync():
    from service_layer.ditto_sync import start_sync
    start_sync(interval=5)


def start_simulator_watcher(poll_interval: int = 5):
    """Runs forever in a daemon thread. Starts/stops the simulator's
    InfluxDB-writing loop based on whichever data source is actually
    active right now (physical.obd_reader.get_data_source()), so
    simulated telemetry is only ever written while the simulator is
    actually the selected/fallback source — never silently alongside
    a live adapter or MQTT feed.
    """
    from physical import obd_reader, simulator

    sim_thread = None

    # Make sure obd_reader has initialised at least once before we start
    # polling its mode.
    try:
        obd_reader.initialise()
    except Exception as e:
        print(f"[MAIN] obd_reader.initialise() failed: {e}")

    while True:
        try:
            # read_sensors() re-checks the live override each call, which
            # is also what keeps _data_source current; call it cheaply via
            # get_data_source() after a read_sensors() elsewhere would be
            # ideal, but since nothing else polls at this cadence, trigger
            # a check ourselves.
            obd_reader.read_sensors()
            active_source = obd_reader.get_data_source()
        except Exception as e:
            print(f"[MAIN] Simulator watcher: error checking data source: {e}")
            time.sleep(poll_interval)
            continue

        should_run_sim = active_source == "simulator"

        if should_run_sim and (sim_thread is None or not sim_thread.is_alive()):
            print("[MAIN] Data source is simulator — starting simulator write loop.")
            simulator.stop_event.clear()
            sim_thread = threading.Thread(target=simulator.run, daemon=True)
            sim_thread.start()

        elif not should_run_sim and sim_thread is not None and sim_thread.is_alive():
            print(f"[MAIN] Data source is now '{active_source}' — stopping simulator write loop.")
            simulator.stop_event.set()
            sim_thread.join(timeout=2)
            sim_thread = None

        time.sleep(poll_interval)


def main():
    print("=" * 55)
    print("  ACDT — Agentic Car Digital Twin  ")
    print("  2018 Toyota Camry                ")
    print("=" * 55)

    # 1. Provision Ditto (vehicle + mechanic twins)
    wait_for_ditto()
    wait_for_mechanic_ditto()

    # 2. Build Neo4j knowledge graph
    wait_for_neo4j()

    # 3. Start Ditto sync
    print("[MAIN] Starting Ditto sync...")
    start_ditto_sync()

    # 4. Start autonomous monitor
    print("[MAIN] Starting autonomous monitor...")
    from autonomous.monitor import start_autonomous_monitor
    start_autonomous_monitor()

    # 5. Start simulator watcher (only writes simulated telemetry while
    #    the simulator is actually the active data source)
    watcher_thread = threading.Thread(target=start_simulator_watcher, daemon=True)
    watcher_thread.start()

    # 6. Start FastAPI server
    print("[MAIN] Dashboard → http://localhost:8501\n")
    from shared.config import API_HOST, API_PORT
    uvicorn.run(
        "service_layer.agent_api:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()

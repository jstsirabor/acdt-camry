"""
main.py
────────
Starts the full ACDT stack:
  1. Provisions Eclipse Ditto (vehicle + mechanic twins)
  2. Builds Neo4j knowledge graph
  3. Starts Ditto sync loop
  4. Starts autonomous monitor (safety + maintenance)
  5. Starts OBD-II simulator
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


def start_simulator():
    time.sleep(5)
    from physical.simulator import run
    print("[MAIN] Starting simulator...")
    run()


def start_ditto_sync():
    from service_layer.ditto_sync import start_sync
    start_sync(interval=5)


def main():
    print("=" * 55)
    print("  ACDT — Agentic Car Digital Twin  ")
    print("  2018 Toyota Camry                ")
    print("=" * 55)

    # 1. Provision Ditto (vehicle + mechanic twins)
    wait_for_ditto()

    # 2. Build Neo4j knowledge graph
    wait_for_neo4j()

    # 3. Start Ditto sync
    print("[MAIN] Starting Ditto sync...")
    start_ditto_sync()

    # 4. Start autonomous monitor
    print("[MAIN] Starting autonomous monitor...")
    from autonomous.monitor import start_autonomous_monitor
    start_autonomous_monitor()

    # 5. Start simulator
    sim_thread = threading.Thread(target=start_simulator, daemon=True)
    sim_thread.start()

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

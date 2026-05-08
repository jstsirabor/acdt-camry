"""
main.py
────────
Starts the full ACDT stack:
  1. Provisions Eclipse Ditto (idempotent)
  2. Builds Neo4j knowledge graph (idempotent)
  3. Starts Ditto sync loop (background thread)
  4. Starts the FastAPI agent server
  5. Starts the OBD-II simulator (background thread)

Open the dashboard at: http://localhost:8501
"""
import threading
import time
import uvicorn

def start_simulator():
    time.sleep(3)
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

    # 1. Provision Ditto
    print("\n[MAIN] Provisioning Eclipse Ditto...")
    from service_layer.ditto_client import provision_ditto
    provision_ditto()

    # 2. Build knowledge graph
    print("[MAIN] Building Neo4j knowledge graph...")
    from intelligent.neo4j_kg import build_knowledge_graph
    build_knowledge_graph()

    # 3. Start Ditto sync
    print("[MAIN] Starting Ditto sync...")
    start_ditto_sync()

    # 4. Start simulator in background
    sim_thread = threading.Thread(target=start_simulator, daemon=True)
    sim_thread.start()

    # 5. Start API server
    print("[MAIN] Starting dashboard at http://localhost:8501\n")
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

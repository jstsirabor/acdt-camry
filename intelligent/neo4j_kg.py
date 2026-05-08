"""
intelligent/neo4j_kg.py
────────────────────────
Vehicle maintenance knowledge graph using Neo4j.
Builds the graph on first run, provides diagnosis queries.
"""
from neo4j import GraphDatabase
from shared.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_driver = None

def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver

def build_knowledge_graph():
    """Idempotent — safe to call on every startup."""
    driver = _get_driver()
    with driver.session() as s:
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Component) REQUIRE c.name IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (f:FailureMode) REQUIRE f.name IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (sym:Symptom) REQUIRE sym.name IS UNIQUE")

        nodes = """
        MERGE (engine:Component       {name:'engine',               type:'powertrain'})
        MERGE (cat:Component          {name:'catalytic_converter',   type:'emissions'})
        MERGE (o2up:Component         {name:'o2_sensor_upstream',    type:'sensor'})
        MERGE (o2down:Component       {name:'o2_sensor_downstream',  type:'sensor'})
        MERGE (coolant:Component      {name:'coolant_system',        type:'cooling'})
        MERGE (fuel:Component         {name:'fuel_system',           type:'fuel'})
        MERGE (spark:Component        {name:'spark_plugs',           type:'ignition'})
        MERGE (trans:Component        {name:'transmission',          type:'drivetrain'})
        MERGE (brakes:Component       {name:'brake_system',         type:'safety'})

        MERGE (cat_fail:FailureMode   {name:'cat_efficiency_below_threshold', severity:'high',   dtc:'P0420'})
        MERGE (overheat:FailureMode   {name:'engine_overheating',             severity:'critical',dtc:'P0217'})
        MERGE (o2_fail:FailureMode    {name:'o2_sensor_failure',              severity:'medium', dtc:'P0136'})
        MERGE (misfire:FailureMode    {name:'engine_misfire',                 severity:'high',   dtc:'P0300'})
        MERGE (lean:FailureMode       {name:'lean_fuel_mixture',              severity:'medium', dtc:'P0171'})
        MERGE (rich:FailureMode       {name:'rich_fuel_mixture',              severity:'medium', dtc:'P0172'})
        MERGE (trans_fail:FailureMode {name:'transmission_slip',              severity:'high',   dtc:'P0700'})

        MERGE (high_o2_corr:Symptom   {name:'high_o2_correlation'})
        MERGE (low_o2_diff:Symptom    {name:'low_o2_voltage_differential'})
        MERGE (high_coolant:Symptom   {name:'high_coolant_temp'})
        MERGE (high_rpm:Symptom       {name:'high_engine_rpm'})
        MERGE (high_load:Symptom      {name:'high_engine_load'})
        MERGE (lean_trim:Symptom      {name:'positive_fuel_trim'})
        MERGE (rich_trim:Symptom      {name:'negative_fuel_trim'})
        MERGE (rough_idle:Symptom     {name:'rough_idle'})
        MERGE (high_o2_corr2:Symptom  {name:'o2_sensor_no_switching'})
        """
        s.run(nodes)

        rels = """
        MATCH (cat:Component {name:'catalytic_converter'})
        MATCH (cat_fail:FailureMode {name:'cat_efficiency_below_threshold'})
        MATCH (high_o2_corr:Symptom {name:'high_o2_correlation'})
        MATCH (low_o2_diff:Symptom  {name:'low_o2_voltage_differential'})
        MERGE (cat)-[:CAN_FAIL_AS]->(cat_fail)
        MERGE (high_o2_corr)-[:INDICATES]->(cat_fail)
        MERGE (low_o2_diff)-[:INDICATES]->(cat_fail)
        SET cat_fail.action = 'replace_catalytic_converter'

        WITH cat_fail
        MATCH (overheat:FailureMode {name:'engine_overheating'})
        MATCH (coolant:Component    {name:'coolant_system'})
        MATCH (high_coolant:Symptom {name:'high_coolant_temp'})
        MERGE (coolant)-[:CAN_FAIL_AS]->(overheat)
        MERGE (high_coolant)-[:INDICATES]->(overheat)
        SET overheat.action = 'stop_vehicle_immediately_check_coolant'

        WITH overheat
        MATCH (o2_fail:FailureMode  {name:'o2_sensor_failure'})
        MATCH (o2down:Component     {name:'o2_sensor_downstream'})
        MATCH (no_switch:Symptom    {name:'o2_sensor_no_switching'})
        MERGE (o2down)-[:CAN_FAIL_AS]->(o2_fail)
        MERGE (no_switch)-[:INDICATES]->(o2_fail)
        SET o2_fail.action = 'replace_downstream_o2_sensor'

        WITH o2_fail
        MATCH (misfire:FailureMode  {name:'engine_misfire'})
        MATCH (engine:Component     {name:'engine'})
        MATCH (rough_idle:Symptom   {name:'rough_idle'})
        MATCH (high_rpm:Symptom     {name:'high_engine_rpm'})
        MERGE (engine)-[:CAN_FAIL_AS]->(misfire)
        MERGE (rough_idle)-[:INDICATES]->(misfire)
        MERGE (high_rpm)-[:INDICATES]->(misfire)
        SET misfire.action = 'inspect_spark_plugs_and_coils'

        WITH misfire
        MATCH (lean:FailureMode  {name:'lean_fuel_mixture'})
        MATCH (rich:FailureMode  {name:'rich_fuel_mixture'})
        MATCH (fuel:Component    {name:'fuel_system'})
        MATCH (lean_trim:Symptom {name:'positive_fuel_trim'})
        MATCH (rich_trim:Symptom {name:'negative_fuel_trim'})
        MERGE (fuel)-[:CAN_FAIL_AS]->(lean)
        MERGE (fuel)-[:CAN_FAIL_AS]->(rich)
        MERGE (lean_trim)-[:INDICATES]->(lean)
        MERGE (rich_trim)-[:INDICATES]->(rich)
        SET lean.action = 'inspect_maf_sensor_and_vacuum_leaks'
        SET rich.action = 'inspect_injectors_and_fuel_pressure'
        """
        s.run(rels)
    print("[NEO4J] Knowledge graph ready.")

def diagnose(symptoms: list[str]) -> list[dict]:
    driver = _get_driver()
    with driver.session() as s:
        result = s.run("""
            MATCH (sym:Symptom)-[:INDICATES]->(f:FailureMode)
            WHERE sym.name IN $symptoms
            RETURN DISTINCT f.name AS failure,
                            f.severity AS severity,
                            f.action AS action,
                            f.dtc AS dtc
            ORDER BY
              CASE f.severity
                WHEN 'critical' THEN 1
                WHEN 'high'     THEN 2
                WHEN 'medium'   THEN 3
                ELSE 4
              END
        """, symptoms=symptoms)
        return [dict(r) for r in result]

def get_components() -> list[str]:
    driver = _get_driver()
    with driver.session() as s:
        result = s.run("MATCH (c:Component) RETURN c.name AS name ORDER BY name")
        return [r["name"] for r in result]

def get_failure_modes() -> list[dict]:
    driver = _get_driver()
    with driver.session() as s:
        result = s.run("""
            MATCH (c:Component)-[:CAN_FAIL_AS]->(f:FailureMode)
            RETURN c.name AS component, f.name AS failure,
                   f.severity AS severity, f.dtc AS dtc, f.action AS action
        """)
        return [dict(r) for r in result]

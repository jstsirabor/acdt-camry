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
        MERGE (gear_slip:Symptom      {name:'rpm_flare_without_acceleration'})
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

        WITH lean, rich
        MATCH (trans_fail:FailureMode {name:'transmission_slip'})
        MATCH (trans:Component        {name:'transmission'})
        MATCH (gear_slip:Symptom      {name:'rpm_flare_without_acceleration'})
        MERGE (trans)-[:CAN_FAIL_AS]->(trans_fail)
        MERGE (gear_slip)-[:INDICATES]->(trans_fail)
        SET trans_fail.action = 'inspect_transmission_fluid_and_solenoids'
        """
        s.run(rels)

        procedures = """
        MERGE (p1:RepairProcedure {name:'replace_catalytic_converter'})
        SET p1.steps = [
            'Confirm diagnosis with a second drive cycle if possible — cat failure is expensive to get wrong.',
            'Raise the vehicle and locate the catalytic converter along the exhaust, between the exhaust manifold and the muffler.',
            'Disconnect the upstream and downstream O2 sensor connectors before removing the converter.',
            'Remove the mounting bolts or clamps at both ends of the converter.',
            'Remove the old converter and inspect the mating surfaces for damage or corrosion.',
            'Install the new converter with new gaskets, torque bolts to manufacturer spec.',
            'Reconnect both O2 sensors, ensuring connectors are fully seated.',
            'Clear the stored DTC and run a drive cycle to confirm the code does not return.'
        ]
        SET p1.estimated_time_hours = 2.5
        SET p1.tools_required = ['jack and stands', 'socket set', 'oxygen sensor socket', 'penetrating oil']

        MERGE (p2:RepairProcedure {name:'stop_vehicle_immediately_check_coolant'})
        SET p2.steps = [
            'Pull over safely and switch off the engine immediately — do not continue driving.',
            'Allow the engine to cool for at least 20 minutes before opening the coolant system.',
            'Check the coolant reservoir level once cool. Do not open a hot radiator cap.',
            'Inspect for visible leaks at hoses, the radiator, and the water pump.',
            'If low, top up with the correct coolant type and monitor temperature on restart.',
            'If the leak is not visible or temperature rises again quickly, do not continue the journey — arrange a tow.'
        ]
        SET p2.estimated_time_hours = 0.5
        SET p2.tools_required = ['coolant (correct type)', 'flashlight']

        MERGE (p3:RepairProcedure {name:'replace_downstream_o2_sensor'})
        SET p3.steps = [
            'Locate the downstream O2 sensor, positioned after the catalytic converter.',
            'Disconnect the battery negative terminal before starting electrical work.',
            'Unclip the sensor wiring harness connector.',
            'Use an oxygen sensor socket to remove the old sensor — apply penetrating oil first if seized.',
            'Apply anti-seize compound to the threads of the new sensor, avoiding the sensor tip.',
            'Install the new sensor and torque to spec, reconnect the wiring harness.',
            'Reconnect the battery, clear the DTC, and run a drive cycle to confirm.'
        ]
        SET p3.estimated_time_hours = 0.75
        SET p3.tools_required = ['oxygen sensor socket', 'anti-seize compound', 'penetrating oil']

        MERGE (p4:RepairProcedure {name:'inspect_spark_plugs_and_coils'})
        SET p4.steps = [
            'Identify which cylinder is misfiring from the diagnostic trouble code, if cylinder-specific.',
            'Remove the engine cover and locate the ignition coil for the affected cylinder.',
            'Disconnect the coil connector and remove the coil mounting bolt.',
            'Remove the spark plug using a spark plug socket.',
            'Inspect the plug for fouling, wear, or incorrect gap — compare against a known-good plug.',
            'If worn, replace with the correct plug type and gap to manufacturer spec.',
            'If the plug looks fine, the coil itself is the likely fault — swap with a known-good coil from another cylinder to confirm before replacing.',
            'Reinstall, clear the DTC, and test drive to confirm the misfire is resolved.'
        ]
        SET p4.estimated_time_hours = 1.0
        SET p4.tools_required = ['spark plug socket', 'gap gauge', 'replacement spark plug']

        MERGE (p5:RepairProcedure {name:'inspect_maf_sensor_and_vacuum_leaks'})
        SET p5.steps = [
            'Visually inspect all vacuum lines and intake boots for cracks, looseness, or disconnection.',
            'Use a smoke test or a controlled propane torch method to check for vacuum leaks if visual inspection is inconclusive.',
            'Locate the mass airflow sensor in the intake tract, before the throttle body.',
            'Remove the MAF sensor and inspect for dirt or contamination on the sensing element.',
            'Clean gently with a dedicated MAF sensor cleaner only — do not touch the sensing wire.',
            'Reinstall, clear the fuel trim DTC, and monitor short and long term fuel trim on a test drive.'
        ]
        SET p5.estimated_time_hours = 1.0
        SET p5.tools_required = ['MAF sensor cleaner', 'smoke test kit (if available)']

        MERGE (p6:RepairProcedure {name:'inspect_injectors_and_fuel_pressure'})
        SET p6.steps = [
            'Connect a fuel pressure gauge to the fuel rail test port.',
            'Compare the reading against manufacturer specification with the engine running at idle.',
            'If pressure is too high, inspect the fuel pressure regulator.',
            'If pressure is correct, check individual injector balance using an injector pulse test or noise test.',
            'Inspect for a leaking injector by checking for fuel smell or wet plugs on affected cylinders.',
            'Replace faulty injector(s) or regulator as identified, clear the DTC, and confirm fuel trims normalise on a test drive.'
        ]
        SET p6.estimated_time_hours = 1.5
        SET p6.tools_required = ['fuel pressure gauge', 'injector test light']

        MERGE (p7:RepairProcedure {name:'inspect_transmission_fluid_and_solenoids'})
        SET p7.steps = [
            'Check transmission fluid level and condition with the engine warm and running, per the dipstick procedure — look for a burnt smell or dark/discoloured fluid.',
            'If fluid is low, top up with the correct spec fluid and recheck for slipping.',
            'If fluid is burnt or discoloured, this points to internal wear — a fluid and filter change is the first step, not a guaranteed fix.',
            'Retrieve any transmission-specific DTCs beyond the generic P0700 to identify which shift solenoid is implicated.',
            'Inspect the wiring and connector for the implicated solenoid for damage or corrosion.',
            'Test solenoid resistance against manufacturer spec using a multimeter.',
            'If a solenoid is out of spec, replace it — this is usually accessible without removing the transmission pan on this platform.',
            'Clear the DTC and road test through all gear ranges to confirm the slip is resolved. If slipping persists after fluid and solenoid checks, this points to internal clutch pack wear — recommend a transmission specialist.'
        ]
        SET p7.estimated_time_hours = 2.0
        SET p7.tools_required = ['multimeter', 'OBD-II scanner (transmission-specific codes)', 'transmission fluid (correct spec)', 'drain pan']

        WITH 1 AS x
        MATCH (cat_fail:FailureMode {name:'cat_efficiency_below_threshold'})
        MATCH (p1:RepairProcedure {name:'replace_catalytic_converter'})
        MERGE (cat_fail)-[:HAS_PROCEDURE]->(p1)

        WITH 1 AS x
        MATCH (overheat:FailureMode {name:'engine_overheating'})
        MATCH (p2:RepairProcedure {name:'stop_vehicle_immediately_check_coolant'})
        MERGE (overheat)-[:HAS_PROCEDURE]->(p2)

        WITH 1 AS x
        MATCH (o2_fail:FailureMode {name:'o2_sensor_failure'})
        MATCH (p3:RepairProcedure {name:'replace_downstream_o2_sensor'})
        MERGE (o2_fail)-[:HAS_PROCEDURE]->(p3)

        WITH 1 AS x
        MATCH (misfire:FailureMode {name:'engine_misfire'})
        MATCH (p4:RepairProcedure {name:'inspect_spark_plugs_and_coils'})
        MERGE (misfire)-[:HAS_PROCEDURE]->(p4)

        WITH 1 AS x
        MATCH (lean:FailureMode {name:'lean_fuel_mixture'})
        MATCH (p5:RepairProcedure {name:'inspect_maf_sensor_and_vacuum_leaks'})
        MERGE (lean)-[:HAS_PROCEDURE]->(p5)

        WITH 1 AS x
        MATCH (rich:FailureMode {name:'rich_fuel_mixture'})
        MATCH (p6:RepairProcedure {name:'inspect_injectors_and_fuel_pressure'})
        MERGE (rich)-[:HAS_PROCEDURE]->(p6)

        WITH 1 AS x
        MATCH (trans_fail:FailureMode {name:'transmission_slip'})
        MATCH (p7:RepairProcedure {name:'inspect_transmission_fluid_and_solenoids'})
        MERGE (trans_fail)-[:HAS_PROCEDURE]->(p7)
        """
        s.run(procedures)
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

def get_repair_procedure(failure_name: str) -> dict:
    """Get the step-by-step repair procedure linked to a failure mode."""
    driver = _get_driver()
    with driver.session() as s:
        result = s.run("""
            MATCH (f:FailureMode {name: $failure_name})-[:HAS_PROCEDURE]->(p:RepairProcedure)
            RETURN p.name AS procedure_name, p.steps AS steps,
                   p.estimated_time_hours AS estimated_time_hours,
                   p.tools_required AS tools_required
        """, failure_name=failure_name)
        record = result.single()
        return dict(record) if record else {}

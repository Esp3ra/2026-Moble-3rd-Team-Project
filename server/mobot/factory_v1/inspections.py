"""Inspection-attempt storage and migration for the pre-pickup inspection flow."""


def migrate(db):
    cycle_columns = {row[1] for row in db.execute("PRAGMA table_info(V1_CYCLES)")}
    if "flow_version" not in cycle_columns:
        db.execute("ALTER TABLE V1_CYCLES ADD COLUMN flow_version TEXT NOT NULL DEFAULT '0.2.0'")
    columns = {row[1] for row in db.execute("PRAGMA table_info(V1_INSPECTIONS)")}
    legacy = bool(columns) and "attempt_number" not in columns
    if legacy:
        db.execute("ALTER TABLE V1_INSPECTIONS RENAME TO V1_INSPECTIONS_LEGACY")
    db.execute("""CREATE TABLE IF NOT EXISTS V1_INSPECTIONS (
        inspection_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, order_id TEXT NOT NULL,
        body TEXT, vision TEXT, image BLOB, archive_id TEXT UNIQUE,
        attempt_number INTEGER NOT NULL DEFAULT 1 CHECK(attempt_number >= 1),
        previous_inspection_id TEXT REFERENCES V1_INSPECTIONS(inspection_id),
        rework_command_id TEXT REFERENCES V1_COMMANDS(command_id),
        source_command_id TEXT UNIQUE REFERENCES V1_COMMANDS(command_id),
        UNIQUE(cycle_id, attempt_number)
    )""")
    if legacy:
        db.execute("""INSERT INTO V1_INSPECTIONS(inspection_id,cycle_id,order_id,body,vision,image,archive_id)
            SELECT inspection_id,cycle_id,order_id,body,vision,image,archive_id FROM V1_INSPECTIONS_LEGACY""")
        db.execute("DROP TABLE V1_INSPECTIONS_LEGACY")

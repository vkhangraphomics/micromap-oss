#!/bin/sh
# Create the two constituent databases, the composite database,
# and seed each constituent. Run once after Neo4j is healthy.
# Neo4j 5.x composite alias syntax: CREATE ALIAS `<composite>.<name>` FOR DATABASE <db>
set -e
PASS=fabricpass123

CY()    { cypher-shell -u neo4j -p "$PASS" -d system "$@"; }
CY_DB() { DB=$1; shift; cypher-shell -u neo4j -p "$PASS" -d "$DB" "$@"; }

echo "--- creating constituent databases ---"
CY "CREATE DATABASE micromap IF NOT EXISTS;"
CY "CREATE DATABASE primekg  IF NOT EXISTS;"

echo "--- waiting for databases to come online ---"
for db in micromap primekg; do
  for i in $(seq 1 20); do
    STATUS=$(CY "SHOW DATABASE $db YIELD currentStatus RETURN currentStatus;" 2>/dev/null \
             | grep -v "currentStatus" | tr -d ' "')
    [ "$STATUS" = "online" ] && echo "$db: online" && break
    sleep 2
  done
done

echo "--- creating composite database: graphomics ---"
CY "CREATE COMPOSITE DATABASE graphomics IF NOT EXISTS;"

echo "--- adding constituent aliases (Neo4j 5.x syntax) ---"
# CREATE ALIAS `<composite>`.`<alias>` FOR DATABASE <db>
# Quote the composite and alias as TWO identifiers, not one quoted dotted
# string: `graphomics.micromap` (one quoted name) creates a STANDALONE alias
# (composite association = NULL), never a constituent, so `USE
# graphomics.micromap` fails with "Graph not found". `graphomics`.`micromap`
# keeps the dot as the namespace separator and registers a real constituent.
# No `|| true` — fail fast so seeding doesn't run against an unregistered composite.
cypher-shell -u neo4j -p "$PASS" -d system \
  "CREATE ALIAS \`graphomics\`.\`micromap\` IF NOT EXISTS FOR DATABASE micromap;"
cypher-shell -u neo4j -p "$PASS" -d system \
  "CREATE ALIAS \`graphomics\`.\`primekg\` IF NOT EXISTS FOR DATABASE primekg;"

echo "--- verifying aliases registered before seeding ---"
ALIASES=$(CY "SHOW DATABASES YIELD name, constituents WHERE name='graphomics' RETURN constituents;" 2>/dev/null)
echo "$ALIASES" | grep -q "graphomics.micromap" || { echo "ERROR: graphomics.micromap alias missing — aborting"; exit 1; }
echo "$ALIASES" | grep -q "graphomics.primekg"  || { echo "ERROR: graphomics.primekg alias missing — aborting";  exit 1; }
echo "aliases verified ✓"

echo "--- seeding micromap ---"
cypher-shell -u neo4j -p "$PASS" -d micromap --file /seed/01-micromap.cypher

echo "--- seeding primekg ---"
cypher-shell -u neo4j -p "$PASS" -d primekg --file /seed/02-primekg.cypher

echo "--- verify composite sees both constituents ---"
CY "SHOW DATABASE graphomics YIELD name, currentStatus, constituents \
    RETURN name, currentStatus, constituents;"

echo "=== setup complete ==="
